import time
import traceback

import arrow
import sentry_sdk
from django.utils import timezone

from hackabot.apps.bot.telegram import (
    send_event_reminder,
    send_poll,
    verify_webhook,
)

POLL_DAY = 0  # Monday (0 = Monday in arrow weekday)
POLL_HOUR = 15
POLL_MINUTE = 0
EVENT_DAY = 3  # Thursday


def should_send_poll(node, now_in_tz):
    if now_in_tz.weekday() != POLL_DAY:
        return False

    if now_in_tz.hour != POLL_HOUR:
        return False

    if now_in_tz.minute != POLL_MINUTE:
        return False

    if node.last_poll_sent_at:
        days_since = (timezone.now() - node.last_poll_sent_at).days
        if days_since < 6:
            return False

    return True


def should_send_event_reminder(event, now_in_tz):
    if now_in_tz.weekday() != EVENT_DAY:
        return False

    if now_in_tz.hour != event.time.hour:
        return False

    if now_in_tz.minute != event.time.minute:
        return False

    if event.last_reminder_sent_at:
        days_since = (timezone.now() - event.last_reminder_sent_at).days
        if days_since < 6:
            return False

    return True


def process_node_poll(node):
    now_in_tz = arrow.now(node.timezone or "UTC")
    if should_send_poll(node, now_in_tz):
        print(f"📊 Time to send poll for {node.name}")
        try:
            send_poll(node)
            node.last_poll_sent_at = timezone.now()
            node.save(update_fields=["last_poll_sent_at"])
            print(f"✅ Poll sent and timestamp updated for {node.name}")
        except Exception as e:
            print(f"❌ Error sending poll for {node.name}: {e}")
            sentry_sdk.capture_exception(e)


def process_node_events(node):
    from hackabot.apps.bot.models import Event

    now_in_tz = arrow.now(node.timezone or "UTC")
    events = Event.objects.filter(node=node)

    for event in events:
        if should_send_event_reminder(event, now_in_tz):
            print(f"🔔 Time to send {event.type} reminder for {node.name}")
            try:
                send_event_reminder(event)
                event.last_reminder_sent_at = timezone.now()
                event.save(update_fields=["last_reminder_sent_at"])
                print(f"✅ Reminder sent and timestamp updated for {event}")
            except Exception as e:
                print(f"❌ Error sending reminder for {event}: {e}")
                sentry_sdk.capture_exception(e)


def check_all_nodes():
    from hackabot.apps.bot.models import Node

    nodes = Node.objects.filter(group__isnull=False).select_related("group")

    for node in nodes:
        process_node_poll(node)
        process_node_events(node)


def run_worker():
    print("🤖🤖🤖 Hackabot Worker starting...")

    verify_webhook()

    print("Worker will check for tasks every minute...")

    while 1:
        try:
            check_all_nodes()
            time.sleep(60)
        except Exception as e:
            print("--- Worker error ---")
            print(traceback.format_exc())
            sentry_sdk.capture_exception(e)
            time.sleep(60)
