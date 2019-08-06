import datetime
import io
import json
import unittest.mock as mock
import os
import random
import string
from channels.layers import get_channel_layer
from channels.testing import ChannelsLiveServerTestCase
from datetime import timedelta
from django import template
from django.contrib.auth.models import User, Group
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django_webtest import WebTest
from guardian.shortcuts import assign_perm
from guardian.utils import get_anonymous_user
from model_mommy import mommy
from model_mommy.recipe import Recipe
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from unittest import skipIf
from unittest.mock import Mock
from urllib.error import URLError

from labshare.consumers import GPUInfoUpdater
from labshare.models import Device, GPU, Reservation, GPUProcess, EmailAddress
from labshare.templatetags.icon import icon
from labshare.utils import get_devices, update_gpu_info, determine_failed_gpus, publish_gpu_states, publish_device_state

device_recipe = Recipe(
    Device,
    name=lambda: ''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(16))
)


class TestLabshare(WebTest):

    csrf_checks = False

    @classmethod
    def setUpTestData(cls):
        cls.user = mommy.make(User)
        cls.devices = device_recipe.make(_quantity=3)
        mommy.make(GPU, device=cls.devices[0], _quantity=2, used_memory="12 Mib", total_memory="112 Mib")
        cls.gpu = mommy.make(GPU, device=cls.devices[1], used_memory="12 Mib", total_memory="112 Mib")
        mommy.make(GPU, device=cls.devices[-1], used_memory="12 Mib", total_memory="112 Mib")

        cls.group = mommy.make(Group)
        cls.user.groups.add(cls.group)

        for device in cls.devices:
            assign_perm('use_device', cls.group, device)

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
        mommy.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)

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
            mommy.make(Reservation, gpu=gpu, user=self.user)

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
        mommy.make(Reservation, gpu=self.devices[1].gpus.first(), user=self.user)

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
        mommy.make(Reservation, gpu=gpu, user=self.user)
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
        user = mommy.make(User)
        user.groups.add(self.group)
        mommy.make(Reservation, gpu=gpu, user=user)

        response = self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 403)

    def test_gpu_done_only_one_reservation(self):
        gpu = self.devices[0].gpus.first()
        mommy.make(Reservation, gpu=gpu, user=self.user)

        response = self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_gpu_done_one_more_reservation(self):
        gpu = self.devices[0].gpus.first()
        mommy.make(Reservation, _quantity=2, gpu=gpu, user=self.user, user_reserved_next_available_spot=False)

        response = self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_gpu_done_next_available_spot_reserved(self):
        user = mommy.make(User)
        user.groups.add(self.group)
        gpus = self.devices[0].gpus
        mommy.make(Reservation, gpu=gpus.first(), user=self.user)
        mommy.make(Reservation, gpu=gpus.last(), user=self.user)
        mommy.make(Reservation, gpu=gpus.first(), user=user, user_reserved_next_available_spot=True)
        mommy.make(Reservation, gpu=gpus.last(), user=user, user_reserved_next_available_spot=True)
        self.assertEqual(Reservation.objects.count(), 4)

        response = self.app.post(reverse("done_with_gpu", args=[gpus.first().id]), user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(gpus.first().reservations.count(), 1)
        self.assertEqual(gpus.first().reservations.first().user, user)
        self.assertEqual(gpus.last().reservations.count(), 1)
        self.assertEqual(gpus.last().reservations.first().user, self.user)

    def test_gpu_done_next_available_spot_reserved_additional_reservation(self):
        user = mommy.make(User)
        user.groups.add(self.group)
        gpus = self.devices[0].gpus
        mommy.make(Reservation, gpu=gpus.last(), user=self.user)
        mommy.make(Reservation, gpu=gpus.first(), user=self.user)
        mommy.make(Reservation, gpu=gpus.first(), user=user, user_reserved_next_available_spot=True)
        mommy.make(Reservation, gpu=gpus.last(), user=user, user_reserved_next_available_spot=True)
        mommy.make(Reservation, gpu=gpus.last(), user=self.user)
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
        other = mommy.make(User)
        other.groups.add(self.group)
        mommy.make(Reservation, gpu=gpu, user=self.user)
        mommy.make(Reservation, gpu=gpu, user=other)
        mommy.make(Reservation, gpu=gpu, user=self.user)

        self.assertEqual(gpu.last_reservation().user, self.user)
        self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(gpu.last_reservation().user, other)
        self.assertEqual(gpu.current_reservation().user, self.user)
        self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=other)
        self.assertEqual(Reservation.objects.count(), 1)
        self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(gpu.last_reservation(), None)

    def test_cancel_gpu_reservation_wrong_user(self):
        gpu = self.devices[0].gpus.first()
        users = mommy.make(User, _quantity=2)
        for user in users:
            user.groups.add(self.group)
        mommy.make(Reservation, gpu=gpu, user=users[0])
        mommy.make(Reservation, gpu=gpu, user=users[1])

        response = self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=self.user, expect_errors=True)
        self.assertEqual(response.status_code, 404)

    def test_cancel_gpu(self):
        gpu = self.devices[0].gpus.first()
        other = mommy.make(User)
        other.groups.add(self.group)
        mommy.make(Reservation, gpu=gpu, user=other)
        mommy.make(Reservation, gpu=gpu, user=self.user)

        self.app.post(reverse("cancel_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(gpu.current_reservation().user, other)

    def test_gpu_updated_too_long_ago(self):
        for gpu in GPU.objects.all():
            last_updated = gpu.last_updated
            self.assertFalse(gpu.last_update_too_long_ago())
            gpu.last_updated = last_updated - timedelta(minutes = 29)
            self.assertFalse(gpu.last_update_too_long_ago())
            gpu.last_updated = last_updated - timedelta(minutes = 30)
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
        device = mommy.prepare(Device)
        self.assertEqual(str(device), device.name)

    def test_gpu_str_representation(self):
        gpu = mommy.prepare(GPU, device=self.devices[0])
        self.assertEqual(str(gpu), gpu.model_name)

    def test_reservation_str_representation(self):
        reservation = mommy.prepare(Reservation, gpu=self.gpu, user=self.user)
        self.assertEqual(str(reservation), "{gpu} on {device}, {user}".format(
            gpu=reservation.gpu,
            device=reservation.gpu.device,
            user=reservation.user
        ))


class TestMessages(WebTest):

    csrf_checks = False

    def setUp(self):
        self.user = mommy.make(User, is_superuser=True, is_staff=True, email="test@example.com")
        self.devices = device_recipe.make(_quantity=3)
        mommy.make(GPU, device=self.devices[0], _quantity=2, used_memory="12 Mib", total_memory="112 Mib")
        mommy.make(GPU, device=self.devices[1], used_memory="12 Mib", total_memory="112 Mib")
        mommy.make(GPU, device=self.devices[-1], used_memory="12 Mib", total_memory="112 Mib")

        self.group = mommy.make(Group)
        for device in self.devices:
            assign_perm('use_device', self.group, device)
        self.user.groups.add(self.group)

    def test_view_message_site_no_user(self):
        response = self.app.get(reverse("send_message"), expect_errors=True)
        self.assertEqual(response.status_code, 302)

    def test_view_message_site_normal_user(self):
        user = mommy.make(User)
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
        _ = mommy.make(User)

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
        user = mommy.make(User)
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
        addresses = [address.email for address in mommy.make(EmailAddress, _quantity=3, user=self.user)]
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
        self.user = mommy.make(User, is_superuser=True)

        devices = device_recipe.make(_quantity=3)
        for idx, device in enumerate(devices):
            gpus = mommy.make(GPU, _quantity=2, device=device)
            for gpu in gpus:
                if idx == 1:
                    mommy.make(GPUProcess, gpu=gpu, _quantity=2)
                elif idx == 2:
                    mommy.make(GPUProcess, gpu=gpu)

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
        self.staff_user = mommy.make(User)
        self.user = mommy.make(User)
        self.devices = device_recipe.make(_quantity=3)
        mommy.make(GPU, device=self.devices[0], _quantity=2, used_memory="12 Mib", total_memory="112 Mib")
        mommy.make(GPU, device=self.devices[1], used_memory="12 Mib", total_memory="112 Mib")
        mommy.make(GPU, device=self.devices[-1], used_memory="12 Mib", total_memory="112 Mib")

        self.student_group = mommy.make(Group, name="students")
        self.user.groups.add(self.student_group)

        for device in self.devices[:-1]:
            assign_perm('use_device', self.student_group, device)

        self.staff_group = mommy.make(Group, name="staff")
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
        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[0].name), user=self.user, xhr=True)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[-1].name), user=self.user, expect_errors=True, xhr=True)
        self.assertEqual(response.status_code, 403)

        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[-1].name), user=self.staff_user, xhr=True)
        self.assertEqual(response.status_code, 200)

    def test_gpu_listing_non_existing_gpu_name(self):
        names_to_test = ['undefined', 'a_name_that_will_never_exist_at_least_we_hope_so']
        for name in names_to_test:
            response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(name), user=self.user, xhr=True, expect_errors=True)
            self.assertEqual(response.status_code, 404)

    def test_gpu_info(self):
        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[0].gpus.first().uuid), user=self.user, xhr=True)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[-1].gpus.first().uuid), user=self.user, expect_errors=True, xhr=True)
        self.assertEqual(response.status_code, 403)

        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[-1].gpus.first().uuid), user=self.staff_user, xhr=True)
        self.assertEqual(response.status_code, 200)

    def test_gpu_info_non_existent_gpu(self):
        ids_to_test = ['undefined', 'a_name_that_will_never_exist_at_least_we_hope_so']
        for id in ids_to_test:
            response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(id), user=self.user, xhr=True, expect_errors=True)
            self.assertEqual(response.status_code, 404)

    def test_gpu_done(self):
        mommy.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)
        response = self.app.post(reverse("done_with_gpu", args=[self.devices[0].gpus.first().id]), user=self.user)
        self.assertEqual(response.status_code, 200)

        mommy.make(Reservation, gpu=self.devices[-1].gpus.first(), user=self.staff_user)
        response = self.app.post(
            reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]),
            user=self.user,
            expect_errors=True
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Reservation.objects.count(), 1)

        response = self.app.post(reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]), user=self.staff_user)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(
            reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]),
            user=self.user,
            expect_errors=True
        )
        self.assertEqual(response.status_code, 400)

    def test_gpu_cancel(self):
        mommy.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)
        response = self.app.post(reverse("cancel_gpu", args=[self.devices[0].gpus.first().id]), user=self.user)
        self.assertEqual(response.status_code, 200)

        mommy.make(Reservation, gpu=self.devices[-1].gpus.first(), user=self.staff_user)
        response = self.app.post(reverse("cancel_gpu", args=[self.devices[-1].gpus.first().id]), user=self.user,
                                expect_errors=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Reservation.objects.count(), 1)

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
        user = mommy.make(User)
        mommy.make(EmailAddress, _quantity=5, user=user)

    def test_stringify_email_address(self):
        for address in EmailAddress.objects.all():
            address_info = "{}: {}".format(address.user, address.email)
            self.assertEqual(str(address), address_info)


