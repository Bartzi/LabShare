import pytz

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from rest_framework.authtoken.models import Token


class Device(models.Model):
    name = models.CharField(max_length=255)
    user = models.OneToOneField(User, on_delete=models.CASCADE)

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

    def serialize(self):
        return {
            'name': self.name,
        }


class GPU(models.Model):
    uuid = models.CharField(max_length=255)
    model_name = models.CharField(max_length=255)
    reserved = models.BooleanField(default=False)
    device = models.ForeignKey(Device, related_name='gpus', on_delete=models.CASCADE)

    def serialize(self):
        return {
            'uuid': self.uuid,
            'model_name': self.model_name,
            'reserved': self.reserved
        }


@receiver(post_save, sender=Device)
def save_device_user(sender, instance, **kwargs):
    instance.user.save()


@receiver(post_save, sender=Device)
def create_device_auth_token(sender, instance=None, created=False, **kwargs):
    if created:
        token = Token.objects.create(user=instance.user)
        token.save()


class EmailAddress(models.Model):
    user = models.ForeignKey(User, related_name="email_addresses", on_delete=models.CASCADE)
    email = models.EmailField(max_length=255)

    def __str__(self):
        return "{}: {}".format(self.user, self.email)
