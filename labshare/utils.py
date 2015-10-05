from .models import Device


def get_devices():
    return [(device.name, device.name) for device in Device.objects.all()]
