# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0008_auto_20151014_1641'),
    ]

    operations = [
        migrations.AddField(
            model_name='reservation',
            name='user_reserved_next_available_spot',
            field=models.BooleanField(default=False),
        ),
    ]
