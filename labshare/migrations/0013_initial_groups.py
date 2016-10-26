# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib.auth.models import Group
from django.db import migrations
from guardian.shortcuts import assign_perm


def initial_data(apps, schema_editor):
    staff = Group.objects.create(name="Staff")
    assign_perm('labshare.use_device', staff)
    staff.save()


def delete_staff_group(apps, schema_editor):
    staff = Group.objects.get(name="Staff")
    staff.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('labshare', '0012_auto_20161026_1453'),
    ]


    operations = [
        migrations.RunPython(initial_data, delete_staff_group),
    ]
