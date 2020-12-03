from django.core.management import BaseCommand
from rest_framework.authtoken.models import Token

from labshare.models import Device


class Command(BaseCommand):
    help = "lists the authentication tokens for all registered devices"

    def handle(self, *args, **options):
        auth_tokens = {}
        for device in Device.objects.all():
            token = Token.objects.get(user=device.user)
            auth_tokens[device.name] = token

        if len(auth_tokens) > 0:
            print("The following devices and tokens are registered:")
            print("\n".join([f"{name}: {token}" for name, token in auth_tokens.items()]))
        else:
            print("No devices or tokens registered.")

