# -*- coding: utf-8 -*-
# Generated by Django 1.10.1 on 2017-01-14 11:29
from __future__ import unicode_literals

import django.core.validators
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('Leaderboards', '0010_performance_victory_count'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='session',
            options={'ordering': ['-date_time']},
        ),
        migrations.AddField(
            model_name='performance',
            name='trueskill_beta',
            field=models.FloatField(default=4.166666666666667, verbose_name='TrueSkill Skill Factor (ß)'),
        ),
        migrations.AddField(
            model_name='performance',
            name='trueskill_delta',
            field=models.FloatField(default=0.0001, verbose_name='TrueSkill Delta (δ)'),
        ),
        migrations.AddField(
            model_name='performance',
            name='trueskill_mu0',
            field=models.FloatField(default=25.0, editable=False, verbose_name='Trueskill Initial Mean (µ)'),
        ),
        migrations.AddField(
            model_name='performance',
            name='trueskill_p',
            field=models.FloatField(default=0.1, verbose_name='TrueSkill Draw Probability (p)'),
        ),
        migrations.AddField(
            model_name='performance',
            name='trueskill_sigma0',
            field=models.FloatField(default=8.333333333333334, editable=False, verbose_name='Trueskill Initial Standard Deviation (σ)'),
        ),
        migrations.AddField(
            model_name='performance',
            name='trueskill_tau',
            field=models.FloatField(default=0.08333333333333334, verbose_name='TrueSkill Dynamics Factor (τ)'),
        ),
        migrations.AddField(
            model_name='rating',
            name='last_play',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name='rating',
            name='last_victory',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AlterField(
            model_name='league',
            name='name',
            field=models.CharField(max_length=200, validators=[django.core.validators.RegexValidator(code='reserved', message='GLOBAL is a reserved league name', regex='^GLOBAL')], verbose_name='Name of the League'),
        ),
    ]