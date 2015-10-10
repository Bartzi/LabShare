# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import datetime


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0005_auto_20151006_1151'),
    ]

    operations = [
        migrations.AddField(
            model_name='reservation',
            name='time_reserved',
            field=models.DateTimeField(default=datetime.datetime(2015, 10, 10, 13, 8, 27, 423721), auto_now_add=True),
            preserve_default=False,
        ),
    ]