def fail_url(url, timeout=None):
    raise URLError("400")


def working_gpu_data_with_one_gpu_not_in_use(url, timeout=None):
    return json.dumps([
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
        }
    ])


def working_gpu_data_with_one_gpu_in_use(url, timeout=None):
    base_data = working_gpu_data_with_one_gpu_not_in_use(url, timeout=timeout)
    base_data = json.loads(base_data)[0]
    base_data["in_use"] = "yes"
    base_data["processes"] = [
        {
            "pid": 1,
            "username": "Mr. Keks",
            "name": "TestProcess",
            "used_memory": "10 MB",
        }
    ]
    return json.dumps([base_data])


def working_gpu_data_with_one_gpu_use_na_false(url, timeout=None):
    base_data = working_gpu_data_with_one_gpu_not_in_use(url, timeout=timeout)
    base_data = json.loads(base_data)[0]
    base_data["in_use"] = "na"
    return json.dumps([base_data])


def working_gpu_data_with_one_gpu_use_na_true(url, timeout=None):
    base_data = working_gpu_data_with_one_gpu_not_in_use(url, timeout=timeout)
    base_data = json.loads(base_data)[0]
    base_data["in_use"] = "na"
    base_data["memory"]["used"] = "900 MB"
    return json.dumps([base_data])


