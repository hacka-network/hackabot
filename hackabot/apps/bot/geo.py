import math
import os

import maxminddb
import sentry_sdk
from django.conf import settings
from maxminddb.errors import InvalidDatabaseError

NEAREST_MAX_KM = 1000
EARTH_RADIUS_KM = 6371

_reader = None
_reader_tried = False


def get_reader():
    global _reader, _reader_tried

    if _reader_tried:
        return _reader
    _reader_tried = True

    path = settings.GEOIP_DB_PATH
    if not path or not os.path.exists(path):
        sentry_sdk.capture_message(
            f"GeoIP database missing at {path}, nearest node disabled",
            level="warning",
        )
        return None

    try:
        _reader = maxminddb.open_database(path)
    except (InvalidDatabaseError, OSError) as e:
        sentry_sdk.capture_exception(e)
        print(f"❌ GeoIP database unreadable: {e}")
    return _reader


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        # Heroku's router appends the socket peer, so the last entry is the
        # only one a client can't prepend a spoofed value in front of.
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


def locate(ip):
    if not ip:
        return None

    reader = get_reader()
    if reader is None:
        return None

    try:
        record = reader.get(ip)
    except ValueError:
        return None

    if not isinstance(record, dict):
        return None

    location = record.get("location") or {}
    lat = location.get("latitude")
    lon = location.get("longitude")
    if lat is None or lon is None:
        return None
    return lat, lon


def distance_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = [
        math.radians(value) for value in (lat1, lon1, lat2, lon2)
    ]
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest_node(request, nodes):
    point = locate(client_ip(request))
    if point is None:
        return None

    lat, lon = point
    best = None
    best_km = None
    for node in nodes:
        if node.disabled or node.lat is None or node.lon is None:
            continue
        km = distance_km(lat, lon, node.lat, node.lon)
        if best_km is None or km < best_km:
            best = node
            best_km = km

    if best_km is None or best_km > NEAREST_MAX_KM:
        return None
    return best
