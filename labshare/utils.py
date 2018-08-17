import sys
import json
import urllib.request
from django.conf import settings

from django.core.mail import send_mail, EmailMessage
from django.http import HttpResponse
from django.shortcuts import render
from django.template import loader
from urllib.error import URLError

from .models import Device, GPU, GPUProcess


def get_devices():
    return [(device.name, device.name) for device in Device.objects.all()]


def send_reservation_mail_for(request, gpu):
    if gpu.reservations.count() > 1:
        current_reservation = gpu.reservations.order_by("time_reserved").first()
        email_addresses = [address.email for address in current_reservation.user.email_addresses.all()]
        email_addresses.append(current_reservation.user.email)
        send_mail(
            "New reservation on GPU",
            render(request, "mails/new_reservation.txt", {
                    "gpu": gpu,
                    "reservation": current_reservation
                }).content.decode('utf-8'),
            settings.DEFAULT_FROM_EMAIL,
            email_addresses,
        )


def send_gpu_done_mail(request, gpu, reservation):
    email_addresses = [address.email for address in reservation.user.email_addresses.all()]
    email_addresses.append(reservation.user.email)
    send_mail(
        "GPU free for use",
        render(request, "mails/gpu_free.txt", {"gpu": gpu, "reservation": reservation}).content.decode('utf-8'),
        settings.DEFAULT_FROM_EMAIL,
        email_addresses,
    )


def get_current_reservation(gpu):
    reservations = gpu.reservations.all()
    if len(reservations) == 0:
        return ""
    return reservations.order_by("time_reserved").first().user


def get_next_reservation(gpu):
    reservations = gpu.reservations.all()
    if len(reservations) <= 1:
        return ""
    return reservations.order_by("time_reserved").all()[1].user


def login_required_ajax(function=None, redirect_field_name=None):
    """
    Just make sure the user is authenticated to access a certain ajax view

    Otherwise return a HttpResponse 401 - authentication required
    instead of the 302 redirect of the original Django decorator
    """
    def _decorator(view_func):
        def _wrapped_view(request, *args, **kwargs):
            if request.user.is_authenticated:
                return view_func(request, *args, **kwargs)
            else:
                return HttpResponse(status=401)
        return _wrapped_view

    if function is None:
        return _decorator
    else:
        return _decorator(function)


def update_gpu_info():
    for device in Device.objects.all():
        try:
            ip_address = device.ip_address
            response = urllib.request.urlopen("http://{}:12000".format(ip_address), timeout=10).read().decode('utf-8')
            gpus = json.loads(response)
            for gpu_data in gpus:
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
        except Exception as e:
            print(e, file=sys.stderr)


def determine_failed_gpus():
    # gather all GPUs that have not been updated in a while and notify users + admin of possible problems
    failed_gpus = filter(lambda gpu: gpu.last_update_too_long_ago() and not gpu.marked_as_failed, GPU.objects.all())
    for failed_gpu in failed_gpus:
        # 1. gather all email addresses
        admin_emails = [data[1] for data in settings.ADMINS]

        current_user = get_current_reservation(failed_gpu)
        if current_user != "":
            current_user_emails = [address.email for address in current_user.email_addresses.all()]
            current_user_emails.append(current_user.email)
        else:
            current_user_emails = admin_emails

        # 2. prepare and send email
        email_template = loader.get_template('mails/gpu_problem.txt')
        email = EmailMessage(
            subject="[Labshare] Problem with GPU",
            body=email_template.render({'user': current_user, "gpu": failed_gpu}),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=current_user_emails,
            cc=admin_emails,
        )
        email.send()

        # 3. mark gpu as failed and inhibit further emails
        failed_gpu.marked_as_failed = True
        failed_gpu.save(update_fields=["marked_as_failed"])
