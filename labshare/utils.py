import smtplib
from labshare import settings
from .models import Device


def get_devices():
    return [(device.name, device.name) for device in Device.objects.all()]


def send_email(to, subject, message_body):
    msg = "From: {from_name} <{from_addr}>\nTo: {to_name} <{to_addr}>\nSubject: {subject_mail}\n{message}".format(
                    from_name=settings.FROM_EMAIL.split('@')[0],
                    from_addr=settings.FROM_EMAIL,
                    to_name=to.split('@')[0],
                    to_addr=to,
                    subject_mail=subject,
                    message=message_body)

    server = smtplib.SMTP(settings.EMAIL_HOST, 25)
    server.ehlo()
    server.sendmail(settings.FROM_EMAIL, to, msg)
    server.quit()