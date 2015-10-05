from django import template
register = template.Library()


@register.filter
def current_reservation(gpu):
    reservations = gpu.reservations.all()
    if len(reservations) == 0:
        return ""
    return reservations.order_by("pk").first().user


@register.filter
def next_reservation(gpu):
    reservations = gpu.reservations.all()
    if len(reservations) <= 1:
        return ""
    return reservations.order_by("pk").all()[1].user
