from urllib.error import URLError
from django.core.management import BaseCommand
from labshare.models import Device, GPU

import urllib.request
import json


class Command(BaseCommand):
    help = "queries every connected device for updated info on GPU status"

    def handle(self, *args, **options):
        for device in Device.objects.all():
            try:
                ip_address = device.ip_address
                response = urllib.request.urlopen("http://{}:8080".format(ip_address), timeout=10).read().decode('utf-8')
                gpus = json.loads(response)
                for gpu_data in gpus:
                    gpu = GPU.objects.filter(device=device, model_name=gpu_data["name"])
                    if not gpu.exists():
                        gpu = GPU(
                            device=device,
                            model_name=gpu_data["name"],
                            free_memory=gpu_data["memory"]["free"],
                            used_memory=gpu_data["memory"]["used"],
                            total_memory=gpu_data["memory"]["total"],
                        )
                    else:
                        gpu = gpu.get()
                        memory_info = gpu_data["memory"]
                        gpu.free_memory = memory_info["free"]
                        gpu.used_memory = memory_info["used"]
                        gpu.total_memory = memory_info["total"]
                    gpu.save()
            except URLError:
                pass
            except Exception as e:
                self.stderr.write(e, ending='')