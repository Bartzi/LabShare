import datetime
import io
import json
import mock as mock
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
from unittest.mock import Mock
from urllib.error import URLError

from labshare.models import Device, GPU, Reservation, GPUProcess, EmailAddress
from labshare.templatetags.icon import icon
from labshare.templatetags.reservations import queue_position
from labshare.utils import get_devices, update_gpu_info, determine_failed_gpus


class TestLabshare(WebTest):

    csrf_checks = False

    @classmethod
    def setUpTestData(cls):
        cls.user = mommy.make(User)
        cls.devices = mommy.make(Device, _quantity=3)
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

    def test_index_containing_reservations(self):
        user1 = mommy.make(User)
        user1.groups.add(self.group)
        user2 = mommy.make(User)
        user2.groups.add(self.group)
        mommy.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)
        mommy.make(Reservation, gpu=self.devices[0].gpus.first(), user=user1)
        mommy.make(Reservation, gpu=self.devices[1].gpus.first(), user=user2)

        response = self.app.get(reverse("index"), user=self.user)
        self.assertEqual(response.status_code, 200)

        for reservation in Reservation.objects.all():
            self.assertIn(reservation.user.username, response.body.decode('utf-8'))

        user3 = mommy.make(User)
        user3.groups.add(self.group)
        mommy.make(Reservation, gpu=self.devices[0].gpus.first(), user=user3)

        response = self.app.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(user3.username, response.body.decode('utf-8'))

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
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_gpu_done_one_more_reservation(self):
        gpu = self.devices[0].gpus.first()
        mommy.make(Reservation, _quantity=2, gpu=gpu, user=self.user, user_reserved_next_available_spot=False)

        response = self.app.post(reverse("done_with_gpu", args=[gpu.id]), user=self.user)
        self.assertEqual(response.status_code, 302)
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
        self.assertEqual(response.status_code, 302)
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
        self.assertEqual(response.status_code, 302)
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

    def test_template_tag_position_in_queue(self):
        gpu = self.devices[0].gpus.first()
        other = mommy.make(User)
        other.groups.add(self.group)
        mommy.make(Reservation, gpu=gpu, user=other)
        mommy.make(Reservation, gpu=gpu, user=self.user)

        self.assertEqual(queue_position(gpu, self.user), 1)

    def test_template_tag_position_in_queue_not_reserved(self):
        gpu = self.devices[0].gpus.first()

        self.assertIsNone(queue_position(gpu, self.user))


class TestMessages(WebTest):

    csrf_checks = False

    def setUp(self):
        self.user = mommy.make(User, is_superuser=True, is_staff=True, email="test@example.com")
        self.devices = mommy.make(Device, _quantity=3)
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

        devices = mommy.make(Device, _quantity=3)
        for idx, device in enumerate(devices):
            gpus = mommy.make(GPU, _quantity=2, device=device)
            for gpu in gpus:
                if idx == 1:
                    mommy.make(GPUProcess, gpu=gpu, _quantity=2)
                elif idx == 2:
                    mommy.make(GPUProcess, gpu=gpu)

    def test_process_button_display(self):
        response = self.app.get(reverse("index"), user=self.user)
        self.assertEqual(response.status_code, 200)

        response_body = response.body.decode('utf-8')
        self.assertEqual(response_body.count("disabled"), 2)
        self.assertEqual(response_body.count("1 Process"), 2)
        self.assertEqual(response_body.count("2 Processes"), 2)

    def test_process_overlay(self):
        response = self.app.get(reverse("index"), user=self.user)
        self.assertEqual(response.status_code, 200)

        response_body = response.body.decode('utf-8')
        for i in range(1, GPUProcess.objects.count() + 1):
            self.assertIn('gpu-proc-list-{}'.format(i), response_body)

    def test_process_info_in_response(self):
        response = self.app.get(reverse("index"), user=self.user)
        self.assertEqual(response.status_code, 200)

        response_body = response.body.decode('utf-8')
        for process in GPUProcess.objects.all():
            self.assertIn("PID: {}".format(process.pid), response_body)
            self.assertIn(process.name, response_body)
            self.assertIn("User: {}".format(process.username), response_body)
            self.assertIn("Memory Usage: {}".format(process.memory_usage), response_body)

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
        self.devices = mommy.make(Device, _quantity=3)
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

    def test_gpu_listing(self):
        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[0].name), user=self.user, xhr=True)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[-1].name), user=self.user, expect_errors=True, xhr=True)
        self.assertEqual(response.status_code, 403)

        response = self.app.get(reverse('gpus_for_device') + '?device_name={}'.format(self.devices[-1].name), user=self.staff_user, xhr=True)
        self.assertEqual(response.status_code, 200)

    def test_gpu_info(self):
        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[0].gpus.first().uuid), user=self.user, xhr=True)
        self.assertEqual(response.status_code, 200)

        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[-1].gpus.first().uuid), user=self.user, expect_errors=True, xhr=True)
        self.assertEqual(response.status_code, 403)

        response = self.app.get(reverse('gpu_info') + '?uuid={}'.format(self.devices[-1].gpus.first().uuid), user=self.staff_user, xhr=True)
        self.assertEqual(response.status_code, 200)

    def test_gpu_done(self):
        mommy.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)
        response = self.app.post(reverse("done_with_gpu", args=[self.devices[0].gpus.first().id]), user=self.user)
        self.assertRedirects(response, reverse("index"))

        mommy.make(Reservation, gpu=self.devices[-1].gpus.first(), user=self.staff_user)
        response = self.app.post(
            reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]),
            user=self.user,
            expect_errors=True
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Reservation.objects.count(), 1)

        response = self.app.post(reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]), user=self.staff_user)
        self.assertRedirects(response, reverse("index"))

        response = self.app.get(
            reverse("done_with_gpu", args=[self.devices[-1].gpus.first().id]),
            user=self.user,
            expect_errors=True
        )
        self.assertEqual(response.status_code, 400)

    def test_gpu_cancel(self):
        mommy.make(Reservation, gpu=self.devices[0].gpus.first(), user=self.user)
        response = self.app.post(reverse("cancel_gpu", args=[self.devices[0].gpus.first().id]), user=self.user)
        self.assertRedirects(response, reverse("index"))

        mommy.make(Reservation, gpu=self.devices[-1].gpus.first(), user=self.staff_user)
        response = self.app.post(reverse("cancel_gpu", args=[self.devices[-1].gpus.first().id]), user=self.user,
                                expect_errors=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Reservation.objects.count(), 1)

        response = self.app.post(reverse("cancel_gpu", args=[self.devices[-1].gpus.first().id]), user=self.staff_user)
        self.assertRedirects(response, reverse("index"))

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
        self.device = mommy.make(Device)

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
