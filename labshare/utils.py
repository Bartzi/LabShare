import sys

import channels.layers
import json
import urllib.request
from asgiref.sync import async_to_sync
from django.conf import settings

from django.core.mail import send_mail, EmailMessage
from django.http import HttpResponse
from django.shortcuts import render
from django.template import loader
from urllib.error import URLError

from .models import Device, GPU, GPUProcess, Reservation


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


def delete_reservation(reservation):
    gpu = reservation.gpu
    reservation.delete()

    # get the user of the reservation that is now current and send him an email
    current_reservation = gpu.current_reservation()
    if current_reservation is not None:
        current_reservation.start_usage()
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

        send_gpu_done_mail(gpu, current_reservation)
    publish_device_state(gpu.device)


def send_gpu_done_mail(gpu, reservation):
    email_addresses = [address.email for address in reservation.user.email_addresses.all()]
    email_addresses.append(reservation.user.email)

    send_mail(
        "GPU free for use",
        loader.get_template("mails/gpu_free.txt").render({'reservation': reservation, "gpu": gpu}),
        settings.DEFAULT_FROM_EMAIL,
        email_addresses
    )


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

        current_user = failed_gpu.get_current_user()
        if current_user is not None:
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


def publish_device_state(device, channel_name=None):
    channel_layer = channels.layers.get_channel_layer()
    name = device.name
    device_data = device.serialize()
    if channel_name is None:
        send_function = async_to_sync(channel_layer.group_send)
    else:
        send_function = async_to_sync(channel_layer.send)
    send_function(channel_name if channel_name else name, {'type': 'update_info', 'message': json.dumps(device_data)})


def publish_gpu_states():
    devices = Device.objects.all()

    for device in devices:
        publish_device_state(device)


def send_extension_reminder(reservation):
    email_addresses = [address.email for address in reservation.user.email_addresses.all()]
    email_addresses.append(reservation.user.email)

    send_mail(
        "GPU reservation is expiring",
        loader.get_template("mails/expiration_reminder.txt").render({'reservation': reservation, "gpu": reservation.gpu}),
        settings.DEFAULT_FROM_EMAIL,
        email_addresses
    )
    reservation.set_reminder_sent()


def expire_reservation(reservation):
    delete_reservation(reservation)
    email_addresses = [address.email for address in reservation.user.email_addresses.all()]
    email_addresses.append(reservation.user.email)

    send_mail(
        "GPU reservation expired",
        loader.get_template("mails/usage_expired.txt").render({'reservation': reservation, "gpu": reservation.gpu}),
        settings.DEFAULT_FROM_EMAIL,
        email_addresses,
    )


def check_reservations():
    all_reservations = Reservation.objects.all()
    for reservation in all_reservations:
        if reservation.usage_started is None:
            continue
        if reservation.is_usage_expired():
            expire_reservation(reservation)
            continue
        if reservation.needs_reminder():
            send_extension_reminder(reservation)
