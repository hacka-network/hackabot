from django.core.management.base import BaseCommand

from hackabot.apps.bot.models import MeetupPhoto
from hackabot.apps.bot.views import _get_event_date


class Command(BaseCommand):
    help = "Backfill MeetupPhoto created dates to event day"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without saving",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        photos = MeetupPhoto.objects.select_related("node").all()
        updated = 0

        for photo in photos:
            node = photo.node
            new_created = _get_event_date(
                photo.created, node.event_day, node.timezone
            )

            if photo.created == new_created:
                continue

            self.stdout.write(
                f"{photo} : {photo.created} -> {new_created}"
            )

            if not dry_run:
                photo.created = new_created
                photo.save(update_fields=["created"])

            updated += 1

        label = "Would update" if dry_run else "Updated"
        self.stdout.write(f"{label} {updated} photo(s)")
