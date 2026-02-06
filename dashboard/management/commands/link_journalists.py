from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative, Journalist

class Command(BaseCommand):
    help = 'Link existing articles to Journalist profiles based on author field'

    def handle(self, *args, **options):
        updated = 0
        not_found = 0

        # Get all unique author names from articles (skip "Unknown")
        author_names = MediaNarrative.objects.exclude(author__in=['', 'Unknown'])\
                                            .values_list('author', flat=True)\
                                            .distinct()

        for name in author_names:
            if not name:
                continue

            # Find the Journalist (case-insensitive)
            journalist = Journalist.objects.filter(name__iexact=name.strip()).first()

            if journalist:
                # Link all articles with this author name to the journalist
                linked = MediaNarrative.objects.filter(
                    author__iexact=name.strip(),
                    journalist_fk__isnull=True
                ).update(journalist_fk=journalist)

                if linked > 0:
                    updated += linked
                    self.stdout.write(f"Linked {linked} articles to '{journalist.name}'")
            else:
                not_found += 1
                self.stdout.write(self.style.WARNING(f"No Journalist profile found for author: {name}"))

        self.stdout.write(self.style.SUCCESS(f'Done! Linked {updated} articles. {not_found} authors not found in Journalist model.'))