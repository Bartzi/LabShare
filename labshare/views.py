import json

from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, HttpResponseBadRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import render

from .forms import DeviceSelectForm
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
        gpu = GPU.objects.get(uuid=form.data["gpu"])
        reservation = Reservation(gpu=gpu, user=request.user)
        reservation.save()
        return HttpResponseRedirect(reverse("index"))

    return render(request, "reserve.html", {
        "form": form,
    })


@login_required
def gpus(request):
    if request.method != "GET" or not request.is_ajax():
        return HttpResponseBadRequest()

    device_name = request.GET['device_name']
    device = Device.objects.get(name=device_name)
    return_data = {
        'gpus': [{
            "id": gpu.uuid,
            "name": gpu.model_name} for gpu in device.gpus.all()]
    }
    return HttpResponse(json.dumps(return_data, indent=4))


@login_required
def gpu_info(request):
    if request.method != "GET" or not request.is_ajax():
        return HttpResponseBadRequest()

    gpu = GPU.objects.get(uuid=request.GET['uuid'])
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
    gpu = GPU.objects.get(pk=gpu_id)
    current_reservation = gpu.reservations.order_by("time_reserved").first()

    if current_reservation.user != request.user:
        return HttpResponseForbidden()

    current_reservation.delete()
    return HttpResponseRedirect(reverse("index"))
