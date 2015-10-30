from django.contrib.auth.models import User
from django.db import models


class Device(models.Model):
    name = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField()

    def __str__(self):
        return self.name


class GPU(models.Model):
    uuid = models.CharField(unique=True, max_length=255)
    device = models.ForeignKey(Device, related_name="gpus")
    last_updated = models.DateTimeField(auto_now=True)
    model_name = models.CharField(max_length=255)
    free_memory = models.CharField(max_length=100)
    used_memory = models.CharField(max_length=100)
    total_memory = models.CharField(max_length=100)

    def __str__(self):
        return self.model_name

    def in_use(self):
        used_mem = int(self.used_memory.split()[0])
        # device is in use if more than 800 MiB of video ram are in use
        return True if used_mem > 800 else False


class Reservation(models.Model):
    gpu = models.ForeignKey(GPU, related_name="reservations")
    user = models.ForeignKey(User, related_name="reservations")
    time_reserved = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return "{gpu} on {device}, {user}".format(device=self.gpu.device, gpu=self.gpu, user=self.user)