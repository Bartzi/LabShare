from django import forms
from django.contrib.auth.models import User
from django.forms import SelectMultiple, Select


class MessageForm(forms.Form):
    message_all_users = forms.BooleanField(required=False)
    recipients = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        required=False,
        widget=SelectMultiple(attrs={"id": "recipients-field", "multiple": "multiple"})
    )
    subject = forms.CharField(required=True)
    message = forms.CharField(widget=forms.Textarea, required=True)


class ViewAsForm(forms.Form):
    username = forms.ModelChoiceField(
        queryset=User.objects.all(),
        empty_label="Select a user",
        required=True,
        widget=Select(attrs={"id": "username-field"})
    )
