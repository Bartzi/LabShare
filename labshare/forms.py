from django import forms

from .utils import get_devices


class DeviceSelectForm(forms.Form):
    device = forms.ChoiceField(choices=get_devices)
    gpu = forms.CharField()
    next_available_spot = forms.HiddenInput()
