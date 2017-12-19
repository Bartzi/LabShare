# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('labshare', '0002_device_ip_address'),
    ]

    operations = [
        migrations.CreateModel(
            name='Reservation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gpu', models.ForeignKey(to='labshare.GPU', related_name='reservations', on_delete='cascade')),
                ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name='reservations', on_delete='cascade')),
            ],
        ),
    ]
