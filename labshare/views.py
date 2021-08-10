import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.core.mail import EmailMessage
from django.http import HttpResponseRedirect, HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated

from labshare.decorators import render_to
from labshare.utils import login_required_ajax, publish_device_state
from .forms import MessageForm, ViewAsForm
from .models import Device, GPU


@ensure_csrf_cookie
@render_to("overview.html")
def index(request):
    devices = list(filter(lambda device: device.can_be_used_by(request.user), Device.objects.all()))
    sorted_devices = sorted(devices, key=lambda x: x.name)
    return {"devices": sorted_devices}


@api_view(['POST'])
@authentication_classes((TokenAuthentication,))
@permission_classes((IsAuthenticated,))
def update_gpu_info(request):
    if request.method != "POST":
        raise SuspiciousOperation

    data = json.loads(request.read().decode("utf-8"))
    device_name = data["device_name"]
    device = Device.objects.get(name=device_name)  # Device should exist because it's authorized

    gpus = []
    for gpu_data in data["gpu_data"]:
        uuid = gpu_data['uuid']
        gpu, created = GPU.objects.get_or_create(uuid=uuid, model_name=gpu_data['name'], device=device)
        gpu_in_use = True if gpu_data.get("in_use", "na") == "yes" else False

        processes = []
        if gpu_in_use:
            # push processes if this is supported by the GPU
            for process in gpu_data.get('processes', []):
                processes.append({
                    "name": process.get("name", "Unknown"),
                    "pid": int(process.get("pid", "0")),
                    "memory_usage": process.get("used_memory", "Unknown"),
                    "username": process.get("username", "Unknown"),
                })
        serialized_gpu = gpu.serialize()
        gpu = {
            "used_memory": gpu_data["memory"]["used"],
            "total_memory": gpu_data["memory"]["total"],
            "utilization": gpu_data["gpu_util"],
            "in_use": gpu_in_use,
            "marked_as_failed": False,
            "processes": processes,
        }
        gpu.update(serialized_gpu)
        gpus.append(gpu)

    device_data = device.serialize()
    device_data["gpus"] = gpus
    publish_device_state(device_data)

    return HttpResponse()


@login_required
@render_to("send_message.html")
def send_message(request):
    form = MessageForm(request.POST or None)
    if form.is_valid():
        sender = request.user
        sender_addresses = [address.email for address in sender.email_addresses.all()]
        sender_addresses.append(sender.email)

        bcc_addresses = []
        if form.cleaned_data.get('message_all_users'):
            if not request.user.is_staff:
                raise SuspiciousOperation
            users = User.objects.exclude(id=sender.id)
            bcc_addresses = [user.email for user in users]
            bcc_addresses.extend([address.email for user in users for address in user.email_addresses.all()])
            email_addresses = [sender.email]
        else:
            recipients = form.cleaned_data.get('recipients')
            if len(recipients) == 0:
                form.add_error('recipients', "Please select at least one recipient")
                return {"form": form}
            recipients = recipients.all()
            email_addresses = [address.email for recipient in recipients for address in recipient.email_addresses.all()]
            email_addresses.extend([recipient.email for recipient in recipients])

        subject = form.cleaned_data.get('subject')
        message = form.cleaned_data.get('message')

        email = EmailMessage(
            subject="[Labshare] {}".format(subject),
            body=message,
            from_email=sender.email,
            to=email_addresses,
            bcc=bcc_addresses,
            cc=sender_addresses,
        )
        email.send()

        messages.success(request, "Message sent!")
        return HttpResponseRedirect(reverse("index"))

    return {"form": form}


@login_required
@render_to("view_as.html")
def view_as(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    form = ViewAsForm()
    return {"form": form}
