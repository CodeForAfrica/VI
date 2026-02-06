import csv
from django.core.management.base import BaseCommand
from dashboard.models import MediaOutlet

class Command(BaseCommand):
    help = 'Import MediaOutlet data from CSV file'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        imported = 0
        skipped = 0

        with open(csv_file, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                try:
                    # Clean empty strings to None
                    cleaned_row = {k: (v.strip() if v.strip() else None) for k, v in row.items()}

                    MediaOutlet.objects.update_or_create(
                        name=cleaned_row['name'],
                        defaults={
                            'parent_organisation': cleaned_row.get('parent_organisation'),
                            'website': cleaned_row.get('website'),
                            'country': cleaned_row.get('country'),
                            'city': cleaned_row.get('city'),
                            'content_sharing_agreements': cleaned_row.get('content_sharing_agreements'),
                            'linkedin_profile': cleaned_row.get('linkedin_profile'),
                            'facebook_profile': cleaned_row.get('facebook_profile'),
                            'x_profile': cleaned_row.get('x_profile'),
                            'instagram_profile': cleaned_row.get('instagram_profile'),
                        }
                    )
                    imported += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Skipped row {row}: {e}"))
                    skipped += 1

        self.stdout.write(self.style.SUCCESS(f'Imported {imported} media outlets. Skipped {skipped}.'))