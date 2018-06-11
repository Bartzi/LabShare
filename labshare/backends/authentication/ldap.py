from django_auth_ldap.backend import LDAPBackend as DjangoLDAPBackend

from labshare.models import EmailAddress


class LDAPBackend(DjangoLDAPBackend):

    def authenticate_ldap_user(self, ldap_user, password):
        user = super().authenticate_ldap_user(ldap_user, password)
        if user is None:
            return None

        # because we can have multiple email addresses per user we need to store
        email_addresses = ldap_user.attrs[ldap_user.settings.USER_ATTR_MAP['email']]
        if len(email_addresses) > 1 and user.email_addresses.count() == 0:
            for address in email_addresses[1:]:
                # create extra email address objects for user
                email = EmailAddress.objects.create(user=user, email=address)
                email.save()

        return user
