import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, SuspiciousOperation
from django.core.mail import EmailMessage
from django.http import HttpResponseRedirect, HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated

from labshare.decorators import render_to
from labshare.utils import send_reservation_mail_for, send_gpu_done_mail, login_required_ajax, publish_device_state, \
    delete_reservation
from .forms import DeviceSelectForm, MessageForm, ViewAsForm
from .models import Device, Reservation, GPU, GPUProcess


@ensure_csrf_cookie
@render_to("overview.html")
def index(request):
    devices = list(filter(lambda device: device.can_be_used_by(request.user), Device.objects.all()))
    return {"devices": devices}


@login_required
@render_to("reserve.html")
def reserve(request):
    accessible_devices = filter(lambda device: device.can_be_used_by(request.user), Device.objects.all())

    form = DeviceSelectForm(request.POST or None, devices=accessible_devices)
    if form.is_valid():
        device = get_object_or_404(Device, name=form.data["device"])
        if json.loads(form.data.get("next-available-spot", "false")):
            if not device.can_be_used_by(request.user):
                raise PermissionDenied
            # first check whether a gpu is already available on that given machine
            for gpu in device.gpus.all():
                if gpu.reservations.count() is 0:
                    reservation = Reservation(gpu=gpu, user=request.user)
                    reservation.start_usage(save=False)
                    reservation.save()
                    send_gpu_done_mail(gpu, reservation)
                    return HttpResponseRedirect(reverse("index"))

            # if there is no gpu available right now reserve all on this device and mark them as special reservation
            for gpu in device.gpus.all():
                reservation = Reservation(gpu=gpu, user=request.user, user_reserved_next_available_spot=True)
                reservation.save()
                send_reservation_mail_for(request, gpu)
        else:
            gpu = get_object_or_404(GPU, uuid=form.data["gpu"])
            if not gpu.device.can_be_used_by(request.user):
                raise PermissionDenied
            start_usage = gpu.reservations.count() is 0
            reservation = Reservation(gpu=gpu, user=request.user)
            if start_usage:
                reservation.start_usage(save=False)
            reservation.save()

            send_reservation_mail_for(request, gpu)

        # notify our users of this change for this device
        publish_device_state(device)

        if request.is_ajax():
            return HttpResponse()
        return HttpResponseRedirect(reverse("index"))

    return {"form": form}


@login_required_ajax
def gpus(request):
    if request.method != "GET" or not request.is_ajax():
        raise SuspiciousOperation

    device_name = request.GET.get('device_name', None)
    if device_name is None:
        raise Http404

    device = get_object_or_404(Device, name=device_name)
    if not device.can_be_used_by(request.user):
        raise PermissionDenied

    return_data = {
        'gpus': [{
            "id": gpu.uuid,
            "name": gpu.model_name} for gpu in device.gpus.all()]
    }
    return HttpResponse(json.dumps(return_data, indent=4))


@login_required_ajax
def gpu_info(request):
    if request.method != "GET" or not request.is_ajax():
        raise SuspiciousOperation

    uuid = request.GET.get('uuid', None)
    if uuid is None:
        raise Http404

    gpu = get_object_or_404(GPU, uuid=uuid)

    if not gpu.device.can_be_used_by(request.user):
        raise PermissionDenied

    current_reservation = gpu.current_reservation()

    return_data = {
        "used": gpu.used_memory,
        "total": gpu.total_memory,
        "user": current_reservation.user.username if current_reservation is not None else "No current user",
    }

    return HttpResponse(json.dumps(return_data, indent=4))


@login_required
def gpu_done(request, gpu_id):
    gpu = get_object_or_404(GPU, pk=gpu_id)

    if request.method != "POST":
        raise SuspiciousOperation

    if not gpu.device.can_be_used_by(request.user):
        raise PermissionDenied

    current_reservation = gpu.current_reservation()

    if current_reservation is None:
        raise Http404

    if current_reservation.user != request.user:
        raise PermissionDenied

    delete_reservation(current_reservation)

    return HttpResponse()


@login_required
def gpu_extend(request, gpu_id):
    gpu = get_object_or_404(GPU, pk=gpu_id)

    if request.method != "POST":
        raise SuspiciousOperation

    if not gpu.device.can_be_used_by(request.user):
        raise PermissionDenied

    current_reservation = gpu.current_reservation()

    if current_reservation is None:
        raise Http404

    if current_reservation.user != request.user:
        raise PermissionDenied

    if not current_reservation.extend():
        raise SuspiciousOperation

    publish_device_state(current_reservation.gpu.device)

    return HttpResponse()


@login_required
def gpu_cancel(request, gpu_id):
    gpu = get_object_or_404(GPU, pk=gpu_id)
    if request.method != "POST":
        raise SuspiciousOperation

    if not gpu.device.can_be_used_by(request.user):
        raise PermissionDenied

    try:
        reservation = gpu.reservations.filter(user=request.user).latest("time_reserved")
        if reservation == gpu.get_current_reservation():
            raise SuspiciousOperation
        reservation.delete()
        publish_device_state(gpu.device)
    except ObjectDoesNotExist as e:
        raise Http404

    return HttpResponse()


@api_view(['POST'])
@authentication_classes((TokenAuthentication,))
@permission_classes((IsAuthenticated,))
def update_gpu_info(request):
    # TODO: validate with mock data that contain processes
    if request.method != "POST":
        raise SuspiciousOperation

    data = json.loads(request.read().decode("utf-8"))
    device_name = data["device_name"]
    device = Device.objects.get(name=device_name)  # Device should exist because it's authorized

    for gpu_data in data["gpu_data"]:
        gpu = GPU.objects.filter(device=device, uuid=gpu_data["uuid"])

        gpu_in_use = True if gpu_data.get("in_use", "na") == "yes" else False
        if gpu_data.get("in_use", "na") == "na":
            # assume that device is in use if more than 800 MiB of video ram are in use
            gpu_in_use = int(gpu_data["memory"]["used"].split()[0]) > 800

        if not gpu.exists():
            gpu = GPU(
                device=device,
                model_name=gpu_data["name"],
                uuid=gpu_data["uuid"],
                used_memory=gpu_data["memory"]["used"],
                total_memory=gpu_data["memory"]["total"],
                utilization=gpu_data["gpu_util"],
                in_use=gpu_in_use,
            )
        else:
            gpu = gpu.get()
            memory_info = gpu_data["memory"]
            gpu.used_memory = memory_info["used"]
            gpu.total_memory = memory_info["total"]
            gpu.in_use = gpu_in_use
            gpu.marked_as_failed = False
        gpu.save()

        gpu.processes.all().delete()
        if gpu_in_use:
            # save processes if this is supported by the GPU
            for process in gpu_data.get('processes', []):
                GPUProcess(
                    gpu=gpu,
                    name=process.get("name", "Unknown"),
                    pid=int(process.get("pid", "0")),
                    memory_usage=process.get("used_memory", "Unknown"),
                    username=process.get("username", "Unknown"),
                ).save()

        publish_device_state(device)

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
