from django.contrib import admin

from .models import Device, GPU, Reservation


admin.site.register(Device)
admin.site.register(GPU)
admin.site.register(Reservation)

