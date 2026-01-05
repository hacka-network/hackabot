import time
import traceback

import arrow
import sentry_sdk
from django.utils import timezone

from hackabot.apps.bot.telegram import (
    HACKA_NETWORK_GLOBAL_CHAT_ID,
    send_event_reminder,
    send_poll,
    send_weekly_attendance_summary,
    verify_webhook,
)

POLL_DAY = 0  # Monday (0 = Monday in arrow weekday)
POLL_HOUR = 7
POLL_MINUTE = 0
POLL_TIMEZONE = "UTC"  # All polls sent at 7am UTC (= 3pm Bali)
EVENT_DAY = 3  # Thursday
REMINDER_MINUTES_BEFORE = 30
SUMMARY_DAY = 4  # Friday
SUMMARY_HOUR = 7
SUMMARY_MINUTE = 0


def should_send_poll(node, now_utc):
    if now_utc.weekday() != POLL_DAY:
        return False

    if now_utc.hour != POLL_HOUR:
        return False

    if now_utc.minute != POLL_MINUTE:
        return False

    if node.last_poll_sent_at:
        days_since = (timezone.now() - node.last_poll_sent_at).days
        if days_since < 6:
            return False

    return True


def should_send_event_reminder(event, now_in_tz):
    if now_in_tz.weekday() != EVENT_DAY:
        return False

    # Calculate reminder time (30 mins before event)
    event_datetime = now_in_tz.replace(
        hour=event.time.hour,
        minute=event.time.minute,
        second=0,
        microsecond=0,
    )
    reminder_datetime = event_datetime.shift(minutes=-REMINDER_MINUTES_BEFORE)

    if now_in_tz.hour != reminder_datetime.hour:
        return False

    if now_in_tz.minute != reminder_datetime.minute:
        return False

    if event.last_reminder_sent_at:
        days_since = (timezone.now() - event.last_reminder_sent_at).days
        if days_since < 6:
            return False

    return True


def process_node_poll(node):
    now_utc = arrow.now(POLL_TIMEZONE)
    if should_send_poll(node, now_utc):
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


def should_send_weekly_summary(global_group, now_utc):
    if now_utc.weekday() != SUMMARY_DAY:
        return False

    if now_utc.hour != SUMMARY_HOUR:
        return False

    if now_utc.minute != SUMMARY_MINUTE:
        return False

    if global_group.last_weekly_summary_sent_at:
        days_since = (
            timezone.now() - global_group.last_weekly_summary_sent_at
        ).days
        if days_since < 6:
            return False

    return True


def process_weekly_summary():
    from hackabot.apps.bot.models import Group

    now_utc = arrow.now(POLL_TIMEZONE)

    try:
        global_group = Group.objects.get(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID)
        )
    except Group.DoesNotExist:
        return

    if should_send_weekly_summary(global_group, now_utc):
        print("📊 Time to send weekly attendance summary")
        try:
            result = send_weekly_attendance_summary()
            if result:
                global_group.last_weekly_summary_sent_at = timezone.now()
                global_group.save(update_fields=["last_weekly_summary_sent_at"])
                print("✅ Weekly summary sent and timestamp updated")
        except Exception as e:
            print(f"❌ Error sending weekly summary: {e}")
            sentry_sdk.capture_exception(e)


def check_all_nodes():
    from hackabot.apps.bot.models import Node

    nodes = Node.objects.filter(group__isnull=False).select_related("group")

    for node in nodes:
        process_node_poll(node)
        process_node_events(node)

    process_weekly_summary()


def run_worker():
    print("🤖🤖🤖 Hackabot Worker starting...")

    verify_webhook()

    print("Worker will check for tasks every 30 seconds...")

    while 1:
        try:
            check_all_nodes()
            time.sleep(30)
        except Exception as e:
            print("--- Worker error ---")
            print(traceback.format_exc())
            sentry_sdk.capture_exception(e)
            time.sleep(30)
