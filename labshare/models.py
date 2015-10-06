from django.contrib.auth.models import User
from django.db import models


class Device(models.Model):
    name = models.CharField(max_length=255)
    uuid = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField()

    def __str__(self):
        return self.name


class GPU(models.Model):
    device = models.ForeignKey(Device, related_name="gpus")
    model_name = models.CharField(max_length=255)
    free_memory = models.CharField(max_length=100)
    used_memory = models.CharField(max_length=100)
    total_memory = models.CharField(max_length=100)

    def __str__(self):
        return self.model_name


class Reservation(models.Model):
    gpu = models.ForeignKey(GPU, related_name="reservations")
    user = models.ForeignKey(User, related_name="reservations")