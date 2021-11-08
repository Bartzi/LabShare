import ldap
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import BaseCommand
from django_auth_ldap.backend import _LDAPUser
from django_auth_ldap.config import LDAPSearch

from labshare.backends.authentication.ldap import LDAPBackend


class Command(BaseCommand):
    help = "Syncs current user database with all known users in the LDAP Database"

    def process_user_list(self, ldap_users: list) -> list:
        # convert list of all ldap uids to a list of usernames if the user has a valid mail address
        converted_users = []
        for dn, user_data in ldap_users:
            if 'mail' in user_data:
                converted_users.append(user_data['uid'][0])

        return converted_users

    def filter_django_users(self, users: list) -> list:
        return [user for user in users if len(user.email) > 0 and not hasattr(user, 'device')]

    def handle(self, *args, **options):
        ldap_backend = LDAPBackend()
        dummy_user = _LDAPUser(ldap_backend, username="dummy")
        user_search = LDAPSearch(settings.AUTH_LDAP_USER_DN, ldap.SCOPE_SUBTREE, "(uid=*)")

        user_data = user_search.execute(dummy_user.connection)
        list_of_current_ldap_users = self.process_user_list(user_data)

        known_users = self.filter_django_users(User.objects.all())
        num_deleted_users = 0
        num_imported_users = 0

        for user in known_users:
            if user.username in list_of_current_ldap_users:
                index = list_of_current_ldap_users.index(user.username)
                list_of_current_ldap_users.pop(index)
            else:
                if not user.is_superuser:
                    user.delete()
                    num_deleted_users += 1

        # if there are any usernames left, we need to add new users to the database
        for username in list_of_current_ldap_users:
            django_user = ldap_backend.populate_user(username)
            if django_user is None:
                self.stderr.write(self.style.ERROR(f"Could not create user with username {username}"))
                continue

            ldap_backend.update_mail_addresses(django_user)
            ldap_backend.set_groups_of_user(django_user)

            num_imported_users += 1

        self.stdout.write(self.style.SUCCESS("Import Complete"))
        self.stdout.write(self.style.SUCCESS(f"Imported {num_imported_users} users into the database."))
        self.stdout.write(self.style.SUCCESS(f"Deleted {num_deleted_users} users from the database."))
