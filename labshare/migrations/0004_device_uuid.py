# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0003_reservation'),
    ]

    operations = [
        migrations.AddField(
            model_name='device',
            name='uuid',
            field=models.CharField(default='0', max_length=255),
            preserve_default=False,
        ),
    ]
