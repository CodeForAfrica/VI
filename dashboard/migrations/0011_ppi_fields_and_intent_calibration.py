from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0010_medianarrative_confidence_is_intent_only_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='medianarrative',
            name='anchor_split',
            field=models.CharField(
                blank=True, null=True, max_length=10,
                choices=[('train', 'Used for model training'), ('calib', 'Held out for calibration and PPI')],
                help_text="Which anchor set a labelled article belongs to. Only 'calib' anchors correct the index."),
        ),
        migrations.AddField(
            model_name='medianarrative',
            name='intent_probs',
            field=models.JSONField(
                blank=True, null=True,
                help_text='Raw model probability for every strategic-intent class, {label: probability}'),
        ),
        migrations.CreateModel(
            name='IntentCalibration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('n_samples', models.IntegerField()),
                ('params', models.JSONField()),
                ('brier_before', models.FloatField(blank=True, null=True)),
                ('brier_after', models.FloatField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
