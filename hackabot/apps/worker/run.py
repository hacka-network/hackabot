import time
import traceback
from functools import partial, wraps

import arrow
import schedule
import sentry_sdk

from hackabot.apps.bot.telegram import (
    send_event_reminder,
    send_poll,
    verify_webhook,
)


def make_time(hour, minute, tz, second=0):
    t = (
        arrow.now()
        .to(tz or "UTC")
        .replace(hour=hour, minute=minute, second=second)
        .to("local")
        .format("HH:mm:ss")
    )
    print(f"make_time({hour}, {minute}, {tz}, second={second}) -> {t}")
    return t


def wrap_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"Error in {func.__name__}: {e.__class__.__name__}: {e}")
            sentry_sdk.capture_exception(e)
            return None

    return wrapper


def schedule_node_poll(node):
    tz = node.timezone
    poll_hour = 15
    poll_minute = 0

    job_time = make_time(poll_hour, poll_minute, tz)
    job = partial(send_poll, node)
    job.__name__ = f"send_poll_{node.name}"

    schedule.every().monday.at(job_time).do(wrap_errors(job))
    print(f"  Scheduled poll for {node.name} at {job_time} (Monday)")


def schedule_node_events(node):
    from hackabot.apps.bot.models import Event

    events = Event.objects.filter(node=node)
    tz = node.timezone

    for event in events:
        hour = event.time.hour
        minute = event.time.minute
        job_time = make_time(hour, minute, tz)

        job = partial(send_event_reminder, event)
        job.__name__ = f"send_{event.type}_{node.name}"

        schedule.every().thursday.at(job_time).do(wrap_errors(job))
        print(f"  Scheduled {event.type} for {node.name} at {job_time} (Thursday)")


def schedule_all_nodes():
    from hackabot.apps.bot.models import Node

    nodes = Node.objects.filter(group__isnull=False).select_related("group")
    print(f"Found {nodes.count()} nodes with groups")

    for node in nodes:
        print(f"Scheduling {node.name}...")
        schedule_node_poll(node)
        schedule_node_events(node)


def run_worker():
    print("🤖🤖🤖 Hackabot Worker starting...")

    # Verify webhook is set correctly
    verify_webhook()

    # Schedule all nodes from DB
    schedule_all_nodes()

    print("All tasks scheduled:")
    for job in schedule.jobs:
        print(f"  {job}")

    while 1:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            print("--- Worker error ---")
            print(traceback.format_exc())
            sentry_sdk.capture_exception(e)
