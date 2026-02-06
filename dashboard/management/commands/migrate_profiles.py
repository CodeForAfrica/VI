from django.core.management.base import BaseCommand
from dashboard.models import MediaNarrative, Journalist, MediaOutlet

class Command(BaseCommand):
    help = 'Migrate old CharField data to new ForeignKey fields'

    def handle(self, *args, **options):
        updated = 0

        for article in MediaNarrative.objects.all():
            changed = False

            # Migrate media_outlet
            if article.media_outlet and not article.media_outlet_fk:
                outlet, _ = MediaOutlet.objects.get_or_create(name=article.media_outlet)
                article.media_outlet_fk = outlet
                changed = True

            # Migrate author
            if article.author and not article.journalist_fk:
                journalist, _ = Journalist.objects.get_or_create(name=article.author)
                article.journalist_fk = journalist
                changed = True

            if changed:
                article.save()
                updated += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully migrated {updated} articles!'))