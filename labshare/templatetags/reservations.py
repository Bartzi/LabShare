from django import template

from labshare.utils import get_current_reservation, get_next_reservation

register = template.Library()


@register.filter
def current_reservation(gpu):
    return get_current_reservation(gpu)


@register.filter
def next_reservation(gpu):
    return get_next_reservation(gpu)


@register.filter
def queue_position(gpu, user):
    reservations = gpu.reservations.order_by("time_reserved").all()
    user_reservations = reservations.filter(user__id=user.id)
    if len(user_reservations) < 1:
        return None
    else:
        return reservations.filter(
            time_reserved__lt=user_reservations.first().time_reserved).count()
