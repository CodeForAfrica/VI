# dashboard/management/commands/reset_other_intents_to_null.py
from django.core.management.base import BaseCommand
from django.db import transaction
from dashboard.models import MediaNarrative
import time

class Command(BaseCommand):
    help = 'Resets strategic_intent from \'Other\' to NULL'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to update in each batch (default: 1000)',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        max_retries_per_batch = 3
        retry_delay = 2

        # Find all articles where strategic_intent is literally 'Other'
        # Get IDs first to avoid issues with updating while iterating a filtered queryset
        ids_to_update = list(MediaNarrative.objects.filter(strategic_intent='Other').values_list('id', total = len(ids_to_update)
        self.stdout.write(self.style.SUCCESS(f"Found {total} articles with strategic_intent = 'Other' to reset to NULL."))

        if total == 0:
            self.stdout.write(self.style.WARNING("No articles found requiring processing. Exiting."))
            return

        success_count = 0
        failed_ids = []

        for i in range(0, total, batch_size):
            batch_ids = ids_to_update[i:i + batch_size]
            retries = 0
            success = False

            while retries < max_retries_per_batch and not success:
                try:
                    with transaction.atomic():
                        # Update the strategic_intent field to NULL for the batch
                        updated_in_batch = MediaNarrative.objects.filter(
                            id__in=batch_ids
                        ).update(strategic_intent=None) # Sets to NULL in the database

                        success_count += updated_in_batch
                        self.stdout.write(f"Batch {i//batch_size + 1}: Updated {updated_in_batch} articles.")
                        success = True  # Mark success, exit retry loop

                except Exception as e:
                    retries += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f"Batch {i//batch_size + 1} failed (attempt {retries}): {e}"
                        )
                    )
                    if retries < max_retries_per_batch:
                        self.stdout.write(f"Retrying batch in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    else:
                        self.stderr.write(
                            self.style.ERROR(
                                f"Batch {i//batch_size + 1} failed permanently after {max_retries_per_batch} attempts. IDs: {batch_ids}"
                            )
                        )
                        failed_ids.extend(batch_ids)

        self.stdout.write(
            self.style.SUCCESS(
                f"Reset operation completed. Successfully updated {success_count} articles. "
                f"Failed to update {len(failed_ids)} articles."
            )
        )
        if failed_ids:
            self.stdout.write(f"IDs of failed updates: {failed_ids}")
