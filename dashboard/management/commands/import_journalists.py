import csv
from django.core.management.base import BaseCommand
from dashboard.models import Journalist

class Command(BaseCommand):
    help = 'Import journalists from Journalist.csv file (handles different encodings)'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to Journalist.csv')

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        imported = 0
        skipped = 0

        encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'windows-1252', 'iso-8859-1']
        success = False

        for enc in encodings:
            try:
                with open(csv_file, 'r', encoding=enc) as file:
                    reader = csv.DictReader(file)
                    for row_num, row in enumerate(reader, start=2):
                        try:
                            cleaned_row = {k: (v.strip() if v.strip() else None) for k, v in row.items()}
                            if not cleaned_row.get('name'):
                                skipped += 1
                                continue

                            journalist, created = Journalist.objects.update_or_create(
                                name=cleaned_row['name'],
                                defaults={
                                    'city': cleaned_row.get('city'),
                                    'country_based': cleaned_row.get('country_based'),
                                    'nationality': cleaned_row.get('nationality'),
                                    'linkedin_profile': cleaned_row.get('linkedin_profile'),
                                    'facebook_profile': cleaned_row.get('facebook_profile'),
                                    'x_profile': cleaned_row.get('x_profile'),
                                    'instagram_profile': cleaned_row.get('instagram_profile'),
                                }
                            )
                            action = "Created" if created else "Updated"
                            self.stdout.write(self.style.SUCCESS(f"{action}: {journalist.name}"))
                            imported += 1
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Row {row_num} skipped: {e}"))
                            skipped += 1

                    success = True
                    self.stdout.write(self.style.SUCCESS(f"Successfully read file with encoding: {enc}"))
                    break

            except UnicodeDecodeError:
                continue
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error with encoding {enc}: {e}"))
                continue

        if not success:
            self.stdout.write(self.style.ERROR("Could not read the CSV file with any supported encoding."))
            return

        self.stdout.write(self.style.SUCCESS(f'Import complete: {imported} journalists imported, {skipped} skipped.'))