from django.db import migrations, models


def set_existing_states(apps, schema_editor):
    MediaNarrative = apps.get_model("dashboard", "MediaNarrative")
    MediaNarrative.objects.filter(
        models.Q(ml_processed_at__isnull=False)
        | (
            models.Q(strategic_intent__isnull=False)
            & ~models.Q(strategic_intent="")
        )
    ).update(inference_status="completed")


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0009_remove_medianarrative_vulnerability_index_and_more")]

    operations = [
        migrations.AddField(
            model_name="medianarrative",
            name="inference_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="medianarrative",
            name="inference_error_code",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="medianarrative",
            name="inference_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(set_existing_states, migrations.RunPython.noop),
    ]
