from django.core.management import BaseCommand

from labshare.utils import determine_failed_gpus, publish_gpu_states, check_reservations


class Command(BaseCommand):
    help = "Updates reservations and GPU states and sends mails for failed devices based on the latest GPU info"

    def handle(self, *args, **options):
        determine_failed_gpus()
        publish_gpu_states()
        check_reservations()