def return_bytes_io(func):
    def wrapper(*args, **kwargs):
        data = func(*args, **kwargs)
        stream = io.BytesIO(bytearray(data, encoding='utf-8'))
        return stream
    return wrapper


class UpdateGPUTests(TestCase):

    def setUp(self):
        self.device = device_recipe.make()

    @mock.patch("urllib.request.urlopen", fail_url)
    def test_update_gpu_info_no_gpu_works(self):
        mommy.make(Device)
        for device in Device.objects.all():
            mommy.make(GPU, device=device, _quantity=2)
        pre_call_last_update = [gpu.last_updated for gpu in GPU.objects.all()]
        update_gpu_info()
        post_call_last_update = [gpu.last_updated for gpu in GPU.objects.all()]
        for timestamp_before_call, timestamp_after_call in zip(pre_call_last_update, post_call_last_update):
            self.assertEqual(timestamp_before_call, timestamp_after_call)

    @mock.patch("urllib.request.urlopen", return_bytes_io(working_gpu_data_with_one_gpu_not_in_use))
    def test_update_gpu_info_new_gpu(self):
        self.assertEqual(GPU.objects.count(), 0)
        update_gpu_info()
        self.assertEqual(GPU.objects.count(), 1)

    @mock.patch("urllib.request.urlopen", return_bytes_io(working_gpu_data_with_one_gpu_in_use))
    def test_update_gpu_info_new_gpu_in_use(self):
        self.assertEqual(GPU.objects.count(), 0)
        update_gpu_info()
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get()
        self.assertTrue(gpu.in_use)
        self.assertEqual(GPUProcess.objects.count(), 1)

    @mock.patch("urllib.request.urlopen", return_bytes_io(working_gpu_data_with_one_gpu_use_na_false))
    def test_update_gpu_info_new_gpu_use_na_false(self):
        self.assertEqual(GPU.objects.count(), 0)
        update_gpu_info()
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get()
        self.assertFalse(gpu.in_use)

    @mock.patch("urllib.request.urlopen", return_bytes_io(working_gpu_data_with_one_gpu_use_na_true))
    def test_update_gpu_info_new_gpu_use_na_true(self):
        self.assertEqual(GPU.objects.count(), 0)
        update_gpu_info()
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get()
        self.assertTrue(gpu.in_use)

    @mock.patch("urllib.request.urlopen", return_bytes_io(working_gpu_data_with_one_gpu_in_use))
    def test_update_gpu_info_old_gpu_switch_to_in_use(self):
        mommy.make(GPU, device=self.device, uuid="lorem", in_use=False)
        self.assertEqual(GPU.objects.count(), 1)
        self.assertEqual(GPUProcess.objects.count(), 0)
        update_gpu_info()
        self.assertEqual(GPU.objects.count(), 1)
        gpu = GPU.objects.get()
        self.assertTrue(gpu.in_use)
        self.assertEqual(GPUProcess.objects.count(), 1)

    @mock.patch("urllib.request.urlopen", return_bytes_io(working_gpu_data_with_one_gpu_in_use))
    def test_update_gpu_info_old_gpu_add_new_in_use_gpu(self):
        mommy.make(GPU, device=self.device, uuid="test", in_use=False)
        self.assertEqual(GPU.objects.count(), 1)
        self.assertEqual(GPUProcess.objects.count(), 0)
        update_gpu_info()
        self.assertEqual(GPU.objects.count(), 2)
        gpu = GPU.objects.get(uuid="lorem")
        self.assertTrue(gpu.in_use)
        self.assertEqual(GPUProcess.objects.count(), 1)

    @mock.patch("urllib.request.urlopen", return_bytes_io(working_gpu_data_with_one_gpu_in_use))
    def test_update_gpu_info_old_gpu_add_new_processes(self):
        gpu = mommy.make(GPU, device=self.device, uuid="lorem", in_use=False)
        process = mommy.make(GPUProcess, gpu=gpu)
        self.assertEqual(GPU.objects.count(), 1)
        self.assertEqual(GPUProcess.objects.count(), 1)
        update_gpu_info()
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
            mock_now.return_value = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(hours=2)
            self.gpu_1 = mommy.make(GPU)
        self.gpu_2 = mommy.make(GPU)
        self.user = mommy.make(User)

    def test_failed_gpus_fresh_fail(self):
        pre_last_update = self.gpu_1.last_updated
        determine_failed_gpus()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(admin_mail, mail.outbox[0].to)
        gpu = GPU.objects.get(id=self.gpu_1.id)
        self.assertTrue(gpu.marked_as_failed)
        self.assertEqual(gpu.last_updated, pre_last_update)

    def test_failed_gpus_with_user_with_single_email(self):
        mommy.make(Reservation, user=self.user, gpu=self.gpu_1)
        mommy.make(Reservation, user=self.user, gpu=self.gpu_2)

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
        mommy.make(Reservation, user=self.user, gpu=self.gpu_1)
        mommy.make(Reservation, user=self.user, gpu=self.gpu_2)
        mommy.make(EmailAddress, user=self.user, _quantity=2)
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
            mock_now.return_value = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(hours=2)
            self.gpu_1.save()

        determine_failed_gpus()
        self.assertEqual(len(mail.outbox), 0)

    def test_multiple_failed_gpus(self):
        mommy.make(Reservation, user=self.user, gpu=self.gpu_1)
        with mock.patch("django.utils.timezone.now") as mock_now:
            mock_now.return_value = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(hours=2)
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
            mock_now.return_value = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(hours=2)
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
        cls.super_user = mommy.make(User, is_superuser=True, is_staff=True)
        cls.user = mommy.make(User)

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
        self.user = mommy.make(User)
        assign_perm('labshare.use_device', self.user, self.device)

        mommy.make(GPU, device=self.device, _quantity=2)
        mommy.make(GPU, device=self.device_2, _quantity=2)

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
        self.user = mommy.make(User)
        assign_perm('labshare.use_device', self.user, self.device)

        mommy.make(GPU, device=self.device, _quantity=2)
        mommy.make(GPU, device=self.device_2, _quantity=2)

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


