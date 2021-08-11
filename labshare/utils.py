import json

import channels.layers
from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpResponse
from django.shortcuts import render
from django.template import loader

from .models import Device


def get_devices():
    return [(device.name, device.name) for device in Device.objects.all()]


def publish_device_state(device_data, channel_name=None):
    channel_layer = channels.layers.get_channel_layer()
    name = device_data['name']
    if 'gpus' not in device_data:
        device_data['gpus'] = []

    if channel_name is None:
        send_function = async_to_sync(channel_layer.group_send)
    else:
        send_function = async_to_sync(channel_layer.send)
    send_function(channel_name if channel_name else name, {'type': 'update_info', 'message': json.dumps(device_data)})
