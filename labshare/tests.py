import datetime
import io
import json
import os
import random
import string
import time
import unittest.mock as mock
from datetime import timedelta
from unittest import skipIf
from unittest.mock import Mock
from urllib.error import URLError

from channels.layers import get_channel_layer
from channels.testing import ChannelsLiveServerTestCase
from django import template
from django.contrib.auth.models import User, Group
from django.core import mail
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django_webtest import WebTest
from guardian.shortcuts import assign_perm
from guardian.utils import get_anonymous_user
from model_bakery import baker
from model_bakery.recipe import Recipe
from rest_framework import status
from rest_framework.test import APITestCase
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from labshare.consumers import GPUInfoUpdater
from labshare.models import Device, GPU, Reservation, GPUProcess, EmailAddress
from labshare.templatetags.icon import icon
from labshare.utils import get_devices, determine_failed_gpus, publish_gpu_states, \
    publish_device_state, check_reservations

device_recipe = Recipe(
    Device,
    name=lambda: ''.join(
        random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(16))
)


def utc_now():
    return datetime.datetime.now(tz=datetime.timezone.utc)


def make_reservation_in_the_past(user, gpu, distance):
    with mock.patch("django.utils.timezone.now") as mock_now:
        mock_now.return_value = utc_now() - distance
        reservation = baker.make(Reservation, gpu=gpu, user=user)
        reservation.start_usage()
    return reservation


class LabshareTestSetup(WebTest):
    csrf_checks = False

    @classmethod
    def setUpTestData(cls):
        cls.user = baker.make(User, email="user@example.com")
        cls.devices = device_recipe.make(_quantity=3)
        baker.make(GPU, device=cls.devices[0], _quantity=2, used_memory="12 Mib", total_memory="112 Mib")
        cls.gpu = baker.make(GPU, device=cls.devices[1], used_memory="12 Mib", total_memory="112 Mib")
        baker.make(GPU, device=cls.devices[-1], used_memory="12 Mib", total_memory="112 Mib")

        cls.group = baker.make(Group)
        cls.user.groups.add(cls.group)

        for device in cls.devices:
            assign_perm('use_device', cls.group, device)


