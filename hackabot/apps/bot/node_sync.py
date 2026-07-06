import json
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import sentry_sdk
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from hackabot.apps.bot.models import Node

REQUIRED_FIELDS = (
    "name",
    "emoji",
    "established",
    "location",
    "timezone",
    "signup_url",
)
MIN_YEAR = 1970
MIN_ACTIVE_FOR_GUARD = 4
MAX_DISABLE_FRACTION = 0.5
FETCH_TIMEOUT = 15
NODE_SYNC_LOCK_KEY = 8274531


def is_flag_emoji(value):
    cps = [ord(c) for c in value]
    if len(cps) == 2 and all(0x1F1E6 <= cp <= 0x1F1FF for cp in cps):
        return True
    if (
        len(cps) >= 3
        and cps[0] == 0x1F3F4
        and cps[-1] == 0xE007F
        and all(0xE0020 <= cp <= 0xE007E for cp in cps[1:-1])
    ):
        return True
    return False


def name_slug(name):
    return name.lower().replace(" ", "")


def valid_timezone(tz):
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def validate_entry(entry):
    if not isinstance(entry, dict):
        return None, None, "entry is not an object"

    name = entry.get("name")
    name_valid = (
        isinstance(name, str)
        and bool(name.strip())
        and len(name.strip()) <= 100
    )
    name_out = name.strip() if name_valid else None

    unknown = set(entry.keys()) - set(REQUIRED_FIELDS)
    if unknown:
        return name_out, None, f"unknown keys: {sorted(unknown)}"

    missing = [f for f in REQUIRED_FIELDS if f not in entry]
    if missing:
        return name_out, None, f"missing fields: {missing}"

    if not name_valid:
        return name_out, None, "invalid name"

    emoji = entry["emoji"]
    if (
        not isinstance(emoji, str)
        or len(emoji) > 10
        or not is_flag_emoji(emoji)
    ):
        return name_out, None, "emoji must be a single flag"

    established = entry["established"]
    max_year = timezone.now().year + 1
    if (
        isinstance(established, bool)
        or not isinstance(established, int)
        or established < MIN_YEAR
        or established > max_year
    ):
        return name_out, None, "established must be a plausible year"

    location = entry["location"]
    if (
        not isinstance(location, str)
        or not location.strip()
        or len(location) > 255
    ):
        return name_out, None, "invalid location"

    tz = entry["timezone"]
    if not isinstance(tz, str) or not valid_timezone(tz):
        return name_out, None, "invalid timezone"

    url = entry["signup_url"]
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or len(url) > 200
        or not urlparse(url).netloc
    ):
        return name_out, None, "signup_url must be an https URL"

    cleaned = dict(
        name=name.strip(),
        emoji=emoji,
        established=established,
        location=location.strip(),
        timezone=tz,
        signup_url=url,
    )
    return name_out, cleaned, None


def report_problem(message):
    print(f"⚠️ {message}")
    sentry_sdk.capture_message(message, level="error")


def build_abort(reason, message):
    report_problem(f"nodes.json sync aborted: {message}")
    return dict(aborted=True, reason=reason)


def entry_ident(entry, index):
    if isinstance(entry, dict) and isinstance(entry.get("name"), str):
        return entry["name"]
    return f"#{index}"


def apply_entry(cleaned):
    matches = list(Node.objects.filter(name=cleaned["name"]))
    if len(matches) > 1:
        report_problem(
            f"nodes.json: multiple nodes named {cleaned['name']}, skipped"
        )
        return "ambiguous"

    if not matches:
        Node.objects.create(
            name=cleaned["name"],
            emoji=cleaned["emoji"],
            established=cleaned["established"],
            location=cleaned["location"],
            timezone=cleaned["timezone"],
            signup_url=cleaned["signup_url"],
            unlisted=False,
            disabled=False,
        )
        return "created"

    node = matches[0]
    changed = []
    for field in (
        "emoji",
        "established",
        "location",
        "timezone",
        "signup_url",
    ):
        if getattr(node, field) != cleaned[field]:
            setattr(node, field, cleaned[field])
            changed.append(field)
    if node.unlisted:
        node.unlisted = False
        changed.append("unlisted")
        if node.disabled:
            node.disabled = False
            changed.append("disabled")

    if changed:
        node.save(update_fields=changed)
        return "updated"
    return "unchanged"


def disable_missing(present_names):
    disabled = []
    for node in Node.objects.filter(unlisted=False):
        if node.name not in present_names:
            node.unlisted = True
            node.disabled = True
            node.save(update_fields=["unlisted", "disabled"])
            disabled.append(node.name)
    return disabled


def acquire_sync_lock():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)", [NODE_SYNC_LOCK_KEY]
            )


def reconcile_nodes(data):
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return build_abort("bad_shape", "missing top-level 'nodes' list")

    present_names = set()
    seen_names = set()
    seen_slugs = set()
    valid = []
    error_count = 0

    for index, entry in enumerate(data["nodes"]):
        name, cleaned, error = validate_entry(entry)
        if name:
            present_names.add(name)
        if error:
            report_problem(
                f"nodes.json entry {entry_ident(entry, index)}: {error}"
            )
            error_count += 1
            continue
        if cleaned["name"] in seen_names:
            report_problem(
                f"nodes.json: duplicate name {cleaned['name']}, skipped"
            )
            error_count += 1
            continue
        slug = name_slug(cleaned["name"])
        if slug in seen_slugs:
            report_problem(
                f"nodes.json: {cleaned['name']} collides with an "
                f"existing node id, skipped"
            )
            error_count += 1
            continue
        seen_names.add(cleaned["name"])
        seen_slugs.add(slug)
        valid.append(cleaned)

    if not valid:
        return build_abort("no_valid_nodes", "no valid node entries")

    with transaction.atomic():
        acquire_sync_lock()

        active_names = list(
            Node.objects.filter(unlisted=False).values_list("name", flat=True)
        )
        disappearing = [n for n in active_names if n not in present_names]
        if (
            len(active_names) >= MIN_ACTIVE_FOR_GUARD
            and len(disappearing) > len(active_names) * MAX_DISABLE_FRACTION
        ):
            return build_abort(
                "suspicious_drop",
                f"would disable {len(disappearing)}/"
                f"{len(active_names)} nodes",
            )

        counts = dict(created=0, updated=0, unchanged=0)
        for cleaned in valid:
            result = apply_entry(cleaned)
            if result in counts:
                counts[result] += 1

        disabled = disable_missing(present_names)

    summary = dict(
        aborted=False,
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        disabled=len(disabled),
        skipped=error_count,
    )
    print(f"🔄 nodes.json sync: {summary}")
    return summary


def sync_nodes_from_url(url=None):
    url = url or settings.NODES_JSON_URL
    try:
        response = requests.get(url, timeout=FETCH_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        sentry_sdk.capture_exception(e)
        print(f"❌ nodes.json fetch failed: {e}")
        return dict(aborted=True, reason="fetch_failed")

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        sentry_sdk.capture_exception(e)
        print(f"❌ nodes.json parse failed: {e}")
        return dict(aborted=True, reason="parse_failed")

    return reconcile_nodes(data)
