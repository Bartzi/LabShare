import copy
import datetime
import io
import json
import os
import random
import string
import time
import unittest.mock as mock
import uuid
from datetime import timedelta
from unittest import skipIf
from unittest.mock import Mock

import requests
from channels.layers import get_channel_layer
from channels.testing import ChannelsLiveServerTestCase, WebsocketCommunicator
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
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from labshare.consumers import GPUInfoUpdater
from labshare.models import Device, EmailAddress
from labshare.routing import application
from labshare.templatetags.icon import icon
from labshare.utils import get_devices, publish_device_state

device_recipe = Recipe(
    Device,
    name=lambda: ''.join(
        random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(16))
)


def utc_now():
    return datetime.datetime.now(tz=datetime.timezone.utc)


class LabshareTestSetup(WebTest):
    csrf_checks = False

    @classmethod
    def setUpTestData(cls):
        cls.user = baker.make(User, email="user@example.com")
        cls.devices = device_recipe.make(_quantity=3)

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

    def test_get_devices(self):
        device_info = get_devices()
        for device, device_info in zip(Device.objects.all(), device_info):
            self.assertEqual((device.name, device.name), device_info)

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


class TestMessages(WebTest):
    csrf_checks = False

    def setUp(self):
        self.user = baker.make(User, is_superuser=True, is_staff=True, email="test@example.com")
        self.devices = device_recipe.make(_quantity=3)

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


class LabSharePermissionTests(WebTest):
    csrf_checks = False

    def setUp(self):
        self.staff_user = baker.make(User)
        self.user = baker.make(User)
        self.devices = device_recipe.make(_quantity=3)

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


class EmailAddressTests(WebTest):

    def setUp(self):
        user = baker.make(User)
        baker.make(EmailAddress, _quantity=5, user=user)

    def test_stringify_email_address(self):
        for address in EmailAddress.objects.all():
            address_info = "{}: {}".format(address.user, address.email)
            self.assertEqual(str(address), address_info)


def get_bare_gpu_template():
    return {
        "name": "Test GPU",
        "model_name": "NVIDIA Super GPU",
        "uuid": "lorem",
        "in_use": "no",
        "processes": [],
        "gpu_util": "25 %",
    }


def get_gpu_template():
    template = get_bare_gpu_template()
    template['memory'] = {
        "total": "100 MB",
        "used": "20 MB",
        "free": "80 MB",
    }
    return template


def get_parsed_gpu_template():
    template = get_bare_gpu_template()
    memory = {
        "used_memory": "20 MiB",
        "total_memory": "100 MiB",
    }
    template.update(memory)
    return template


