# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0004_device_uuid'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='device',
            name='uuid',
        ),
        migrations.AddField(
            model_name='gpu',
            name='uuid',
            field=models.CharField(max_length=255, default='0'),
            preserve_default=False,
        ),
    ]
