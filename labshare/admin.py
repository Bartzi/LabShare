from django.contrib import admin

from .models import Device, GPU
# Register your models here.
admin.site.register(Device)
admin.site.register(GPU)