def working_gpu_data_with_one_gpu_not_in_use():
    return [
        get_gpu_template()
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
        response = self.client.post(self.url,
                                    request_data(self.device.name, working_gpu_data_with_one_gpu_not_in_use),
                                    format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


admin_mail = "test@example.com"


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

    @mock.patch('labshare.utils.async_to_sync', async_to_sync_mock)
    def test_publish_device_state_without_channel_name(self):
        device = self.device.serialize()
        publish_device_state(device)
        device.update({"gpus": []})
        data = {
            'type': 'update_info',
            'message': json.dumps(device),
        }

        send_function_mock.assert_called_with(self.device.name, data)

    @mock.patch('labshare.utils.async_to_sync', async_to_sync_mock)
    def test_publish_device_state_with_channel_name(self):
        channel_name = "kekse"
        device = self.device.serialize()
        publish_device_state(device, channel_name=channel_name)
        device.update({"gpus": []})
        data = {
            'type': 'update_info',
            'message': json.dumps(device),
        }

        send_function_mock.assert_called_with(channel_name, data)


class ConsumerTests(TestCase):

    def setUp(self):
        self.device = device_recipe.make()
        self.device_2 = device_recipe.make()
        self.user = baker.make(User)
        assign_perm('labshare.use_device', self.user, self.device)

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
        chrome_option_list = ["disable-gpu", "no-sandbox", "disable-dev-shm-usage"]
        if os.environ.get("GITHUB_ACTIONS", "false") == "true":
            chrome_option_list.append("headless")
        for option in chrome_option_list:
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
        assign_perm('labshare.use_device', cls.user, cls.device_1)

        cls.device_2 = device_recipe.make()

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

    def publish_device_states(self, gpu_data=None):
        if gpu_data is None:
            # we do not care about the GPU data of our devices
            for device in Device.objects.all():
                gpus = []
                for _ in range(random.randint(0, 2)):
                    gpu = get_gpu_template()
                    gpu['uuid'] = uuid.uuid4().hex
                    gpus.append(gpu)
                device_data = {
                    "name": device.name,
                    "gpus": gpus
                }
                publish_device_state(device_data)
        else:
            for data in gpu_data:
                publish_device_state(data)

    def wait_for_page_load(self, gpu_data=None, open_dropdowns=True):
        WebDriverWait(self.driver, 2).until(EC.presence_of_element_located((By.CLASS_NAME, "border-success")))
        self.publish_device_states(gpu_data)
        WebDriverWait(self.driver, 2).until(EC.presence_of_element_located((By.CLASS_NAME, "gpu-row")))
        if open_dropdowns:
            self.open_device_dropdowns()
            time.sleep(1)

    def open_device_dropdowns(self):
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

    def build_gpus_for_device(self, device, min_num_gpus=0, max_num_gpus=2, num_processes=0):
        gpus = []
        for _ in range(random.randint(min_num_gpus, max_num_gpus)):
            gpu = get_parsed_gpu_template()
            gpu['uuid'] = uuid.uuid4().hex
            processes = []
            for i in range(num_processes):
                processes.append({
                    "pid": i,
                    "username": "Mr. Keks",
                    "name": "TestProcess",
                    "memory_usage": "10 MB",
                })
            gpu['processes'] = processes
            gpu['utilization'] = gpu['gpu_util']
            del gpu['gpu_util']
            gpus.append(gpu)
        return {
            'name': device.name,
            'gpus': gpus
        }


@skipIf("GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true", "Skipping this test on Github Actions.")
class FrontendOverviewNonSuperuserTest(FrontendTestsBase):

    def setUp(self):
        self.login_user(self.user.username)

    def test_overview_table_populated_with_device_data_for_non_superuser(self):
        self.driver.find_element_by_id(f"{self.device_1.name}-gpu-table")
        self.assertRaises(NoSuchElementException, self.driver.find_element_by_id, f"{self.device_2.name}-gpu-table")


@skipIf("GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true", "Skipping this test on Github Actions.")
class FrontendOverviewGPUViewTest(FrontendTestsBase):

    def test_overview_gpu_data_correctly_appears(self):
        device_1_data = self.build_gpus_for_device(self.device_1, 1, 1, 0)
        device_2_data = self.build_gpus_for_device(self.device_2, 2, 2, 3)

        self.wait_for_page_load([device_1_data, device_2_data])

        gpu_rows = self.driver.find_elements_by_class_name("gpu-row")
        self.assertEqual(len(gpu_rows), 3)

        for device in Device.objects.all():
            device_table = self.driver.find_element_by_id(f"{device.name}-gpu-table")
            device_data = device_1_data if device_1_data['name'] == device.name else device_2_data
            gpu_rows = device_table.find_elements_by_class_name("gpu-row")
            self.assertEqual(len(device_data['gpus']), len(gpu_rows))


@skipIf("GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true", "Skipping this test on Github Actions.")
class FrontendOverviewGPUUpdateTest(FrontendTestsBase):

    def test_overview_gpu_data_changes(self):
        def check_data(gpu_data):
            for gpu in gpu_data:
                gpu_row = self.driver.find_element_by_id(gpu['uuid'])
                print(gpu_row)
                for element_name, key in [('gpu-model-name', 'model_name'), ('gpu-utilization', 'utilization')]:
                    element = gpu_row.find_element_by_class_name(element_name)
                    print(f"element: {element.text}, GPU: {gpu[key]}")
                    self.assertIn(gpu[key], element.text)

                memory_element = gpu_row.find_element_by_class_name('gpu-memory')
                self.assertIn(gpu['used_memory'], memory_element.text)
                self.assertIn(gpu['total_memory'], memory_element.text)

        device_1_data = self.build_gpus_for_device(self.device_1, 2, 2, 0)
        self.wait_for_page_load([device_1_data])

        check_data(device_1_data['gpus'])

        adjusted_gpu_data = []
        gpu_data = device_1_data['gpus']
        for gpu in gpu_data:
            new_gpu_data = copy.copy(gpu)
            new_gpu_data['utilization'] = f"{random.randint(0, 100)} %"
            new_gpu_data['in_use'] = random.choice(['yes', 'no'])
            new_gpu_data["used_memory"] = f"{random.randint(0, 100)} MB"
            new_gpu_data["free_memory"] = f"{random.randint(0, 100)} MB"
            adjusted_gpu_data.append(new_gpu_data)

        device_1_data['gpus'] = adjusted_gpu_data
        self.wait_for_page_load([device_1_data], open_dropdowns=False)
        check_data(device_1_data['gpus'])


@skipIf("GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true", "Skipping this test on Github Actions.")
class FrontendOverviewProcessListTest(FrontendTestsBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.driver.implicitly_wait(1)

    def test_process_overview(self):
        device_1_data = self.build_gpus_for_device(self.device_1, 1, 1, 0)
        device_2_data = self.build_gpus_for_device(self.device_2, 1, 2, 3)

        self.wait_for_page_load([device_1_data, device_2_data])
        self.assertRaises(NoSuchElementException, self.driver.find_element_by_id, "full-process-list")

        gpu = device_2_data['gpus'][0]
        gpu_row = self.driver.find_element_by_id(gpu['uuid'])
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

        for process_detail, process in zip(process_details, gpu['processes']):
            process_name = process_detail.find_element_by_class_name("card-header").text
            self.assertIn(process_name, process['name'])

            pid_text = process_detail.find_elements_by_class_name("list-group-item")[0].text
            self.assertIn(str(process['pid']), pid_text)

            user_text = process_detail.find_elements_by_class_name("list-group-item")[1].text
            self.assertIn(process['username'], user_text)

            memory_text = process_detail.find_elements_by_class_name("list-group-item")[2].text
            self.assertIn(process['memory_usage'], memory_text)
