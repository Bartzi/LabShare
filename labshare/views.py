import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, SuspiciousOperation
from django.core.mail import EmailMessage
from django.http import HttpResponseRedirect, HttpResponseBadRequest, HttpResponse, HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from .forms import DeviceSelectForm, MessageForm, ViewAsForm
from labshare.utils import send_reservation_mail_for, send_gpu_done_mail, login_required_ajax
from .models import Device, Reservation, GPU
from labshare.decorators import render_to


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
        if json.loads(form.data["next-available-spot"]):
            device = Device.objects.get(name=form.data["device"])
            if not device.can_be_used_by(request.user):
                raise PermissionDenied
            # first check whether a gpu is already available on that given machine
            for gpu in device.gpus.all():
                if gpu.reservations.count() is 0:
                    reservation = Reservation(gpu=gpu, user=request.user)
                    reservation.save()
                    send_gpu_done_mail(request, gpu, reservation)
                    return HttpResponseRedirect(reverse("index"))

            # if there is no gpu available right now reserve all on this device and mark them as special reservation
            for gpu in device.gpus.all():
                reservation = Reservation(gpu=gpu, user=request.user, user_reserved_next_available_spot=True)
                reservation.save()
                send_reservation_mail_for(request, gpu)
        else:
            gpu = GPU.objects.get(uuid=form.data["gpu"])
            if not gpu.device.can_be_used_by(request.user):
                raise PermissionDenied
            reservation = Reservation(gpu=gpu, user=request.user)
            reservation.save()

            send_reservation_mail_for(request, gpu)

        return HttpResponseRedirect(reverse("index"))

    return {"form": form}


@login_required_ajax
def gpus(request):
    if request.method != "GET" or not request.is_ajax():
        raise SuspiciousOperation

    device_name = request.GET.get('device_name', None)
    if device_name is None:
        raise Http404

    device = Device.objects.get(name=device_name)
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

    gpu = GPU.objects.get(uuid=uuid)

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

    current_reservation.delete()

    # get the user of the reservation that is now current and send him an email
    current_reservation = gpu.current_reservation()
    if current_reservation is not None:
        # clear all reservations made for this user if he only reserved the next available spot on this device
        if current_reservation.user_reserved_next_available_spot:
            device = current_reservation.gpu.device
            reservations_to_delete = []
            for gpu in device.gpus.all():
                for reservation in gpu.reservations.all():
                    if reservation == current_reservation:
                        continue
                    elif reservation.user == current_reservation.user and reservation.user_reserved_next_available_spot:
                        reservations_to_delete.append(reservation)

            for reservation in reservations_to_delete:
                reservation.delete()

        send_gpu_done_mail(request, gpu, current_reservation)

    return HttpResponseRedirect(reverse("index"))


@login_required
def gpu_cancel(request, gpu_id):
    gpu = get_object_or_404(GPU, pk=gpu_id)
    if request.method != "POST":
        raise SuspiciousOperation

    if not gpu.device.can_be_used_by(request.user):
        raise PermissionDenied

    try:
        reservation = gpu.reservations.filter(user=request.user).latest("time_reserved")
        reservation.delete()
    except ObjectDoesNotExist as e:
        raise Http404

    return HttpResponseRedirect(reverse("index"))


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
