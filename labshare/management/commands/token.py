import sys

from django.core.management import BaseCommand
from rest_framework.authtoken.models import Token

from labshare.models import Device


class Command(BaseCommand):
    help = "lists the authentication token for the given device"

    def add_arguments(self, parser):
        parser.add_argument("device_name",
                            help="the name of the registered device for which the token should be retrieved")

    def handle(self, *args, **options):
        device_name = options["device_name"]
        try:
            device = Device.objects.get(name=device_name)
        except Device.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"No device with name '{device_name}' is registered."))
            sys.exit(1)

        auth_token = Token.objects.get(user=device.user)
        print(f"{device_name}: {auth_token}")
