# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0007_gpu_last_updated'),
    ]

    operations = [
        migrations.AlterField(
            model_name='gpu',
            name='uuid',
            field=models.CharField(max_length=255, unique=True),
        ),
    ]
