# Generated by Django 2.0.8 on 2018-08-17 13:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labshare', '0017_auto_20180612_1036'),
    ]

    operations = [
        migrations.AddField(
            model_name='gpu',
            name='marked_as_failed',
            field=models.BooleanField(default=False),
        ),
    ]