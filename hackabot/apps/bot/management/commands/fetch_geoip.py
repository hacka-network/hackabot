import gzip
import os

import arrow
import requests
from django.conf import settings
from django.core.management.base import BaseCommand

FETCH_TIMEOUT = 180


class Command(BaseCommand):
    def handle(self, *args, **options):
        target = settings.GEOIP_DB_PATH
        os.makedirs(os.path.dirname(target), exist_ok=True)

        now = arrow.utcnow()
        for month in (
            now.format("YYYY-MM"),
            now.shift(months=-1).format("YYYY-MM"),
        ):
            url = settings.DBIP_URL_TEMPLATE.format(month=month)
            self.stdout.write(f"Fetching {url}")
            response = requests.get(url, timeout=FETCH_TIMEOUT)
            if response.status_code == 404:
                self.stdout.write(f"{month} not published yet")
                continue
            response.raise_for_status()

            with open(target, "wb") as handle:
                handle.write(gzip.decompress(response.content))
            size = os.path.getsize(target) / 1e6
            self.stdout.write(f"Wrote {target} ({size:.0f} MB)")
            return

        self.stdout.write("No database available, nearest node stays off")
