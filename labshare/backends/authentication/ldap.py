from django.conf import settings
from django.contrib.auth.models import Group
from django_auth_ldap.backend import LDAPBackend as DjangoLDAPBackend

from labshare.models import EmailAddress


class LDAPBackend(DjangoLDAPBackend):

    def update_mail_addresses(self, user):
        all_saved_email_addresses = [user.email] + [address.email for address in user.email_addresses.all()]
        all_ldap_email_addresses = user.ldap_user.attrs.get(user.ldap_user.settings.USER_ATTR_MAP['email'], [])

        all_saved_email_addresses.sort()
        all_ldap_email_addresses.sort()
        if all_saved_email_addresses != all_ldap_email_addresses:
            # something changed and we need to change all addresses
            user.email = all_ldap_email_addresses[0]
            user.save()

            # first: delete all current addresses and then start over
            EmailAddress.objects.filter(user=user).delete()

            # second: create extra email address objects for user
            for address in all_ldap_email_addresses[1:]:
                email = EmailAddress.objects.create(user=user, email=address)
                email.save()

    def set_groups_of_user(self, user):
        user_groups = user.ldap_user.group_dns
        ldap_user_groups = Group.objects.filter(
            name__in=[settings.AUTH_LDAP_GROUP_MAP.get(group_name, None) for group_name in user_groups]
        )
        user_groups = user.groups.all()

        if ldap_user_groups.count() == 0:
            # we need the default group!
            ldap_user_groups = Group.objects.filter(name=settings.AUTH_LDAP_DEFAULT_GROUP_NAME)

        if ldap_user_groups.difference(user_groups).count() == 0:
            # all groups are already correct
            return

        # we set the groups of the current to the group names provided by LDAP
        user.groups.clear()
        user.save()
        for group in ldap_user_groups:
            group.user_set.add(user)
            group.save()

    def authenticate_ldap_user(self, ldap_user, password):
        user = super().authenticate_ldap_user(ldap_user, password)
        if user is None:
            return None

        self.update_mail_addresses(user)
        self.set_groups_of_user(user)

        return user
