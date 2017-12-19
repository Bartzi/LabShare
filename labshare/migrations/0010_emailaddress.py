# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('labshare', '0009_reservation_user_reserved_next_available_spot'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailAddress',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, primary_key=True, auto_created=True)),
                ('email', models.EmailField(max_length=255, unique=True)),
                ('user', models.ForeignKey(related_name='email_addresses', to=settings.AUTH_USER_MODEL, on_delete='cascade')),
            ],
        ),
    ]
