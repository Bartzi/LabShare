import random
import string
from unittest import mock

from django.contrib.auth.models import User
from django.core import management
from django.test import TestCase
from model_bakery import baker

from labshare.tests import device_recipe


def get_ldap_users(num_users: int) -> list:
    ldap_data = []
    for user_id in range(num_users):
        user_name = ''.join([random.choice(string.ascii_letters) for _ in range(random.randint(5, 20))])
        user_data = (
            user_name,
            {
                "mail": ["random@random.org"],
                "uid": [user_name],
                "uidnumber": [f'{random.randint(100, 10000)}'],
                "gidnumber": [f'{random.randint(100, 10000)}'],
                "sn": [user_name],
                "cn": [user_name]
            }
        )
        ldap_data.append(user_data)
    return ldap_data


class LDAPSearchMock:

    def __init__(self, num_users):
        self.user_data = get_ldap_users(num_users)
        self.user_dns = [dn for dn, _ in self.user_data]

    def init(self, base_dn, *args, **kwargs):
        self.base_dn = base_dn

    def execute(self, *args, **kwargs):
        if self.base_dn in self.user_dns:
            # we are searching for a specific user
            for user in self.user_data:
                    if user[0] == self.base_dn:
                        return [user]

        if len(args) == 1:
            # we want to get all available users
            return self.user_data
        else:
            # we are looking for a specific user by username and not dn
            filter_args = args[1]
            for user in self.user_data:
                if user[1]['uid'][0] == filter_args['user']:
                    return [user]


class SyncUsersTests(TestCase):

    def setUp(self):
        self.user = baker.make(User)
        self.device = device_recipe.make()

    def check_that_correct_users_are_in_database(self, search_mock):
        imported_users = User.objects.filter(username__in=search_mock.user_dns)
        imported_usernames = [user.username for user in imported_users]
        for user_dn in search_mock.user_dns:
            self.assertIn(user_dn, imported_usernames)

    def test_sync_users_new_users_in_ldap(self):
        num_users_before_update = User.objects.count()
        num_users_to_add = 3
        search_mock = LDAPSearchMock(num_users_to_add)

        with mock.patch('django_auth_ldap.backend.LDAPSearch.execute') as mocked_execute, \
             mock.patch('django_auth_ldap.backend.LDAPSearch.__init__') as mocked_init, \
             mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
            mocked_init.side_effect = search_mock.init
            mocked_execute.side_effect = search_mock.execute
            mocked_bind.return_value = None

            management.call_command('sync_users')

        self.assertEqual(User.objects.count(), num_users_before_update + num_users_to_add)
        self.check_that_correct_users_are_in_database(search_mock)

    def test_sync_users_remove_users_from_ldap(self):
        num_users_before_update = User.objects.count()
        num_users_to_add = 3
        search_mock = LDAPSearchMock(num_users_to_add)

        with mock.patch('django_auth_ldap.backend.LDAPSearch.execute') as mocked_execute, \
             mock.patch('django_auth_ldap.backend.LDAPSearch.__init__') as mocked_init, \
             mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
            mocked_init.side_effect = search_mock.init
            mocked_execute.side_effect = search_mock.execute
            mocked_bind.return_value = None

            management.call_command('sync_users')

            search_mock.user_data.pop()
            search_mock.user_dns.pop()

            management.call_command('sync_users')

        self.assertEqual(User.objects.count(), num_users_before_update + num_users_to_add - 1)
        self.check_that_correct_users_are_in_database(search_mock)

    def test_sync_users_add_and_remove_user_from_ldap(self):
        num_users_before_update = User.objects.count()
        num_users_to_add = 3
        search_mock = LDAPSearchMock(num_users_to_add)

        with mock.patch('django_auth_ldap.backend.LDAPSearch.execute') as mocked_execute, \
             mock.patch('django_auth_ldap.backend.LDAPSearch.__init__') as mocked_init, \
             mock.patch('django_auth_ldap.backend._LDAPUser._bind_as') as mocked_bind:
            mocked_init.side_effect = search_mock.init
            mocked_execute.side_effect = search_mock.execute
            mocked_bind.return_value = None

            management.call_command('sync_users')

            search_mock.user_data.pop()
            search_mock.user_dns.pop()

            new_user = get_ldap_users(1)
            search_mock.user_data.extend(new_user)
            search_mock.user_dns.append(new_user[0][0])

            management.call_command('sync_users')

        self.assertEqual(User.objects.count(), num_users_before_update + num_users_to_add)
        self.check_that_correct_users_are_in_database(search_mock)


