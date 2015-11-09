import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, HttpResponseBadRequest, HttpResponse, HttpResponseForbidden, Http404
from django.shortcuts import render

from .forms import DeviceSelectForm
from labshare.utils import send_reservation_mail_for, send_gpu_done_mail, login_required_ajax
from .models import Device, Reservation, GPU


def index(request):
    devices = Device.objects.all()

    return render(request, "overview.html", {
        "devices": devices,
    })


@login_required
def reserve(request):
    form = DeviceSelectForm(request.POST or None)
    if form.is_valid():
        if json.loads(form.data["next-available-spot"]):
            device = Device.objects.get(name=form.data["device"])
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
            reservation = Reservation(gpu=gpu, user=request.user)
            reservation.save()

            send_reservation_mail_for(request, gpu)

        return HttpResponseRedirect(reverse("index"))

    return render(request, "reserve.html", {
        "form": form,
    })


@login_required_ajax
def gpus(request):
    if request.method != "GET" or not request.is_ajax():
        return HttpResponseBadRequest()

    device_name = request.GET.get('device_name', None)
    if device_name is None:
        return HttpResponseBadRequest()

    device = Device.objects.get(name=device_name)
    return_data = {
        'gpus': [{
            "id": gpu.uuid,
            "name": gpu.model_name} for gpu in device.gpus.all()]
    }
    return HttpResponse(json.dumps(return_data, indent=4))


@login_required_ajax
def gpu_info(request):
    if request.method != "GET" or not request.is_ajax():
        return HttpResponseBadRequest()

    uuid = request.GET.get('uuid', None)
    if uuid is None:
        return HttpResponseBadRequest()

    gpu = GPU.objects.get(uuid=uuid)
    current_reservation = gpu.reservations.order_by("time_reserved").first()

    return_data = {
        "free": gpu.free_memory,
        "used": gpu.used_memory,
        "total": gpu.total_memory,
        "user": current_reservation.user.username if current_reservation is not None else "No current user",
    }

    return HttpResponse(json.dumps(return_data, indent=4))


@login_required
def gpu_done(request, gpu_id):
    try:
        gpu = GPU.objects.get(pk=gpu_id)
    except ObjectDoesNotExist:
        raise Http404

    current_reservation = gpu.reservations.order_by("time_reserved").first()

    if current_reservation is None:
        raise Http404

    if current_reservation.user != request.user:
        return HttpResponseForbidden()

    current_reservation.delete()

    # get the user of the reservation that is now current and send him an email
    current_reservation = gpu.reservations.order_by("time_reserved").first()
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
    try:
        gpu = GPU.objects.get(pk=gpu_id)
    except ObjectDoesNotExist:
        raise Http404

    next_reservation = gpu.reservations.order_by("time_reserved").all()[1]

    if next_reservation is None:
        raise Http404

    if next_reservation.user != request.user:
        return HttpResponseForbidden()

    next_reservation.delete()

    return HttpResponseRedirect(reverse("index"))
