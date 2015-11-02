import smtplib
from django.core.mail import send_mail
from django.shortcuts import render
from labshare import settings
from .models import Device


def get_devices():
    return [(device.name, device.name) for device in Device.objects.all()]


def send_reservation_mail_for(request, gpu):
    if gpu.reservations.count() > 1:
        current_reservation = gpu.reservations.order_by("time_reserved").first()
        send_mail(
            "New reservation on GPU",
            render(request, "mails/new_reservation.txt", {
                    "gpu": gpu,
                    "reservation": current_reservation
                }).content.decode('utf-8'),
            settings.DEFAULT_FROM_EMAIL,
            [current_reservation.user.email],
        )


def send_gpu_done_mail(request, gpu, reservation):
    send_mail(
        "GPU free for use",
        render(request, "mails/gpu_free.txt", {"gpu": gpu, "reservation": reservation}).content.decode('utf-8'),
        settings.DEFAULT_FROM_EMAIL,
        [reservation.user.email],
    )