import json

from django.core.management.base import BaseCommand

from hackabot.apps.bot.node_sync import reconcile_nodes, sync_nodes_from_url


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--file", dest="file", default=None)
        parser.add_argument("--url", dest="url", default=None)

    def handle(self, *args, **options):
        if options["file"]:
            with open(options["file"], encoding="utf-8") as handle:
                data = json.load(handle)
            summary = reconcile_nodes(data)
        else:
            summary = sync_nodes_from_url(options["url"])
        self.stdout.write(str(summary))
