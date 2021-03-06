# -*- coding: utf-8 -*-
# Generated by Django 1.10.5 on 2017-02-28 09:34
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0015_remove_gpu_free_memory'),
    ]

    operations = [
        migrations.CreateModel(
            name='GPUProcess',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=511)),
                ('pid', models.PositiveIntegerField()),
                ('memory_usage', models.CharField(blank=True, max_length=100)),
                ('username', models.CharField(blank=True, max_length=250)),
                ('gpu', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='processes', to='labshare.GPU')),
            ],
        ),
    ]