class FrontendTestsBase(ChannelsLiveServerTestCase):
    serve_static = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.driver = webdriver.Chrome()
        except:
            super().tearDownClass()
            raise

        cls.password = 'test'

        cls.staff_user = mommy.make(User, is_superuser=True)
        cls.staff_user.set_password(cls.password)
        cls.staff_user.save()

        cls.user = mommy.make(User)
        cls.user.set_password(cls.password)
        cls.user.save()

        cls.device_1 = device_recipe.make()
        gpus = mommy.make(GPU, device=cls.device_1, _quantity=3)
        mommy.make(GPUProcess, gpu=gpus[0])
        mommy.make(GPUProcess, gpu=gpus[1])
        mommy.make(Reservation, user=cls.staff_user, gpu=gpus[0])
        mommy.make(Reservation, user=cls.user, gpu=gpus[1])
        mommy.make(Reservation, user=cls.staff_user, gpu=gpus[1])
        assign_perm('labshare.use_device', cls.user, cls.device_1)

        cls.device_2 = device_recipe.make()
        gpus = mommy.make(GPU, device=cls.device_2, _quantity=2)
        mommy.make(GPUProcess, gpu=gpus[0], _quantity=2)
        mommy.make(Reservation, user=cls.staff_user, gpu=gpus[0])

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
class FrontendOverviewDataTest(FrontendTestsBase):

    def test_overview_gpu_data_correct(self):
        self.wait_for_page_load()

        for gpu in GPU.objects.all():
            # 1. check that the elements look correct
            element = self.driver.find_element_by_id(gpu.uuid)
            memory_element = element.find_element_by_class_name("gpu-memory").text
            self.assertEqual(memory_element, gpu.memory_usage())
            current_reservation_element = element.find_element_by_class_name("gpu-current-reservation").text
            current_user = gpu.get_current_user()
            self.assertEqual(current_reservation_element, getattr(current_user, 'username', ''))
            next_reservation_element = element.find_element_by_class_name("gpu-next-reservation").text
            next_users = gpu.get_next_users()
            self.assertEqual(next_reservation_element, next_users[0].username if len(next_users) > 0 else '')

            # 2. check that the buttons are rendered correctly
            buttons = element.find_element_by_class_name("gpu-actions")
            hidden_elements = buttons.find_elements_by_class_name("d-none")
            self.assertEqual(len(hidden_elements), 2)
            current_reservation = gpu.get_current_reservation()
            next_reservations = list(gpu.get_next_reservations())
            for reservation in Reservation.objects.filter(gpu=gpu, user=self.staff_user):
                if reservation == current_reservation:
                    self.assertNotIn(
                        "d-none",
                        buttons.find_element_by_class_name("gpu-done-button").get_attribute("class")
                    )
                else:
                    self.assertIn(
                        "d-none",
                        buttons.find_element_by_class_name("gpu-done-button").get_attribute("class")
                    )

                if reservation in next_reservations:
                    self.assertNotIn(
                        "d-none",
                        buttons.find_element_by_class_name("gpu-cancel-button").get_attribute("class")
                    )
                else:
                    self.assertIn(
                        "d-none",
                        buttons.find_element_by_class_name("gpu-cancel-button").get_attribute("class")
                    )

                if reservation != current_reservation and reservation not in next_reservations:
                    self.assertNotIn(
                        "d-none",
                        buttons.find_element_by_class_name("gpu-reserve-button").get_attribute("class")
                    )
                else:
                    self.assertIn(
                        "d-none",
                        buttons.find_element_by_class_name("gpu-reserve-button").get_attribute("class")
                    )


