from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from datetime import timedelta


class Device(models.Model):
    name = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField()

    class Meta:
        permissions = (
            ('use_device', 'User/Group is allowed to use that device'),
        )

    def __str__(self):
        return self.name

    def can_be_used_by(self, user):
        content_type = ContentType.objects.get_for_model(self)
        permission_name = "{app}.use_{model}".format(app=content_type.app_label, model=content_type.model)
        return user.has_perm(permission_name, self) or user.has_perm(permission_name)


class GPU(models.Model):
    uuid = models.CharField(unique=True, max_length=255)
    device = models.ForeignKey(Device, related_name="gpus", on_delete=models.CASCADE)
    last_updated = models.DateTimeField(auto_now=True)
    model_name = models.CharField(max_length=255)
    used_memory = models.CharField(max_length=100)
    total_memory = models.CharField(max_length=100)
    in_use = models.BooleanField(default=False)
    marked_as_failed = models.BooleanField(default=False)

    def __str__(self):
        return self.model_name

    def last_update_too_long_ago(self):
        return self.last_updated < timezone.now() - timedelta(minutes=30)

    def current_reservation(self):
        try:
            return self.reservations.earliest("time_reserved")
        except Reservation.DoesNotExist as e:
            return None

    def last_reservation(self):
        try:
            return self.reservations.latest("time_reserved")
        except Reservation.DoesNotExist as e:
            return None


class GPUProcess(models.Model):
    gpu = models.ForeignKey(GPU, related_name="processes", on_delete=models.CASCADE)
    name = models.CharField(max_length=511, blank=True)
    pid = models.PositiveIntegerField()
    memory_usage = models.CharField(max_length=100, blank=True)
    username = models.CharField(max_length=250, blank=True)

    def __str__(self):
        return "{process} (by {username}) running on {gpu} (using {memory})".format(
            process=self.name,
            username=self.username,
            gpu=self.gpu,
            memory=self.memory_usage,
        )


class Reservation(models.Model):
    gpu = models.ForeignKey(GPU, related_name="reservations", on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name="reservations", on_delete=models.CASCADE)
    time_reserved = models.DateTimeField(auto_now_add=True)
    user_reserved_next_available_spot = models.BooleanField(default=False)

    def __str__(self):
        return "{gpu} on {device}, {user}".format(device=self.gpu.device, gpu=self.gpu, user=self.user)


class EmailAddress(models.Model):
    user = models.ForeignKey(User, related_name="email_addresses", on_delete=models.CASCADE)
    email = models.EmailField(max_length=255)

    def __str__(self):
        return "{}: {}".format(self.user, self.email)
