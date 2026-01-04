from django.core.management.base import BaseCommand

from ...run import run_worker


class Command(BaseCommand):
    def handle(self, *args, **options):
        run_worker()
