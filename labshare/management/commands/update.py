from django.core.management import BaseCommand

from labshare.utils import update_gpu_info, determine_failed_gpus


class Command(BaseCommand):
    help = "queries every connected device for updated info on GPU status"

    def handle(self, *args, **options):
        update_gpu_info()
        determine_failed_gpus()

