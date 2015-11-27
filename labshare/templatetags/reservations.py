from django import template
register = template.Library()


@register.filter
def current_reservation(gpu):
    reservations = gpu.reservations.all()
    if len(reservations) == 0:
        return ""
    return reservations.order_by("time_reserved").first().user


@register.filter
def next_reservation(gpu):
    reservations = gpu.reservations.all()
    if len(reservations) <= 1:
        return ""
    return reservations.order_by("time_reserved").all()[1].user


@register.filter
def queue_position(gpu, user):
    reservations = gpu.reservations.order_by("time_reserved").all()
    user_reservations = reservations.filter(user__id=user.id)
    if len(user_reservations) < 1:
        return None
    else:
        return reservations.filter(
            time_reserved__lt=user_reservations.first().time_reserved).count()
