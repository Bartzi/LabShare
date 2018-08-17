from django import forms
from django.contrib.auth.models import User
from django.forms import SelectMultiple

from .utils import get_devices


class DeviceSelectForm(forms.Form):
    device = forms.ChoiceField(choices=get_devices)
    gpu = forms.CharField()
    next_available_spot = forms.HiddenInput()

    def __init__(self, *args, **kwargs):
        devices = kwargs.pop('devices')
        super(DeviceSelectForm, self).__init__(*args, **kwargs)
        self.fields['device'].choices = [(device.name, device.name) for device in devices]


class MessageForm(forms.Form):
    message_all_users = forms.BooleanField(required=False)
    recipients = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        required=False,
        widget=SelectMultiple(attrs={"id": "recipients-field", "multiple": "multiple"})
    )
    subject = forms.CharField(required=True)
    message = forms.CharField(widget=forms.Textarea, required=True)
