# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import datetime


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0006_reservation_time_reserved'),
    ]

    operations = [
        migrations.AddField(
            model_name='gpu',
            name='last_updated',
            field=models.DateTimeField(default=datetime.datetime(2015, 10, 10, 13, 34, 50, 251495), auto_now=True),
            preserve_default=False,
        ),
    ]