@skipIf("TRAVIS" in os.environ and os.environ["TRAVIS"] == "true", "Skipping this test on Travis CI.")
class FrontendOverviewNonSuperuserTest(FrontendTestsBase):

    def setUp(self):
        self.login_user(self.user.username)

    def test_overview_table_populated_with_device_data_for_non_superuser(self):
        WebDriverWait(self.driver, 2).until(
            lambda _: all(EC.presence_of_element_located((By.ID, gpu.uuid)) for gpu in GPU.objects.filter(device=self.device_1)),
            "Gpus did not show up on page, Websocket Connection not okay?"
        )

        for gpu in GPU.objects.filter(device=self.device_2):
            self.assertRaises(NoSuchElementException, self.driver.find_element_by_id, gpu.uuid)


@skipIf("TRAVIS" in os.environ and os.environ["TRAVIS"] == "true", "Skipping this test on Travis CI.")
class FrontendOverviewDoneButtonTest(FrontendTestsBase):

    def test_overview_done_button(self):
        self.wait_for_page_load()

        num_reservations_for_user = Reservation.objects.filter(user=self.staff_user).count()
        gpu = self.driver.find_element_by_id(self.device_1.gpus.first().uuid)
        done_button = gpu.find_element_by_class_name("gpu-done-button")
        self.assertNotIn("hidden", done_button.get_attribute("class"))
        done_button.click()

        self.wait_for_page_load()
        self.assertEqual(Reservation.objects.filter(user=self.staff_user).count(), num_reservations_for_user - 1)


