from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative, MediaOutlet

class Command(BaseCommand):
    help = 'Link existing articles to MediaOutlet profiles based on media_outlet field'

    def handle(self, *args, **options):
        updated = 0
        created_count = 0
        not_found = 0

        # Get all unique non-empty media_outlet names from articles
        outlet_names = MediaNarrative.objects.exclude(media_outlet__exact='')\
                                           .values_list('media_outlet', flat=True)\
                                           .distinct()

        self.stdout.write(f"Found {outlet_names.count()} unique outlet names in articles.")

        for name in outlet_names:
            if not name:
                continue

            # Get or create the MediaOutlet object
            outlet, created = MediaOutlet.objects.get_or_create(
                name__iexact=name.strip(),
                defaults={'name': name.strip()}
            )

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created new outlet: {outlet.name}"))

            # Link all articles that have this name in the old field and no FK yet
            linked = MediaNarrative.objects.filter(
                media_outlet__iexact=name.strip(),
                media_outlet_fk__isnull=True
            ).update(media_outlet_fk=outlet)

            if linked > 0:
                updated += linked
                self.stdout.write(f"Linked {linked} articles to '{outlet.name}'")

        # Optional: Report any outlet names in articles that still don't have a profile
        article_outlets = set(MediaNarrative.objects.exclude(media_outlet__exact='').values_list('media_outlet', flat=True))
        profile_outlets = set(MediaOutlet.objects.values_list('name', flat=True))
        missing = article_outlets - profile_outlets

        if missing:
            not_found = len(missing)
            self.stdout.write(
                self.style.WARNING(
                    f"{not_found} outlet names found in articles but not in MediaOutlet model: {', '.join(sorted(missing))}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Done! Created {created_count} new outlets. Linked {updated} articles to profiles. {not_found} names missing.'
            )
        )