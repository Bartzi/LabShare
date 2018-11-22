from django_auth_ldap.backend import LDAPBackend as DjangoLDAPBackend

from labshare.models import EmailAddress


class LDAPBackend(DjangoLDAPBackend):

    def authenticate_ldap_user(self, ldap_user, password):
        user = super().authenticate_ldap_user(ldap_user, password)
        if user is None:
            return None

        # because we can have multiple email addresses per user we need to store them in a different object
        email_addresses = ldap_user.attrs[ldap_user.settings.USER_ATTR_MAP['email']]
        if len(email_addresses) != user.email_addresses.count() + 1:
            # if the first address changed in LDAP, we also have to change it here
            if user.email != email_addresses[0]:
                user.email = email_addresses[0]
                user.save()

            # first: delete all current addresses and then start over
            current_addresses = EmailAddress.objects.filter(user=user)
            for address in current_addresses:
                address.delete()

            # second: create extra email address objects for user
            for address in email_addresses[1:]:
                email = EmailAddress.objects.create(user=user, email=address)
                email.save()

        return user
