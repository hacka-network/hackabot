import json
import zlib
from datetime import datetime, time, timedelta

import pytest
import responses
from django.utils import timezone

from hackabot.apps.bot import telegram
from hackabot.apps.bot.models import (
    ActivityDay,
    Group,
    Node,
    Person,
    Poll,
    PollAnswer,
)
from hackabot.apps.bot.telegram import (
    HACKA_NETWORK_GLOBAL_CHAT_ID,
    TELEGRAM_API_BASE,
    WEEKLY_WIDGET_HTML,
    WEEKLY_WIDGET_MAX_NODES,
    _week_label,
    send_weekly_attendance_summary,
)

WIDGET_URL = f"{TELEGRAM_API_BASE}/bottesttoken/sendWidget"
MESSAGE_URL = f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage"


@pytest.fixture(autouse=True)
def _token(monkeypatch, settings):
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "testtoken")
    settings.MRR_10K_INVITE_LINK = ""


@pytest.fixture
def global_group(db):
    return Group.objects.create(
        telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID),
        display_name="Hacka* Network Global",
    )


def _node_with_attendance(name, emoji, count, group_id):
    group = Group.objects.create(telegram_id=group_id, display_name=name)
    node = Node.objects.create(
        group=group, name=name, emoji=emoji, timezone="UTC"
    )
    poll = Poll.objects.create(
        telegram_id=f"poll_{name}", node=node, question="?"
    )
    for i in range(count):
        person = Person.objects.create(
            telegram_id=group_id * 100 - i, first_name=f"{name} {i}"
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)
    return node


def _widget_payload(call):
    body = json.loads(call.request.body)
    return body, json.loads(body["public_data"])


def test_widget_html_fits_telegram_limits():
    raw = WEEKLY_WIDGET_HTML.encode()
    assert len(raw) <= 32768
    assert len(zlib.compress(raw, 9)) <= 4096
    assert "\x00" not in WEEKLY_WIDGET_HTML


def test_week_label_within_one_month():
    now = timezone.make_aware(datetime(2026, 11, 13, 3, 0))
    assert _week_label(now) == "6–12 NOV"


def test_week_label_across_months():
    now = timezone.make_aware(datetime(2026, 11, 4, 3, 0))
    assert _week_label(now) == "28 OCT–3 NOV"


@responses.activate
def test_sends_widget_with_weekly_stats(global_group):
    _node_with_attendance("Hackagu", "🇮🇩", 3, -1001)
    _node_with_attendance("Hackamai", "🇹🇭", 1, -1002)
    yapper = Person.objects.create(
        telegram_id=999, first_name="Yap", username="yap_per"
    )
    ActivityDay.objects.create(
        person=yapper,
        group=global_group,
        date=timezone.now().date(),
        message_count=42,
    )
    responses.add(
        responses.POST,
        WIDGET_URL,
        json=dict(ok=True, result=dict(message_id=1)),
    )

    assert send_weekly_attendance_summary() is True

    assert len(responses.calls) == 1
    body, data = _widget_payload(responses.calls[0])
    assert body["chat_id"] == global_group.telegram_id
    assert body["html"] == WEEKLY_WIDGET_HTML
    assert data["n"] == [["🇮🇩", "Hackagu", 3], ["🇹🇭", "Hackamai", 1]]
    assert data["m"] == 0
    assert data["y"] == ["@yap_per", 42]
    assert data["w"] == _week_label(timezone.now())
    assert len(body["public_data"].encode()) <= 1024


@responses.activate
def test_falls_back_to_text_when_widget_refused(global_group):
    _node_with_attendance("Hackagu", "🇮🇩", 2, -1001)
    responses.add(
        responses.POST,
        WIDGET_URL,
        json=dict(
            ok=False,
            error_code=400,
            description="Bad Request: INPUT_MEDIA_WIDGET_NOT_ALLOWED",
        ),
        status=400,
    )
    responses.add(
        responses.POST,
        MESSAGE_URL,
        json=dict(ok=True, result=dict(message_id=2)),
    )

    assert send_weekly_attendance_summary() is True

    assert [c.request.url for c in responses.calls] == [
        WIDGET_URL,
        MESSAGE_URL,
    ]
    text = json.loads(responses.calls[1].request.body)["text"]
    assert "2 people" in text
    assert "Hackagu" in text


@responses.activate
def test_caps_node_rows_and_counts_the_rest(global_group):
    extra = 3
    for i in range(WEEKLY_WIDGET_MAX_NODES + extra):
        _node_with_attendance(f"Node{i}", "", 1, -2000 - i)
    responses.add(
        responses.POST,
        WIDGET_URL,
        json=dict(ok=True, result=dict(message_id=1)),
    )

    send_weekly_attendance_summary()

    _, data = _widget_payload(responses.calls[0])
    assert len(data["n"]) == WEEKLY_WIDGET_MAX_NODES
    assert data["m"] == extra


@responses.activate
def test_streak_shows_the_node_of_the_latest_attendance(global_group):
    old_node = _node_with_attendance("Hackaditch", "🇬🇧", 0, -3001)
    new_node = _node_with_attendance("Hackalona", "🇪🇸", 0, -3002)
    person = Person.objects.create(
        telegram_id=777, first_name="Streaker", username="streaker"
    )
    now = timezone.now()
    current_monday = now.date() - timedelta(days=now.weekday())
    for weeks_back, node in [(0, new_node), (1, old_node), (2, old_node)]:
        poll = Poll.objects.create(
            telegram_id=f"poll_streak_{weeks_back}",
            node=node,
            question="?",
            is_attendance=True,
        )
        day = current_monday - timedelta(days=7 * weeks_back)
        Poll.objects.filter(pk=poll.pk).update(
            created=timezone.make_aware(datetime.combine(day, time(0, 0)))
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)
    responses.add(
        responses.POST,
        WIDGET_URL,
        json=dict(ok=True, result=dict(message_id=1)),
    )

    send_weekly_attendance_summary()

    _, data = _widget_payload(responses.calls[0])
    assert data["k"] == ["@streaker", "🇪🇸", 3]