class TestLabshare(LabshareTestSetup):

    def test_index(self):
        response = self.app.get(reverse("index"), user=self.user)
        self.assertEqual(response.status_code, 200)

        for device in self.devices:
            self.assertIn(device.name, response.body.decode('utf-8'))

    def test_reserve_no_user(self):
        response = self.app.get(reverse("reserve"), expect_errors=True)
        self.assertEqual(response.status_code, 302)
        response = self.app.post(reverse("reserve"), expect_errors=True)
        self.assertEqual(response.status_code, 302)

    def test_reserve_get_user(self):
        response = self.app.get(reverse("reserve"), user=self.user)
        self.assertEqual(response.status_code, 200)

    def test_reserve_submit_form(self):
        response = self.app.get(reverse("reserve"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form["device"].force_value(self.devices[0].name)
        form["gpu"].force_value(self.devices[0].gpus.first().uuid)
        form["next-available-spot"] = "false"

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 1)
        reservation = Reservation.objects.first()
        self.assertFalse(reservation.user_reserved_next_available_spot)

    def test_reserve_next_available_gpu_no_reserved_gpu(self):
        response = self.app.get(reverse("reserve"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form["device"].force_value(self.devices[0].name)
        form["gpu"].force_value(self.devices[0].gpus.first().uuid)
        form["next-available-spot"] = "true"

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_reserve_next_available_gpu_one_reserved_gpu(self):
        baker.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)

        response = self.app.get(reverse("reserve"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form["device"].force_value(self.devices[0].name)
        form["gpu"].force_value(self.devices[0].gpus.first().uuid)
        form["next-available-spot"] = "true"

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 2)
        for gpu in self.devices[0].gpus.all():
            self.assertEqual(gpu.reservations.count(), 1)

    def test_reserve_next_available_gpu_two_reserved_gpus(self):
        for gpu in self.devices[0].gpus.all():
            baker.make(Reservation, gpu=gpu, user=self.user)

        response = self.app.get(reverse("reserve"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form["device"].force_value(self.devices[0].name)
        form["gpu"].force_value(self.devices[0].gpus.first().uuid)
        form["next-available-spot"] = "true"

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 4)
        self.assertEqual(Reservation.objects.filter(user_reserved_next_available_spot=True).count(), 2)
        for gpu in self.devices[0].gpus.all():
            self.assertEqual(gpu.reservations.count(), 2)

    def test_reserve_next_available_spot_one_gpu_no_reservation(self):
        response = self.app.get(reverse("reserve"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form["device"].force_value(self.devices[1].name)
        form["gpu"].force_value(self.devices[1].gpus.first().uuid)
        form["next-available-spot"] = "true"

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_reserve_next_available_spot_one_gpu_reservation(self):
        baker.make(Reservation, gpu=self.devices[1].gpus.first(), user=self.user)

        response = self.app.get(reverse("reserve"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form["device"].force_value(self.devices[1].name)
        form["gpu"].force_value(self.devices[1].gpus.first().uuid)
        form["next-available-spot"] = "true"

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(Reservation.objects.filter(user_reserved_next_available_spot=True).count(), 1)

        # the user currently holding a reservation should receive an email
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("user@example.com", mail.outbox[0].to)

    def test_get_devices(self):
        device_info = get_devices()
        for device, device_info in zip(Device.objects.all(), device_info):
            self.assertEqual((device.name, device.name), device_info)

    def test_get_gpu_info_no_user(self):
        response = self.app.get(
            "{url}?device_name={device_name}".format(url=reverse("gpus_for_device"), device_name=self.devices[0].name),
            expect_errors=True
        )
        self.assertEqual(response.status_code, 401)

    def test_get_gpu_info_no_ajax(self):
        response = self.app.get(
            "{url}?device_name={device_name}".format(url=reverse("gpus_for_device"), device_name=self.devices[0].name),
            expect_errors=True,
            user=self.user
        )
        self.assertEqual(response.status_code, 400)

    def test_get_gpu_wrong_method(self):
        response = self.app.post(
            "{url}?device_name={device_name}".format(url=reverse("gpus_for_device"), device_name=self.devices[0].name),
            expect_errors=True,
            user=self.user,
            xhr=True,
        )
        self.assertEqual(response.status_code, 400)

    def test_get_gpu_for_all_devices(self):
        for device in self.devices:
            response = self.app.get(
                "{url}?device_name={device_name}".format(url=reverse("gpus_for_device"), device_name=device.name),
                user=self.user,
                xhr=True,
            )
            self.assertEqual(response.status_code, 200)
            for gpu in device.gpus.all():
                self.assertIn(gpu.uuid, response.body.decode('utf-8'))
                self.assertIn(gpu.model_name, response.body.decode('utf-8'))

    def test_get_gpu_wrong_query_parameter(self):
        response = self.app.get(
            "{url}?bad={device_name}".format(url=reverse("gpus_for_device"), device_name=self.devices[0].name),
            user=self.user,
            expect_errors=True,
            xhr=True,
        )
        self.assertEqual(response.status_code, 404)

    def test_get_gpu_info_bad_requests(self):
        response = self.app.get(
            "{url}?uuid={uuid}".format(url=reverse("gpu_info"), uuid=self.devices[0].gpus.first().uuid),
            expect_errors=True
        )
        self.assertEqual(response.status_code, 401)

        response = self.app.get(
            "{url}?uuid={uuid}".format(url=reverse("gpu_info"), uuid=self.devices[0].gpus.first().uuid),
            expect_errors=True,
            xhr=True,
        )
        self.assertEqual(response.status_code, 401)

        response = self.app.get(
            "{url}?uuid={uuid}".format(url=reverse("gpu_info"), uuid=self.devices[0].gpus.first().uuid),
            expect_errors=True,
            user=self.user,
        )
        self.assertEqual(response.status_code, 400)

        response = self.app.post(
            "{url}?uuid={uuid}".format(url=reverse("gpu_info"), uuid=self.devices[0].gpus.first().uuid),
            expect_errors=True,
            user=self.user,
            xhr=True,
        )
        self.assertEqual(response.status_code, 400)

    def test_get_gpu_info_wrong_query_param(self):
        response = self.app.get(
            "{url}?bad={uuid}".format(url=reverse("gpu_info"), uuid=self.devices[0].gpus.first().uuid),
            expect_errors=True,
            user=self.user,
            xhr=True,
        )
        self.assertEqual(response.status_code, 404)

    def test_get_gpu_info_no_reservation(self):
        gpu = self.devices[0].gpus.first()
        response = self.app.get(
            "{url}?uuid={uuid}".format(url=reverse("gpu_info"), uuid=gpu.uuid),
            expect_errors=True,
            user=self.user,
            xhr=True,
        )
        self.assertEqual(response.status_code, 200)

        self.assertIn(gpu.used_memory, response.body.decode('utf-8'))
        self.assertIn(gpu.total_memory, response.body.decode('utf-8'))
        self.assertIn("No current user", response.body.decode('utf-8'))

    def test_get_gpu_info_with_reservation(self):
        gpu = self.devices[0].gpus.first()
        baker.make(Reservation, gpu=gpu, user=self.user)
        response = self.app.get(
            "{url}?uuid={uuid}".format(url=reverse("gpu_info"), uuid=gpu.uuid),
            expect_errors=True,
            user=self.user,
            xhr=True,
        )
        self.assertEqual(response.status_code, 200)

        self.assertIn(gpu.used_memory, response.body.decode('utf-8'))
        self.assertIn(gpu.total_memory, response.body.decode('utf-8'))
        self.assertIn(self.user.username, response.body.decode('utf-8'))

    def test_gpu_done_no_user(self):
        response = self.app.get(reverse("done_with_gpu", args=[self.devices[0].gpus.first().id]))
        self.assertEqual(response.status_code, 302)

    def test_gpu_done_wrong_gpu_id(self):
        response = self.app.get(reverse("done_with_gpu", args=[17]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 404)

    def test_gpu_done_no_reservation(self):
        gpu = self.devices[0].gpus.first()
        response = self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 404)

    def test_gpu_done_reservation_wrong_user(self):
        gpu = self.devices[0].gpus.first()
        user = baker.make(User)
        user.groups.add(self.group)
        baker.make(Reservation, gpu=gpu, user=user)

        response = self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 403)

    def test_gpu_done_only_one_reservation(self):
        gpu = self.devices[0].gpus.first()
        baker.make(Reservation, gpu=gpu, user=self.user)

        response = self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_gpu_done_one_more_reservation(self):
        gpu = self.devices[0].gpus.first()

        baker.make(Reservation, gpu=gpu, user=self.user, user_reserved_next_available_spot=False)

        waiting_user = baker.make(User, email="waiting_user@example.com")
        waiting_user.groups.add(self.group)
        baker.make(Reservation, gpu=gpu, user=waiting_user)

        response = self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 1)

        # the waiting user should receive an email
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(waiting_user.email, mail.outbox[0].to)

    def test_gpu_done_next_available_spot_reserved(self):
        user = baker.make(User)
        user.groups.add(self.group)
        gpus = self.devices[0].gpus
        baker.make(Reservation, gpu=gpus.first(), user=self.user)
        baker.make(Reservation, gpu=gpus.last(), user=self.user)
        baker.make(Reservation, gpu=gpus.first(), user=user, user_reserved_next_available_spot=True)
        baker.make(Reservation, gpu=gpus.last(), user=user, user_reserved_next_available_spot=True)
        self.assertEqual(Reservation.objects.count(), 4)

        response = self.app.post(reverse("done_with_gpu", args=[gpus.first().id]), user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(gpus.first().reservations.count(), 1)
        self.assertEqual(gpus.first().reservations.first().user, user)
        self.assertEqual(gpus.last().reservations.count(), 1)
        self.assertEqual(gpus.last().reservations.first().user, self.user)

    def test_gpu_done_next_available_spot_reserved_additional_reservation(self):
        user = baker.make(User)
        user.groups.add(self.group)
        gpus = self.devices[0].gpus
        baker.make(Reservation, gpu=gpus.last(), user=self.user)
        baker.make(Reservation, gpu=gpus.first(), user=self.user)
        baker.make(Reservation, gpu=gpus.first(), user=user, user_reserved_next_available_spot=True)
        baker.make(Reservation, gpu=gpus.last(), user=user, user_reserved_next_available_spot=True)
        baker.make(Reservation, gpu=gpus.last(), user=self.user)
        self.assertEqual(Reservation.objects.count(), 5)

        response = self.app.post(reverse("done_with_gpu", args=[gpus.first().id]), user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 3)
        self.assertEqual(gpus.first().reservations.count(), 1)
        self.assertEqual(gpus.first().reservations.first().user, user)
        self.assertEqual(gpus.last().reservations.count(), 2)
        self.assertEqual(gpus.last().reservations.first().user, self.user)
        self.assertEqual(gpus.last().reservations.last().user, self.user)

    def test_cancel_gpu_no_user(self):
        response = self.app.post(reverse("cancel_gpu", args=[self.devices[0].gpus.first().id]))
        self.assertEqual(response.status_code, 302)

    def test_cancel_gpu_wrong_gpu_id(self):
        response = self.app.post(reverse("cancel_gpu", args=[17]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 404)

    def test_cancel_gpu_no_reservation(self):
        gpu = self.devices[0].gpus.first()
        response = self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 404)

    def test_cancel_gpu_multiple_reservation(self):
        gpu = self.devices[0].gpus.first()
        other = baker.make(User)
        other.groups.add(self.group)
        baker.make(Reservation, gpu=gpu, user=self.user)
        baker.make(Reservation, gpu=gpu, user=other)
        baker.make(Reservation, gpu=gpu, user=self.user)

        self.assertEqual(gpu.last_reservation().user, self.user)
        self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(gpu.last_reservation().user, other)
        self.assertEqual(gpu.current_reservation().user, self.user)
        self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=other)
        self.assertEqual(Reservation.objects.count(), 1)
        self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(gpu.last_reservation(), None)

    def test_cancel_gpu_reservation_wrong_user(self):
        gpu = self.devices[0].gpus.first()
        users = baker.make(User, _quantity=2)
        for user in users:
            user.groups.add(self.group)
        baker.make(Reservation, gpu=gpu, user=users[0])
        baker.make(Reservation, gpu=gpu, user=users[1])

        response = self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 404)

    def test_cancel_gpu(self):
        gpu = self.devices[0].gpus.first()
        other = baker.make(User)
        other.groups.add(self.group)
        baker.make(Reservation, gpu=gpu, user=other)
        baker.make(Reservation, gpu=gpu, user=self.user)

        self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(gpu.current_reservation().user, other)

    def test_gpu_updated_too_long_ago(self):
        for gpu in GPU.objects.all():
            last_updated = gpu.last_updated
            self.assertFalse(gpu.last_update_too_long_ago())
            gpu.last_updated = last_updated - timedelta(minutes=29)
            self.assertFalse(gpu.last_update_too_long_ago())
            gpu.last_updated = last_updated - timedelta(minutes=30)
            self.assertTrue(gpu.last_update_too_long_ago())

    def test_template_tag_icon_missing_icon_name(self):
        parser = Mock()
        attrs = {'split.return_value': ['icon']}
        token = Mock(contents=Mock(**attrs))

        self.assertRaises(template.TemplateSyntaxError, icon, parser, token)

    def test_template_tag_icon_name_with_quote(self):
        parser = Mock()
        icon_name = 'wrench'
        attrs = {'split.return_value': ['icon', '"{}"'.format(icon_name)]}
        token = Mock(contents=Mock(**attrs))

        node = icon(parser, token)
        self.assertIn(icon_name, node.render(Mock()))

    def test_template_tag_icon_too_many_arguments(self):
        parser = Mock()
        attrs = {'split.return_value': ['icon', 'icon-name', 'unexpected_string']}
        token = Mock(contents=Mock(**attrs))

        self.assertRaises(template.TemplateSyntaxError, icon, parser, token)

    def test_device_str_representation(self):
        device = baker.prepare(Device)
        self.assertEqual(str(device), device.name)

    def test_gpu_str_representation(self):
        gpu = baker.prepare(GPU, device=self.devices[0])
        self.assertEqual(str(gpu), gpu.model_name)

    def test_reservation_str_representation(self):
        reservation = baker.prepare(Reservation, gpu=self.gpu, user=self.user)
        self.assertEqual(str(reservation), "{gpu} on {device}, {user}".format(
            gpu=reservation.gpu,
            device=reservation.gpu.device,
            user=reservation.user
        ))


class TestReservationExpiration(LabshareTestSetup):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_user = baker.make(User, email="otheruser@example.com")
        cls.other_user.groups.add(cls.group)

    def assertTimeAlmostEqual(self, a, b):
        self.assertLess(abs(a - b), timedelta(seconds=1))

    def make_and_check_new_reservation(self, user, device_num=0):
        previous_num_reservations = Reservation.objects.count()

        response = self.app.get(reverse("reserve"), user=user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form["device"].force_value(self.devices[device_num].name)
        form["gpu"].force_value(self.devices[device_num].gpus.first().uuid)
        form["next-available-spot"] = "false"

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), previous_num_reservations + 1)

    def test_new_reservation(self):
        reservation = baker.make(Reservation, gpu=self.gpu, user=self.user)
        self.assertIsNone(reservation.usage_started)
        self.assertIsNone(reservation.usage_expires)
        self.assertFalse(reservation.is_usage_expired())

        reservation.start_usage()
        self.assertTimeAlmostEqual(reservation.usage_started, utc_now())
        self.assertTimeAlmostEqual(reservation.usage_expires, utc_now() + Reservation.usage_period())

    def test_expiring_reservation(self):
        back_to_the_future = Reservation.usage_period() - (Reservation.reminder_period() + timedelta(minutes=1))
        reservation = make_reservation_in_the_past(self.user, self.gpu, back_to_the_future)

        self.assertFalse(reservation.is_usage_expired())
        self.assertFalse(reservation.needs_reminder())

        with mock.patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = utc_now() + timedelta(minutes=2)
            self.assertFalse(reservation.is_usage_expired())
            self.assertTrue(reservation.needs_reminder())

        with mock.patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = utc_now() + timedelta(hours=49)
            self.assertTrue(reservation.is_usage_expired())
            self.assertTrue(reservation.needs_reminder())

    def test_extending_reservation(self):
        reservation = make_reservation_in_the_past(self.user, self.gpu,
                                                   Reservation.usage_period() - Reservation.reminder_period())

        self.assertTrue(reservation.needs_reminder())
        reservation.set_reminder_sent()
        self.assertTrue(reservation.extension_reminder_sent)

        reservation.extend()
        self.assertFalse(reservation.needs_reminder())
        self.assertFalse(reservation.extension_reminder_sent)
        self.assertTimeAlmostEqual(reservation.usage_expires, utc_now() + Reservation.usage_period())

    def test_extend_gpu(self):
        make_reservation_in_the_past(self.user, self.gpu,
                                     Reservation.usage_period() - Reservation.reminder_period())

        self.app.post(reverse("extend_gpu", args=[self.gpu.id]), user=self.user)
        self.assertEqual(Reservation.objects.count(), 1)
        reservation = Reservation.objects.first()
        self.assertTimeAlmostEqual(reservation.usage_expires, utc_now() + Reservation.usage_period())

    def test_extend_not_allowed_too_early(self):
        reservation = make_reservation_in_the_past(self.user, self.gpu,
                                                   Reservation.usage_period() - Reservation.reminder_period() * 2)
        previous_expiry = reservation.usage_expires

        response = self.app.post(reverse("extend_gpu", args=[self.gpu.id]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 400)

        self.assertEqual(Reservation.objects.count(), 1)
        reservation = Reservation.objects.first()
        self.assertTimeAlmostEqual(reservation.usage_expires, previous_expiry)

    def test_usage_starting(self):
        self.make_and_check_new_reservation(self.user)

        first_reservation = Reservation.objects.first()
        self.assertTimeAlmostEqual(first_reservation.time_reserved, first_reservation.usage_started)
        self.assertTimeAlmostEqual(abs(first_reservation.usage_expires - first_reservation.usage_started),
                                   Reservation.usage_period())
        self.assertFalse(first_reservation.extension_reminder_sent)

        self.make_and_check_new_reservation(self.other_user)

        second_reservation = Reservation.objects.last()
        self.assertEqual(None, second_reservation.usage_started)
        self.assertEqual(None, second_reservation.usage_expires)
        self.assertFalse(second_reservation.extension_reminder_sent)

    def test_reservation_checking_updating(self):
        reservation = make_reservation_in_the_past(self.user, self.gpu, Reservation.usage_period())
        check_reservations()
        self.assertNotIn(reservation, Reservation.objects.all())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to[0], "user@example.com")

    def test_reservation_checking_reminding(self):
        reservation = make_reservation_in_the_past(self.user, self.gpu,
                                                   Reservation.usage_period() - Reservation.reminder_period())
        check_reservations()
        self.assertEqual(reservation, Reservation.objects.first())
        reservation = Reservation.objects.first()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to[0], "user@example.com")
        self.assertTrue(reservation.extension_reminder_sent)

    def test_reservation_checking_no_double_reminding(self):
        reservation = make_reservation_in_the_past(self.user, self.gpu,
                                                   Reservation.usage_period() - Reservation.reminder_period())
        reservation.set_reminder_sent()
        check_reservations()
        self.assertEqual(reservation, Reservation.objects.first())
        reservation = Reservation.objects.first()

        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(reservation.extension_reminder_sent)

    def test_reservation_checking_nothing(self):
        reservation = make_reservation_in_the_past(self.user, self.gpu, timedelta(days=2))
        check_reservations()
        self.assertEqual(reservation, Reservation.objects.first())
        reservation = Reservation.objects.first()

        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(reservation.extension_reminder_sent)


class TestMessages(WebTest):
    csrf_checks = False

    def setUp(self):
        self.user = baker.make(User, is_superuser=True, is_staff=True, email="test@example.com")
        self.devices = device_recipe.make(_quantity=3)
        baker.make(GPU, device=self.devices[0], _quantity=2, used_memory="12 Mib", total_memory="112 Mib")
        baker.make(GPU, device=self.devices[1], used_memory="12 Mib", total_memory="112 Mib")
        baker.make(GPU, device=self.devices[-1], used_memory="12 Mib", total_memory="112 Mib")

        self.group = baker.make(Group)
        for device in self.devices:
            assign_perm('use_device', self.group, device)
        self.user.groups.add(self.group)

    def test_view_message_site_no_user(self):
        response = self.app.get(reverse("send_message"), expect_errors=True)
        self.assertEqual(response.status_code, 302)

    def test_view_message_site_normal_user(self):
        user = baker.make(User)
        user.groups.add(self.group)
        response = self.app.get(reverse("send_message"), user=user)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Message all users", response.body.decode('utf-8'))

    def test_view_message_site_superuser(self):
        response = self.app.get(reverse("send_message"), user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Message all users", response.body.decode('utf-8'))

    def test_send_message_to_all_users(self):
        response = self.app.get(reverse("send_message"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form['message_all_users'] = True
        form['subject'] = 'subject'
        form['message'] = 'message'

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("index"))

        num_email_addresses = User.objects.count()
        num_email_addresses += EmailAddress.objects.count()
        self.assertEqual(len(mail.outbox[0].bcc), num_email_addresses - 1)

    def test_send_message_to_specific_user(self):
        response = self.app.get(reverse("send_message"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form['recipients'] = '1'
        form['subject'] = 'subject'
        form['message'] = 'message'

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("index"))

        # check that the right amount of mails with the right settings is sent
        sent_mail = mail.outbox[0]
        to_address = sent_mail.to
        self.assertEqual(to_address[0], User.objects.get(id=1).email)
        self.assertEqual(len(to_address), 1)

        from_address = sent_mail.from_email
        self.assertEqual(from_address, self.user.email)

        cc_address = sent_mail.cc
        self.assertEqual(len(cc_address), 1)
        self.assertEqual(cc_address[0], self.user.email)

        self.assertEqual(len(sent_mail.bcc), 0)

    def test_send_message_to_multiple_users(self):
        response = self.app.get(reverse("send_message"), user=self.user)
        self.assertEqual(response.status_code, 200)
        _ = baker.make(User)

        form = response.form
        form['recipients'] = ['1', '2']
        form['subject'] = 'subject'
        form['message'] = 'message'

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("index"))

        # check that the right amount of mails with the right settings is sent
        sent_mail = mail.outbox[0]
        to_address = sent_mail.to
        self.assertEqual(len(to_address), 2)
        self.assertEqual(to_address[0], User.objects.get(id=1).email)
        self.assertEqual(to_address[1], User.objects.get(id=2).email)

        from_address = sent_mail.from_email
        self.assertEqual(from_address, self.user.email)

        cc_address = sent_mail.cc
        self.assertEqual(len(cc_address), 1)
        self.assertEqual(cc_address[0], self.user.email)

        self.assertEqual(len(sent_mail.bcc), 0)

    def test_send_message_to_all_not_permitted(self):
        user = baker.make(User)
        user.groups.add(self.group)
        response = self.app.get(reverse("send_message"), user=user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        self.assertNotIn('message_all_users', form.fields)

        data = {
            'message_all_users': True,
            'subject': 'subject',
            'message': 'message',
        }

        response = self.app.post(reverse("send_message"), params=data, user=user, expect_errors=True)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_message_no_recipient_selected(self):
        response = self.app.get(reverse("send_message"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form['subject'] = 'subject'
        form['message'] = 'message'

        response = form.submit()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Please select at least one recipient", response.body.decode('utf-8'))
        self.assertEqual(len(mail.outbox), 0)

    def test_send_message_multiple_email_addresses(self):
        addresses = [address.email for address in baker.make(EmailAddress, _quantity=3, user=self.user)]
        addresses.append(self.user.email)
        response = self.app.get(reverse("send_message"), user=self.user)
        self.assertEqual(response.status_code, 200)

        form = response.form
        form['recipients'] = '2'
        form['subject'] = 'subject'
        form['message'] = 'message'

        response = form.submit()
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("index"))
        self.assertEqual(len(mail.outbox), 1)

        to_addresses = mail.outbox[0].to
        for address in addresses:
            self.assertIn(address, to_addresses)

        cc_addresses = mail.outbox[0].cc
        for address in addresses:
            self.assertIn(address, cc_addresses)


class GPUProcessTests(WebTest):
    csrf_checks = False

    def setUp(self):
        self.user = baker.make(User, is_superuser=True)

        devices = device_recipe.make(_quantity=3)
        for idx, device in enumerate(devices):
            gpus = baker.make(GPU, _quantity=2, device=device)
            for gpu in gpus:
                if idx == 1:
                    baker.make(GPUProcess, gpu=gpu, _quantity=2)
                elif idx == 2:
                    baker.make(GPUProcess, gpu=gpu)

    def test_gpu_process_string(self):
        for process in GPUProcess.objects.all():
            process_info = "{process} (by {username}) running on {gpu} (using {memory})".format(
                process=process.name,
                username=process.username,
                gpu=process.gpu,
                memory=process.memory_usage,
            )
            self.assertEqual(str(process), process_info)


class LabSharePermissionTests(WebTest):
    csrf_checks = False

    def setUp(self):
        self.staff_user = baker.make(User)
        self.user = baker.make(User)
        self.devices = device_recipe.make(_quantity=3)
        baker.make(GPU, device=self.devices[0], _quantity=2, used_memory="12 Mib", total_memory="112 Mib")
        baker.make(GPU, device=self.devices[1], used_memory="12 Mib", total_memory="112 Mib")
        baker.make(GPU, device=self.devices[-1], used_memory="12 Mib", total_memory="112 Mib")

        self.student_group = baker.make(Group, name="students")
        self.user.groups.add(self.student_group)

        for device in self.devices[:-1]:
            assign_perm('use_device', self.student_group, device)

        self.staff_group = baker.make(Group, name="staff")
        self.staff_user.groups.add(self.staff_group)

        for device in self.devices:
            assign_perm('use_device', self.staff_group, device)

    def test_overview_correct(self):
        response = self.app.get(reverse('index'), user=self.user)
        response_text = response.body.decode('utf-8')
        for device in self.devices[:-1]:
            self.assertIn(device.name, response_text)
        self.assertNotIn(self.devices[-1].name, response_text)

        response = self.app.get(reverse("index"), user=self.staff_user)
        for device in self.devices:
            self.assertIn(device.name, response.body.decode('utf-8'))

        response = self.app.get(reverse("index"), user=get_anonymous_user())
        for device in self.devices:
            self.assertNotIn(device.name, response.body.decode('utf-8'))

    def test_reserve_correct_devices(self):
        response = self.app.get(reverse('reserve'), user=self.user)
        response_text = response.body.decode('utf-8')
        for device in self.devices[:-1]:
            self.assertIn(device.name, response_text)
        self.assertNotIn(self.devices[-1].name, response_text)

        response = self.app.get(reverse('reserve'), user=self.staff_user)
        for device in self.devices:
            self.assertIn(device.name, response.body.decode('utf-8'))

        response = self.app.get(reverse("reserve"), user=get_anonymous_user())
        for device in self.devices:
            self.assertNotIn(device.name, response.body.decode('utf-8'))

    def test_reserve_next_available_spot(self):
        response = self.app.get(reverse('reserve'), user=self.user)

        form = response.form
        form['device'] = self.devices[0].name
        form['gpu'].force_value([self.devices[0].gpus.first().uuid])
        form['next-available-spot'] = "true"

        response = form.submit(user=self.user)
        self.assertNotIn("Select a valid choice.", response.body.decode('utf-8'))

        form['device'].force_value([self.devices[-1].name])
        form['gpu'].force_value([self.devices[-1].gpus.first().uuid])
        form['next-available-spot'] = "true"
        response = form.submit(user=self.user)
        self.assertIn("Select a valid choice.", response.body.decode('utf-8'))

    def test_reserve_given_gpu(self):
        response = self.app.get(reverse('reserve'), user=self.user)

        form = response.form
        form['device'].force_value([self.devices[0].name])
        form['gpu'].force_value([self.devices[0].gpus.first().uuid])

        response = form.submit()
        self.assertNotIn("Select a valid choice.", response.body.decode('utf-8'))

        form['gpu'].force_value([self.devices[-1].gpus.first().uuid])
        response = form.submit(expect_errors=True)
        self.assertEqual(response.status_code, 403)

    def test_reserve_non_existent_device_and_gpu(self):
        response = self.app.get(reverse('reserve'), user=self.user)

        form = response.form
        form['device'].force_value(['undefined'])
        form['gpu'].force_value(['undefined'])

        response = form.submit(expect_errors=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Select a valid choice', response.body.decode('utf-8'))

        form['device'].force_value([self.devices[0].name])
        response = form.submit(expect_errors=True)
        self.assertEqual(response.status_code, 404)

    def test_gpu_listing(self):
        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[0].name),
                                user=self.user, xhr=True)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[-1].name),
                                user=self.user, expect_errors=True, xhr=True)
        self.assertEqual(response.status_code, 403)

        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[-1].name),
                                user=self.staff_user, xhr=True)
        self.assertEqual(response.status_code, 200)

    def test_gpu_listing_non_existing_gpu_name(self):
        names_to_test = ['undefined', 'a_name_that_will_never_exist_at_least_we_hope_so']
        for name in names_to_test:
            response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(name), user=self.user,
                                    xhr=True, expect_errors=True)
            self.assertEqual(response.status_code, 404)

    def test_gpu_info(self):
        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[0].gpus.first().uuid),
                                user=self.user, xhr=True)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[-1].gpus.first().uuid),
                                user=self.user, expect_errors=True, xhr=True)
        self.assertEqual(response.status_code, 403)

        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[-1].gpus.first().uuid),
                                user=self.staff_user, xhr=True)
        self.assertEqual(response.status_code, 200)

    def test_gpu_info_non_existent_gpu(self):
        ids_to_test = ['undefined', 'a_name_that_will_never_exist_at_least_we_hope_so']
        for id in ids_to_test:
            response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(id), user=self.user, xhr=True,
                                    expect_errors=True)
            self.assertEqual(response.status_code, 404)

    def test_gpu_done(self):
        baker.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)
        response = self.app.post(reverse("done_with_gpu", args=[self.devices[0].gpus.first().id]), user=self.user)
        self.assertEqual(response.status_code, 200)

        baker.make(Reservation, gpu=self.devices[-1].gpus.first(), user=self.staff_user)
        response = self.app.post(
            reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]),
            user=self.user,
            expect_errors=True
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Reservation.objects.count(), 1)

        response = self.app.post(reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]),
                                 user=self.staff_user)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(
            reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]),
            user=self.user,
            expect_errors=True
        )
        self.assertEqual(response.status_code, 400)

    def test_gpu_cancel(self):
        current_user = baker.make(User)
        current_user.groups.add(self.student_group)

        baker.make(Reservation, gpu=self.devices[0].gpus.first(), user=current_user)
        baker.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)

        # the user who holds the current reservation is not allowed to cancel (only mark as done)
        response = self.app.post(reverse("cancel_gpu", args=[self.devices[0].gpus.first().id]), user=current_user,
                                 expect_errors=True)
        self.assertEqual(response.status_code, 400)

        # other users later in the queue can cancel
        response = self.app.post(reverse("cancel_gpu", args=[self.devices[0].gpus.first().id]), user=self.user)
        self.assertEqual(response.status_code, 200)

        baker.make(Reservation, gpu=self.devices[-1].gpus.first(), user=current_user)
        baker.make(Reservation, gpu=self.devices[-1].gpus.first(), user=self.staff_user)
        response = self.app.post(reverse("cancel_gpu", args=[self.devices[-1].gpus.first().id]), user=self.user,
                                 expect_errors=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Reservation.objects.count(), 3)

        response = self.app.post(reverse("cancel_gpu", args=[self.devices[-1].gpus.first().id]), user=self.staff_user)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(
            reverse("cancel_gpu", args=[self.devices[-1].gpus.first().id]),
            user=self.staff_user,
            expect_errors=True
        )
        self.assertEqual(response.status_code, 400)


