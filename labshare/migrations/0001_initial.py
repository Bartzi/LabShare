# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Device',
            fields=[
                ('id', models.AutoField(serialize=False, verbose_name='ID', auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=255)),
            ],
        ),
        migrations.CreateModel(
            name='GPU',
            fields=[
                ('id', models.AutoField(serialize=False, verbose_name='ID', auto_created=True, primary_key=True)),
                ('model_name', models.CharField(max_length=255)),
                ('free_memory', models.CharField(max_length=100)),
                ('used_memory', models.CharField(max_length=100)),
                ('total_memory', models.CharField(max_length=100)),
                ('device', models.ForeignKey(to='labshare.Device', related_name='gpus')),
            ],
        ),
    ]