@skipIf("TRAVIS" in os.environ and os.environ["TRAVIS"] == "true", "Skipping this test on Travis CI.")
class FrontendOverviewCancelButtonTest(FrontendTestsBase):

    def test_overview_cancel_button(self):
        self.wait_for_page_load()

        num_reservations_for_user = Reservation.objects.filter(user=self.staff_user).count()
        gpu = self.driver.find_element_by_id(list(self.device_1.gpus.all())[1].uuid)
        cancel_button = gpu.find_element_by_class_name("gpu-cancel-button")
        self.assertNotIn("hidden", cancel_button.get_attribute("class"))
        cancel_button.click()

        self.wait_for_page_load()
        self.assertEqual(Reservation.objects.filter(user=self.staff_user).count(), num_reservations_for_user - 1)


@skipIf("TRAVIS" in os.environ and os.environ["TRAVIS"] == "true", "Skipping this test on Travis CI.")
class FrontendOverviewReserveButtonTest(FrontendTestsBase):

    def test_overview_cancel_button(self):
        self.wait_for_page_load()

        num_reservations_for_user = Reservation.objects.filter(user=self.staff_user).count()
        gpu = self.driver.find_element_by_id(list(self.device_1.gpus.all())[2].uuid)
        reserve_button = gpu.find_element_by_class_name("gpu-reserve-button")
        self.assertNotIn("hidden", reserve_button.get_attribute("class"))
        reserve_button.click()

        self.wait_for_page_load()
        self.assertEqual(Reservation.objects.filter(user=self.staff_user).count(), num_reservations_for_user + 1)


@skipIf("TRAVIS" in os.environ and os.environ["TRAVIS"] == "true", "Skipping this test on Travis CI.")
class FrontendOverviewReserveSyncTest(FrontendTestsBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.driver.implicitly_wait(0.5)

    def test_overview_button_sync(self):
        self.wait_for_page_load()
        try:
            self.open_new_window()
            self.driver.get(self.live_server_url + reverse('index'))
            self.wait_for_page_load()
            self.switch_to_window(0)

            # click the reserve button
            gpu = self.driver.find_element_by_id(list(self.device_1.gpus.all())[2].uuid)
            gpu.find_element_by_class_name("gpu-reserve-button").click()
            self.wait_for_page_load()

            # check that reservation appeared in new window
            self.switch_to_window(1)
            self.wait_for_page_load()
            gpu = self.driver.find_element_by_id(list(self.device_1.gpus.all())[2].uuid)
            reserve_button = gpu.find_element_by_class_name("gpu-reserve-button")
            self.assertIn("d-none", reserve_button.get_attribute("class"))
            done_button = gpu.find_element_by_class_name("gpu-done-button")
            self.assertNotIn("d-none", done_button.get_attribute("class"))
        finally:
            self.close_all_new_windows()


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