class EmailAddressTests(WebTest):

    def setUp(self):
        user = baker.make(User)
        baker.make(EmailAddress, _quantity=5, user=user)

    def test_stringify_email_address(self):
        for address in EmailAddress.objects.all():
            address_info = "{}: {}".format(address.user, address.email)
            self.assertEqual(str(address), address_info)


def working_gpu_data_with_one_gpu_not_in_use():
    return [
        {
            "name": "Test GPU",
            "uuid": "lorem",
            "memory": {
                "total": "100 MB",
                "used": "20 MB",
                "free": "80 MB",
            },
            "in_use": "no",
            "processes": [],
            "gpu_util": "25 %",
        }
    ]


def working_gpu_data_with_one_gpu_in_use():
    base_data = working_gpu_data_with_one_gpu_not_in_use()
    base_data = base_data[0]
    base_data["in_use"] = "yes"
    base_data["processes"] = [
        {
            "pid": 1,
            "username": "Mr. Keks",
            "name": "TestProcess",
            "used_memory": "10 MB",
        }
    ]
    return [base_data]


def working_gpu_data_with_one_gpu_use_na_false():
    base_data = working_gpu_data_with_one_gpu_not_in_use()
    base_data = base_data[0]
    base_data["in_use"] = "na"
    return [base_data]


