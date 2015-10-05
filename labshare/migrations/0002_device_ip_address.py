# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='device',
            name='ip_address',
            field=models.GenericIPAddressField(default='0.0.0.0'),
            preserve_default=False,
        ),
    ]