def working_gpu_data_with_one_gpu_use_na_true():
    base_data = working_gpu_data_with_one_gpu_not_in_use()
    base_data = base_data[0]
    base_data["in_use"] = "na"
    base_data["memory"]["used"] = "900 MB"
    return [base_data]


def request_data(device_name, data_function):
    return {
        "gpu_data": data_function(),
        "device_name": device_name
    }


def return_bytes_io(func):
    def wrapper(*args, **kwargs):
        data = func(*args, **kwargs)
        stream = io.BytesIO(bytearray(data, encoding='utf-8'))
        return stream

    return wrapper


class UpdateGPUTests(APITestCase):

    def setUp(self):
        self.device = device_recipe.make()
        self.url = reverse("update_gpu_info")
        user = self.device.user
        self.client.force_authenticate(user=user)

    def test_update_gpu_info_new_gpu(self):
        self.assertEqual(GPU.objects.count(), 0)
        response = self.client.post(self.url,
                                    request_data(self.device.name, working_gpu_data_with_one_gpu_not_in_use),
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(GPU.objects.count(), 1)

    def test_update_gpu_info_new_gpu_in_use(self):
        self.assertEqual(GPU.objects.count(), 0)
        response = self.client.post(self.url,
                                    request_data(self.device.name, working_gpu_data_with_one_gpu_in_use),
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get()
        self.assertTrue(gpu.in_use)
        self.assertEqual(GPUProcess.objects.count(), 1)

    def test_update_gpu_info_new_gpu_use_na_false(self):
        self.assertEqual(GPU.objects.count(), 0)
        response = self.client.post(self.url,
                                    request_data(self.device.name, working_gpu_data_with_one_gpu_use_na_false),
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get()
        self.assertFalse(gpu.in_use)

    def test_update_gpu_info_new_gpu_use_na_true(self):
        self.assertEqual(GPU.objects.count(), 0)
        response = self.client.post(self.url,
                                    request_data(self.device.name, working_gpu_data_with_one_gpu_use_na_true),
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get()
        self.assertTrue(gpu.in_use)

    def test_update_gpu_info_old_gpu_switch_to_in_use(self):
        baker.make(GPU, device=self.device, uuid="lorem", in_use=False)
        self.assertEqual(GPU.objects.count(), 1)
        self.assertEqual(GPUProcess.objects.count(), 0)
        response = self.client.post(self.url,
                                    request_data(self.device.name, working_gpu_data_with_one_gpu_in_use),
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get()
        self.assertTrue(gpu.in_use)
        self.assertEqual(GPUProcess.objects.count(), 1)

    def test_update_gpu_info_old_gpu_add_new_in_use_gpu(self):
        baker.make(GPU, device=self.device, uuid="test", in_use=False)
        self.assertEqual(GPU.objects.count(), 1)
        self.assertEqual(GPUProcess.objects.count(), 0)
        response = self.client.post(self.url,
                                    request_data(self.device.name, working_gpu_data_with_one_gpu_in_use),
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(GPU.objects.count(), 2)
        gpu = GPU.objects.get(uuid="lorem")
        self.assertTrue(gpu.in_use)
        self.assertEqual(GPUProcess.objects.count(), 1)

    def test_update_gpu_info_old_gpu_add_new_processes(self):
        gpu = baker.make(GPU, device=self.device, uuid="lorem", in_use=False)
        process = baker.make(GPUProcess, gpu=gpu)
        self.assertEqual(GPU.objects.count(), 1)
        self.assertEqual(GPUProcess.objects.count(), 1)
        response = self.client.post(self.url,
                                    request_data(self.device.name, working_gpu_data_with_one_gpu_in_use),
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get(uuid="lorem")
        self.assertTrue(gpu.in_use)
        self.assertEqual(GPUProcess.objects.count(), 1)
        self.assertNotEqual(GPUProcess.objects.get(), process)


admin_mail = "test@example.com"


@override_settings(ADMINS=(("Test", admin_mail),))
class FailedGPUTests(TestCase):

    def setUp(self):
        with mock.patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = utc_now() - datetime.timedelta(hours=2)
            self.gpu_1 = baker.make(GPU)
        self.gpu_2 = baker.make(GPU)
        self.user = baker.make(User)

    def test_failed_gpus_fresh_fail(self):
        pre_last_update = self.gpu_1.last_updated
        determine_failed_gpus()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(admin_mail, mail.outbox[0].to)
        gpu = GPU.objects.get(id=self.gpu_1.id)
        self.assertTrue(gpu.marked_as_failed)
        self.assertEqual(gpu.last_updated, pre_last_update)

    def test_failed_gpus_with_user_with_single_email(self):
        baker.make(Reservation, user=self.user, gpu=self.gpu_1)
        baker.make(Reservation, user=self.user, gpu=self.gpu_2)

        pre_last_update = self.gpu_1.last_updated
        determine_failed_gpus()
        self.assertEqual(len(mail.outbox), 1)
        sent_mail = mail.outbox[0]
        self.assertIn(self.user.email, sent_mail.to)
        self.assertIn(admin_mail, sent_mail.cc)
        gpu = GPU.objects.get(id=self.gpu_1.id)
        self.assertTrue(gpu.marked_as_failed)
        self.assertEqual(gpu.last_updated, pre_last_update)

    def test_failed_gpus_with_multiple_email_addresses(self):
        baker.make(Reservation, user=self.user, gpu=self.gpu_1)
        baker.make(Reservation, user=self.user, gpu=self.gpu_2)
        baker.make(EmailAddress, user=self.user, _quantity=2)
        all_email_addresses = [address.email for address in EmailAddress.objects.filter(user=self.user)]
        all_email_addresses.append(self.user.email)

        pre_last_update = self.gpu_1.last_updated
        determine_failed_gpus()
        self.assertEqual(len(mail.outbox), 1)
        sent_mail = mail.outbox[0]
        for address in all_email_addresses:
            self.assertIn(address, sent_mail.to)
        self.assertIn(admin_mail, sent_mail.cc)
        gpu = GPU.objects.get(id=self.gpu_1.id)
        self.assertTrue(gpu.marked_as_failed)
        self.assertEqual(gpu.last_updated, pre_last_update)

    def test_already_failed_gpu_no_resend(self):
        self.gpu_1.marked_as_failed = True
        with mock.patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = utc_now() - datetime.timedelta(hours=2)
            self.gpu_1.save()

        determine_failed_gpus()
        self.assertEqual(len(mail.outbox), 0)

    def test_multiple_failed_gpus(self):
        baker.make(Reservation, user=self.user, gpu=self.gpu_1)
        with mock.patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = utc_now() - datetime.timedelta(hours=2)
            self.gpu_2.model_name = "new name to force date change"
            self.gpu_2.save()

        determine_failed_gpus()
        self.assertEqual(len(mail.outbox), 2)

        first_mail = mail.outbox[0]
        self.assertIn(self.user.email, first_mail.to)
        self.assertIn(admin_mail, first_mail.cc)
        gpu = GPU.objects.get(id=self.gpu_1.id)
        self.assertTrue(gpu.marked_as_failed)

        second_mail = mail.outbox[1]
        self.assertIn(admin_mail, second_mail.to)
        self.assertIn(admin_mail, second_mail.cc)
        gpu = GPU.objects.get(id=self.gpu_2.id)
        self.assertTrue(gpu.marked_as_failed)

    def test_already_failed_gpu_no_resend_but_send_new_fail(self):
        self.gpu_1.marked_as_failed = True
        with mock.patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = utc_now() - datetime.timedelta(hours=2)
            self.gpu_1.save()
            self.gpu_2.model_name = "new name to force date change"
            self.gpu_2.save()

        determine_failed_gpus()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.gpu_2.model_name, mail.outbox[0].message().as_string())


class HijackTests(WebTest):
    csrf_checks = False

    @classmethod
    def setUpTestData(cls):
        cls.super_user = baker.make(User, is_superuser=True, is_staff=True)
        cls.user = baker.make(User)

    def test_superuser_can_see_link(self):
        response = self.app.get(reverse("index"), user=self.super_user)
        self.assertEqual(response.status_code, 200)

        body = response.body.decode('utf-8')
        self.assertIn("Hijack a User", body)

    def test_non_superuser_can_not_see_link(self):
        response = self.app.get(reverse("index"), user=self.user)
        self.assertEqual(response.status_code, 200)

        body = response.body.decode('utf-8')
        self.assertNotIn("Hijack a User", body)

        self.user.is_staff = True
        self.user.save()

        response = self.app.get(reverse("index"), user=self.user)
        self.assertEqual(response.status_code, 200)

        body = response.body.decode('utf-8')
        self.assertNotIn("Hijack a User", body)

    def test_non_superuser_can_not_request_hijack_select_page(self):
        response = self.app.get(reverse('view_as'), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 403)

    def test_super_user_can_request_hijack_select_page(self):
        response = self.app.get(reverse('view_as'), user=self.super_user)
        self.assertEqual(response.status_code, 200)

        body = response.body.decode('utf-8')
        usernames = [u.username for u in User.objects.all()]
        for username in usernames:
            self.assertIn(username, body)


send_function_mock = mock.MagicMock()


def async_to_sync_mock(func_name):
    return send_function_mock


class PublishMethodTests(TestCase):

    def setUp(self):
        self.device = device_recipe.make()
        self.device_2 = device_recipe.make()
        self.user = baker.make(User)
        assign_perm('labshare.use_device', self.user, self.device)

        baker.make(GPU, device=self.device, _quantity=2)
        baker.make(GPU, device=self.device_2, _quantity=2)

    @mock.patch('labshare.utils.async_to_sync', async_to_sync_mock)
    def test_publish_gpu_states(self):
        publish_gpu_states()
        for device in [self.device, self.device_2]:
            data = {
                'type': 'update_info',
                'message': json.dumps(device.serialize()),
            }

            send_function_mock.assert_any_call(device.name, data)

    @mock.patch('labshare.utils.async_to_sync', async_to_sync_mock)
    def test_publish_device_state_without_channel_name(self):
        publish_device_state(self.device)
        data = {
            'type': 'update_info',
            'message': json.dumps(self.device.serialize()),
        }

        send_function_mock.assert_called_with(self.device.name, data)

    @mock.patch('labshare.utils.async_to_sync', async_to_sync_mock)
    def test_publish_device_state_without_channel_name(self):
        channel_name = "kekse"
        publish_device_state(self.device, channel_name=channel_name)
        data = {
            'type': 'update_info',
            'message': json.dumps(self.device.serialize()),
        }

        send_function_mock.assert_called_with(channel_name, data)


class ConsumerTests(TestCase):

    def setUp(self):
        self.device = device_recipe.make()
        self.device_2 = device_recipe.make()
        self.user = baker.make(User)
        assign_perm('labshare.use_device', self.user, self.device)

        baker.make(GPU, device=self.device, _quantity=2)
        baker.make(GPU, device=self.device_2, _quantity=2)

        self.scope = {
            "user": self.user,
            "url_route": {
                "kwargs": {
                    "device_name": None
                }
            }
        }

        self.consumer = GPUInfoUpdater(self.scope)
        self.consumer.channel_layer = get_channel_layer()
        self.consumer.channel_name = "kekse"
        self.mock_method = mock.MagicMock(return_value=None)
        self.consumer.accept = self.mock_method

    def test_consumer_no_permission(self):
        self.consumer.scope['url_route']['kwargs']['device_name'] = self.device_2.name
        self.consumer.base_send = mock.MagicMock(return_value=None)
        self.consumer.connect()
        self.mock_method.assert_not_called()

    def test_consumer_permission(self):
        self.consumer.scope['url_route']['kwargs']['device_name'] = self.device.name
        self.consumer.connect()
        self.mock_method.assert_called()

    @mock.patch('labshare.consumers.async_to_sync', async_to_sync_mock)
    def test_consumer_disconnect(self):
        device_name = "Lorem-Device"
        self.consumer.device_name = device_name
        self.consumer.disconnect("lorem ipsum")
        send_function_mock.assert_called_with(device_name, self.consumer.channel_name)

    def test_consumer_update_info(self):
        message = "Lorem Ipsum"
        self.consumer.send = mock.MagicMock()
        self.consumer.update_info({"message": message})
        self.consumer.send.assert_called_with(text_data=message)


ldap_staff_name = "Staff"
ldap_student_name = "Student"

auth_ldap_group_map = {
    "cn=Staff,ou=group,dc=example,dc=com": ldap_staff_name
}
auth_ldap_default_group_name = ldap_student_name


@override_settings(AUTH_LDAP_GROUP_MAP=auth_ldap_group_map, AUTH_LDAP_DEFAULT_GROUP_NAME=auth_ldap_default_group_name)
class LDAPTests(WebTest):

    @classmethod
    def setUpTestData(cls):
        cls.staff_group = Group.objects.get(name=ldap_staff_name)
        cls.student_group = baker.make(Group, name=ldap_student_name)

        cls.device = device_recipe.make()
        assign_perm('labshare.use_device', cls.staff_group, cls.device)

        cls.username = "test"
        cls.password = "test"

    def get_ldap_user_result(self, email_addresses=('test2@example.com',), group_name=None):
        def side_effect(*args, **kwargs):
            if len(args) == 1:
                # likely a group search
                if group_name is None:
                    return []
                return [(
                    'cn={group_name},ou=group,dc=example,dc=com'.format(group_name=group_name),
                    {
                        "objectclass": ['groupOfNames'],
                        "member": ['uid=test,ou=people,dc=example,dc=com'],
                        "cn": [group_name],
                    }
                )]
            return [(
                'uid=test,ou=people,dc=example,dc=com',
                {
                    "objectclass": ['top', 'person', 'organizationalPerson', 'inetOrgPerson', 'posixAccount',
                                    'shadowAccount'],
                    "sn": ['test'],
                    "cn": ['test'],
                    'uidnumber': ['1'],
                    'uid': ['test'],
                    'mail': list(email_addresses),
                }
            )]

        return side_effect

    def test_ldap_new_user_created_on_login(self):
        # Each device creates a user in addition to the AnonymousUser
        self.assertEqual(User.objects.count(), Device.objects.count() + 1)
        self.assertEqual(Group.objects.get(name=ldap_student_name).user_set.count(), 0)
        with mock.patch('django_auth_ldap.config.LDAPSearch.execute') as mocked_execute:
            mocked_execute.side_effect = self.get_ldap_user_result()
            with mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
                mocked_bind.return_value = None

                client = Client()
                client.login(username=self.username, password=self.password)

                self.assertEqual(User.objects.count(), Device.objects.count() + 2)
                self.assertEqual(Group.objects.get(name=ldap_student_name).user_set.count(), 1)

    def test_ldap_new_user_created_with_group(self):
        self.assertEqual(User.objects.count(), Device.objects.count() + 1)
        self.assertEqual(Group.objects.get(name=ldap_staff_name).user_set.count(), 0)
        self.assertEqual(Group.objects.get(name=ldap_student_name).user_set.count(), 0)
        with mock.patch('django_auth_ldap.config.LDAPSearch.execute') as mocked_execute:
            mocked_execute.side_effect = self.get_ldap_user_result(group_name=ldap_staff_name)
            with mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
                mocked_bind.return_value = None

                client = Client()
                client.login(username=self.username, password=self.password)

                self.assertEqual(User.objects.count(), Device.objects.count() + 2)
                self.assertEqual(Group.objects.get(name=ldap_staff_name).user_set.count(), 1)
                self.assertEqual(Group.objects.get(name=ldap_student_name).user_set.count(), 0)

    def test_ldap_change_mail_addresses(self):
        with mock.patch('django_auth_ldap.config.LDAPSearch.execute') as mocked_execute:
            mocked_execute.side_effect = self.get_ldap_user_result()
            with mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
                mocked_bind.return_value = None

                client = Client()
                client.login(username=self.username, password=self.password)

                test_email_address = 'testtest@example.com'
                mocked_execute.side_effect = self.get_ldap_user_result(email_addresses=[test_email_address])
                self.assertNotEqual(User.objects.get(username=self.username).email, test_email_address)

                client.logout()
                client.login(username=self.username, password=self.password)

                self.assertEqual(User.objects.get(username=self.username).email, test_email_address)

    def test_ldap_add_multiple_mail_addresses(self):
        with mock.patch('django_auth_ldap.config.LDAPSearch.execute') as mocked_execute:
            mocked_execute.side_effect = self.get_ldap_user_result(
                email_addresses=['t@t.com', 'test@test.com', 'test@example.com'])
            with mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
                mocked_bind.return_value = None

                client = Client()
                client.login(username=self.username, password=self.password)

                user = User.objects.get(username=self.username)
                self.assertEqual(EmailAddress.objects.filter(user=user).count(), 2)

    def test_ldap_change_multiple_mail_addresses(self):
        with mock.patch('django_auth_ldap.config.LDAPSearch.execute') as mocked_execute:
            mocked_execute.side_effect = self.get_ldap_user_result(email_addresses=['t@t.com', 'test@example.com'])
            with mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
                mocked_bind.return_value = None

                client = Client()
                client.login(username=self.username, password=self.password)

                user = User.objects.get(username=self.username)
                self.assertEqual(EmailAddress.objects.filter(user=user).count(), 1)

                addresses = ['test@de.de', 'test@example.com', 'test@huhu.com']
                mocked_execute.side_effect = self.get_ldap_user_result(email_addresses=addresses)

                client.logout()
                client.login(username=self.username, password=self.password)

                user = User.objects.get(username=self.username)
                user_mail_addresses = EmailAddress.objects.filter(user=user)
                self.assertEqual(user_mail_addresses.count(), 2)
                self.assertEqual(user.email, addresses[0])
                for mail_address in user_mail_addresses.all():
                    self.assertIn(mail_address.email, addresses[1:])

    def test_ldap_change_group_membership(self):
        self.assertEqual(Group.objects.get(name=ldap_student_name).user_set.count(), 0)
        self.assertEqual(Group.objects.get(name=ldap_staff_name).user_set.count(), 0)

        with mock.patch('django_auth_ldap.config.LDAPSearch.execute') as mocked_execute:
            mocked_execute.side_effect = self.get_ldap_user_result()
            with mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
                mocked_bind.return_value = None

                client = Client()
                client.login(username=self.username, password=self.password)

                self.assertEqual(Group.objects.get(name=ldap_student_name).user_set.count(), 1)
                self.assertEqual(Group.objects.get(name=ldap_staff_name).user_set.count(), 0)

                mocked_execute.side_effect = self.get_ldap_user_result(group_name=ldap_staff_name)

                client.logout()
                client.login(username=self.username, password=self.password)

                self.assertEqual(Group.objects.get(name=ldap_student_name).user_set.count(), 0)
                self.assertEqual(Group.objects.get(name=ldap_staff_name).user_set.count(), 1)


class FrontendTestsBase(ChannelsLiveServerTestCase):
    serve_static = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        chrome_options = Options()
        for option in ["headless", "disable-gpu", "no-sandbox", "disable-dev-shm-usage"]:
            chrome_options.add_argument(option)
        try:
            cls.driver = webdriver.Chrome(options=chrome_options)
        except:
            super().tearDownClass()
            raise

        cls.password = 'test'

        cls.staff_user = baker.make(User, is_superuser=True)
        cls.staff_user.set_password(cls.password)
        cls.staff_user.save()

        cls.user = baker.make(User)
        cls.user.set_password(cls.password)
        cls.user.save()

        cls.device_1 = device_recipe.make()
        gpus = baker.make(GPU, device=cls.device_1, _quantity=4)
        baker.make(GPUProcess, gpu=gpus[0])
        baker.make(GPUProcess, gpu=gpus[1])
        baker.make(Reservation, user=cls.staff_user, gpu=gpus[0])
        baker.make(Reservation, user=cls.user, gpu=gpus[1])
        baker.make(Reservation, user=cls.staff_user, gpu=gpus[1])
        assign_perm('labshare.use_device', cls.user, cls.device_1)
        make_reservation_in_the_past(cls.staff_user, gpus[3], Reservation.usage_period() - timedelta(days=1))

        cls.device_2 = device_recipe.make()
        gpus = baker.make(GPU, device=cls.device_2, _quantity=2)
        baker.make(GPUProcess, gpu=gpus[0], _quantity=2)
        baker.make(Reservation, user=cls.staff_user, gpu=gpus[0])

    def setUp(self):
        self.login_user(self.staff_user.username)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        super().tearDownClass()

    def login_user(self, username):
        if self.driver.get_cookie('sessionid') is not None:
            return

        login_success = self.client.login(username=username, password=self.password)
        self.assertTrue(login_success)
        session_cookie = self.client.cookies['sessionid']

        self.driver.get(self.live_server_url + reverse('index'))
        self.driver.add_cookie({'name': 'sessionid', 'value': session_cookie.value, 'secure': False, 'path': '/'})
        self.driver.refresh()

    def wait_for_page_load(self):
        self.driver.refresh()
        WebDriverWait(self.driver, 2).until(
            lambda _: all(EC.presence_of_element_located((By.ID, gpu.uuid)) for gpu in GPU.objects.all()),
            "Gpus did not show up on page, Websocket Connection not okay?"
        )
        self.open_gpu_dropdowns()

    def open_gpu_dropdowns(self):
        gpu_buttons = self.driver.find_elements_by_class_name('device-heading-btn')
        for button in gpu_buttons:
            button.click()

    def open_new_window(self):
        self.driver.execute_script('window.open("about:blank", "_blank");')
        self.driver.switch_to_window(self.driver.window_handles[-1])

    def close_all_new_windows(self):
        while len(self.driver.window_handles) > 1:
            self.driver.switch_to_window(self.driver.window_handles[-1])
            self.driver.execute_script('window.close();')
        if len(self.driver.window_handles) == 1:
            self.driver.switch_to_window(self.driver.window_handles[0])

    def switch_to_window(self, window_id):
        self.driver.switch_to_window(self.driver.window_handles[window_id])


@skipIf("TRAVIS" in os.environ and os.environ["TRAVIS"] == "true", "Skipping this test on Travis CI.")
class FrontendOverviewNonSuperuserTest(FrontendTestsBase):

    def setUp(self):
        self.login_user(self.user.username)

    def test_overview_table_populated_with_device_data_for_non_superuser(self):
        WebDriverWait(self.driver, 2).until(
            lambda _: all(
                EC.presence_of_element_located((By.ID, gpu.uuid)) for gpu in GPU.objects.filter(device=self.device_1)),
            "Gpus did not show up on page, Websocket Connection not okay?"
        )

        for gpu in GPU.objects.filter(device=self.device_1):
            self.driver.find_element_by_id(gpu.uuid)

        for gpu in GPU.objects.filter(device=self.device_2):
            self.assertRaises(NoSuchElementException, self.driver.find_element_by_id, gpu.uuid)


@skipIf("TRAVIS" in os.environ and os.environ["TRAVIS"] == "true", "Skipping this test on Travis CI.")
class FrontendOverviewProcessListTest(FrontendTestsBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.driver.implicitly_wait(0.5)

    def test_process_overview(self):
        self.wait_for_page_load()
        self.assertRaises(NoSuchElementException, self.driver.find_element_by_id, "full-process-list")

        gpu = self.device_2.gpus.first()
        gpu_row = self.driver.find_element_by_id(gpu.uuid)
        process_show_button = gpu_row.find_element_by_class_name("gpu-process-show")
        WebDriverWait(self.driver, 2).until(
            lambda _: EC.element_to_be_clickable(process_show_button)
        )
        # self.assertFalse(process_show_button.get_property("disabled"))
        self.assertRaises(NoSuchElementException, self.driver.find_element_by_id, "full-process-list")
        process_show_button.click()

        WebDriverWait(self.driver, 2).until(
            lambda _: EC.visibility_of_element_located((By.ID, "full-process-list")),
            "Modal did not open!"
        )

        process_list = self.driver.find_element_by_id("full-process-list")
        process_details = process_list.find_elements_by_class_name("gpu-process-details")

        for process_detail, process in zip(process_details, gpu.processes.all()):
            process_name = process_detail.find_element_by_class_name("card-header").text
            self.assertIn(process_name, process.name)

            pid_text = process_detail.find_elements_by_class_name("list-group-item")[0].text
            self.assertIn(str(process.pid), pid_text)

            user_text = process_detail.find_elements_by_class_name("list-group-item")[1].text
            self.assertIn(process.username, user_text)

            memory_text = process_detail.find_elements_by_class_name("list-group-item")[2].text
            self.assertIn(process.memory_usage, memory_text)
