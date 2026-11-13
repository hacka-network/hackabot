import json
from datetime import timedelta

import arrow
import pytest
import responses
from django.test import Client
from django.utils import timezone
from requests import HTTPError

from hackabot.apps.bot import telegram
from hackabot.apps.bot.lurkers import process_lurkers
from hackabot.apps.bot.models import Group, GroupPerson, Person
from hackabot.apps.bot.telegram import (
    TELEGRAM_API_BASE,
    get_chat_member_status,
    remove_chat_member,
)
from hackabot.apps.worker import run

TEST_WEBHOOK_SECRET = "test-webhook-secret-123"
MRR_CHAT_ID = -1009999999999
ADMIN_CHAT_ID = -1008888888888
OTHER_CHAT_ID = -1001234567890


@pytest.fixture(autouse=True)
def mrr_settings(settings):
    settings.MRR_10K_CHAT_ID = MRR_CHAT_ID
    settings.MRR_ADMIN_CHAT_ID = ADMIN_CHAT_ID


@pytest.fixture
def mrr_group(db):
    return Group.objects.create(telegram_id=MRR_CHAT_ID, display_name="10k")


@pytest.fixture
def statuses(monkeypatch):
    by_user = dict()
    monkeypatch.setattr(
        "hackabot.apps.bot.lurkers.get_chat_member_status",
        lambda chat_id, user_id: by_user.get(user_id, "member"),
    )
    return by_user


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hackabot.apps.bot.lurkers.send",
        lambda chat_id, text, parse_mode="Markdown": calls.append(
            (chat_id, text, parse_mode)
        ),
    )
    return calls


@pytest.fixture
def removed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hackabot.apps.bot.lurkers.remove_chat_member",
        lambda chat_id, user_id: calls.append((chat_id, user_id)),
    )
    return calls


def months_ago(months, days=0):
    return arrow.utcnow().shift(months=-months, days=-days).datetime


def membership(
    group,
    user_id=555,
    username="lurky",
    first_name="Lurky",
    created=None,
    joined_at=None,
    last_message_at=None,
    warned_at=None,
    is_bot=False,
):
    person = Person.objects.create(
        telegram_id=user_id,
        username=username,
        first_name=first_name,
        is_bot=is_bot,
    )
    gp = GroupPerson.objects.create(
        group=group,
        person=person,
        joined_at=joined_at,
        last_message_at=last_message_at,
        inactivity_warned_at=warned_at,
    )
    if created:
        GroupPerson.objects.filter(pk=gp.pk).update(created=created)
        gp.refresh_from_db()
    return gp


class TestWarnings:
    def test_warns_member_silent_for_six_months(
        self, mrr_group, statuses, sent, removed
    ):
        gp = membership(
            mrr_group,
            created=months_ago(8),
            last_message_at=months_ago(6, days=1),
        )

        result = process_lurkers()

        assert result == dict(warned=1, removed=0)
        assert len(sent) == 1
        chat_id, text, parse_mode = sent[0]
        assert chat_id == MRR_CHAT_ID
        assert text.startswith("👋 Hey @lurky, you haven't said anything")
        assert "👀" not in text
        assert "6 months" in text
        assert "7 days" in text
        assert parse_mode == ""
        gp.refresh_from_db()
        assert gp.inactivity_warned_at is not None
        assert removed == []

    def test_does_not_warn_twice(self, mrr_group, statuses, sent, removed):
        membership(mrr_group, created=months_ago(8))

        process_lurkers()
        process_lurkers()

        assert len(sent) == 1

    def test_recent_poster_not_warned(self, mrr_group, statuses, sent):
        membership(
            mrr_group,
            created=months_ago(8),
            last_message_at=months_ago(5, days=27),
        )

        assert process_lurkers() == dict(warned=0, removed=0)
        assert sent == []

    def test_never_posted_counts_from_join(self, mrr_group, statuses, sent):
        membership(mrr_group, user_id=1, created=months_ago(6, days=1))
        membership(mrr_group, user_id=2, created=months_ago(5))

        assert process_lurkers() == dict(warned=1, removed=0)

    def test_rejoin_resets_clock(self, mrr_group, statuses, sent):
        membership(
            mrr_group,
            created=months_ago(12),
            last_message_at=months_ago(11),
            joined_at=months_ago(1),
        )

        assert process_lurkers() == dict(warned=0, removed=0)

    def test_mentions_person_without_username(self, mrr_group, statuses, sent):
        membership(
            mrr_group,
            user_id=777,
            username="",
            first_name="Quiet",
            created=months_ago(7),
        )

        process_lurkers()

        _, text, parse_mode = sent[0]
        assert "[Quiet](tg://user?id=777)" in text
        assert parse_mode == "Markdown"

    def test_skips_admins_and_bots(self, mrr_group, statuses, sent):
        membership(mrr_group, user_id=1, created=months_ago(7))
        membership(mrr_group, user_id=2, created=months_ago(7))
        membership(mrr_group, user_id=3, created=months_ago(7), is_bot=True)
        statuses[1] = "administrator"
        statuses[2] = "creator"

        assert process_lurkers() == dict(warned=0, removed=0)
        assert sent == []

    def test_marks_already_gone_members_as_left(
        self, mrr_group, statuses, sent
    ):
        gp = membership(mrr_group, created=months_ago(7))
        statuses[555] = "left"

        assert process_lurkers() == dict(warned=0, removed=0)
        gp.refresh_from_db()
        assert gp.left is True
        assert sent == []

    def test_ignores_left_members_and_other_groups(self, db, statuses, sent):
        mrr = Group.objects.create(telegram_id=MRR_CHAT_ID)
        other = Group.objects.create(telegram_id=OTHER_CHAT_ID)
        gp = membership(mrr, user_id=1, created=months_ago(7))
        gp.left = True
        gp.save()
        membership(other, user_id=2, created=months_ago(7))

        assert process_lurkers() == dict(warned=0, removed=0)

    def test_failed_send_leaves_unwarned(
        self, mrr_group, statuses, monkeypatch
    ):
        def fail(chat_id, text, parse_mode="Markdown"):
            raise HTTPError("400")

        monkeypatch.setattr("hackabot.apps.bot.lurkers.send", fail)
        gp = membership(mrr_group, created=months_ago(7))

        assert process_lurkers() == dict(warned=0, removed=0)
        gp.refresh_from_db()
        assert gp.inactivity_warned_at is None

    def test_disabled_without_chat_id(self, settings, mrr_group, sent):
        settings.MRR_10K_CHAT_ID = 0
        membership(mrr_group, created=months_ago(7))

        assert process_lurkers() == dict(warned=0, removed=0)

    def test_noop_when_group_unknown(self, db, sent):
        assert process_lurkers() == dict(warned=0, removed=0)


class TestRemovals:
    def test_removes_after_grace_period(
        self, mrr_group, statuses, sent, removed
    ):
        gp = membership(
            mrr_group,
            created=months_ago(8),
            warned_at=timezone.now() - timedelta(days=7),
        )

        assert process_lurkers() == dict(warned=0, removed=1)
        assert removed == [(MRR_CHAT_ID, 555)]
        gp.refresh_from_db()
        assert gp.left is True
        assert gp.inactivity_warned_at is None
        assert sent == [
            (
                ADMIN_CHAT_ID,
                "🧹 Removed @lurky from the $10k MRR group for 6 months"
                " inactivity",
                "",
            )
        ]

    def test_waits_during_grace_period(
        self, mrr_group, statuses, sent, removed
    ):
        membership(
            mrr_group,
            created=months_ago(8),
            warned_at=timezone.now() - timedelta(days=6),
        )

        assert process_lurkers() == dict(warned=0, removed=0)
        assert removed == []

    def test_skips_member_promoted_to_admin(
        self, mrr_group, statuses, sent, removed
    ):
        membership(
            mrr_group,
            created=months_ago(8),
            warned_at=timezone.now() - timedelta(days=10),
        )
        statuses[555] = "administrator"

        assert process_lurkers() == dict(warned=0, removed=0)
        assert removed == []

    def test_failed_removal_keeps_member(
        self, mrr_group, statuses, sent, monkeypatch
    ):
        def fail(chat_id, user_id):
            raise HTTPError("400")

        monkeypatch.setattr(
            "hackabot.apps.bot.lurkers.remove_chat_member", fail
        )
        gp = membership(
            mrr_group,
            created=months_ago(8),
            warned_at=timezone.now() - timedelta(days=10),
        )

        assert process_lurkers() == dict(warned=0, removed=0)
        gp.refresh_from_db()
        assert gp.left is False
        assert gp.inactivity_warned_at is not None

    def test_admin_notice_failure_still_counts(
        self, mrr_group, statuses, removed, monkeypatch
    ):
        def fail(chat_id, text, parse_mode="Markdown"):
            raise HTTPError("400")

        monkeypatch.setattr("hackabot.apps.bot.lurkers.send", fail)
        membership(
            mrr_group,
            created=months_ago(8),
            warned_at=timezone.now() - timedelta(days=10),
        )

        assert process_lurkers() == dict(warned=0, removed=1)

    def test_no_admin_notice_without_admin_chat(
        self, settings, mrr_group, statuses, sent, removed
    ):
        settings.MRR_ADMIN_CHAT_ID = 0
        membership(
            mrr_group,
            created=months_ago(8),
            warned_at=timezone.now() - timedelta(days=10),
        )

        assert process_lurkers() == dict(warned=0, removed=1)
        assert sent == []


@pytest.fixture
def webhook_client(monkeypatch):
    monkeypatch.setattr(
        "hackabot.apps.bot.telegram.TELEGRAM_WEBHOOK_SECRET",
        TEST_WEBHOOK_SECRET,
    )
    monkeypatch.setattr(
        "hackabot.apps.bot.views.send", lambda *args, **kwargs: None
    )
    return Client()


@pytest.fixture
def saved_replies(monkeypatch):
    calls = []

    def fake_send(chat_id, text, parse_mode="Markdown", **kwargs):
        calls.append((chat_id, text, parse_mode, kwargs))

    monkeypatch.setattr("hackabot.apps.bot.lurkers.send", fake_send)
    return calls


def text_update(text="still alive!"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "from": {"id": 555, "first_name": "Lurky", "username": "lurky"},
            "chat": {"id": OTHER_CHAT_ID, "type": "supergroup"},
            "date": int(timezone.now().timestamp()),
            "text": text,
        },
    }


def post_webhook(client, data):
    return client.post(
        "/webhook/telegram/",
        data=json.dumps(data),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TEST_WEBHOOK_SECRET,
    )


class TestWebhookClearsWarning:
    def _warned(self):
        group = Group.objects.create(telegram_id=OTHER_CHAT_ID)
        return membership(
            group,
            created=months_ago(8),
            warned_at=timezone.now() - timedelta(days=2),
        )

    def test_posting_clears_warning_and_announces_save(
        self, db, webhook_client, saved_replies
    ):
        gp = self._warned()

        post_webhook(webhook_client, text_update())

        gp.refresh_from_db()
        assert gp.inactivity_warned_at is None
        assert saved_replies == [
            (
                OTHER_CHAT_ID,
                "🛟 Thank you @lurky - you survived the purge",
                "",
                dict(reply_to_message_id=42),
            )
        ]

    def test_announces_save_only_once(self, db, webhook_client, saved_replies):
        self._warned()

        post_webhook(webhook_client, text_update())
        post_webhook(webhook_client, text_update("and again"))

        assert len(saved_replies) == 1

    def test_unwarned_post_is_not_announced(
        self, db, webhook_client, saved_replies
    ):
        group = Group.objects.create(telegram_id=OTHER_CHAT_ID)
        membership(group, created=months_ago(8))

        post_webhook(webhook_client, text_update())

        assert saved_replies == []

    def test_failed_save_announcement_still_clears_warning(
        self, db, webhook_client, monkeypatch
    ):
        def fail(*args, **kwargs):
            raise HTTPError("400")

        monkeypatch.setattr("hackabot.apps.bot.lurkers.send", fail)
        gp = self._warned()

        response = post_webhook(webhook_client, text_update())

        assert response.status_code == 200
        gp.refresh_from_db()
        assert gp.inactivity_warned_at is None

    def test_rejoin_sets_joined_at_and_clears_warning(
        self, db, webhook_client
    ):
        gp = self._warned()
        gp.left = True
        gp.save()

        post_webhook(
            webhook_client,
            {
                "update_id": 2,
                "chat_member": {
                    "chat": {"id": OTHER_CHAT_ID, "type": "supergroup"},
                    "from": {"id": 555, "first_name": "Lurky"},
                    "date": 1704067200,
                    "old_chat_member": {
                        "user": {"id": 555, "first_name": "Lurky"},
                        "status": "left",
                    },
                    "new_chat_member": {
                        "user": {"id": 555, "first_name": "Lurky"},
                        "status": "member",
                    },
                },
            },
        )

        gp.refresh_from_db()
        assert gp.left is False
        assert gp.inactivity_warned_at is None
        assert timezone.now() - gp.joined_at < timedelta(minutes=1)

    def test_status_change_without_join_keeps_joined_at(
        self, db, webhook_client
    ):
        gp = self._warned()

        post_webhook(
            webhook_client,
            {
                "update_id": 3,
                "chat_member": {
                    "chat": {"id": OTHER_CHAT_ID, "type": "supergroup"},
                    "from": {"id": 1, "first_name": "Admin"},
                    "date": 1704067200,
                    "old_chat_member": {
                        "user": {"id": 555, "first_name": "Lurky"},
                        "status": "member",
                    },
                    "new_chat_member": {
                        "user": {"id": 555, "first_name": "Lurky"},
                        "status": "restricted",
                    },
                },
            },
        )

        gp.refresh_from_db()
        assert gp.joined_at is None
        assert gp.inactivity_warned_at is not None

    def test_new_chat_members_sets_joined_at(self, db, webhook_client):
        gp = self._warned()

        post_webhook(
            webhook_client,
            {
                "update_id": 4,
                "message": {
                    "message_id": 2,
                    "from": {"id": 555, "first_name": "Lurky"},
                    "chat": {"id": OTHER_CHAT_ID, "type": "supergroup"},
                    "date": 1704067200,
                    "new_chat_members": [
                        {"id": 555, "first_name": "Lurky"},
                    ],
                },
            },
        )

        gp.refresh_from_db()
        assert gp.joined_at is not None
        assert gp.inactivity_warned_at is None


class TestTelegramHelpers:
    @pytest.fixture(autouse=True)
    def token(self, monkeypatch):
        monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "testtoken")

    @responses.activate
    def test_get_chat_member_status(self):
        responses.add(
            responses.POST,
            f"{TELEGRAM_API_BASE}/bottesttoken/getChatMember",
            json=dict(ok=True, result=dict(status="member")),
        )

        assert get_chat_member_status(MRR_CHAT_ID, 555) == "member"

    @responses.activate
    def test_remove_chat_member_uses_unban(self):
        responses.add(
            responses.POST,
            f"{TELEGRAM_API_BASE}/bottesttoken/unbanChatMember",
            json=dict(ok=True, result=True),
        )

        remove_chat_member(MRR_CHAT_ID, 555)

        body = json.loads(responses.calls[0].request.body)
        assert body == dict(chat_id=MRR_CHAT_ID, user_id=555)

    @responses.activate
    def test_remove_chat_member_raises_on_error(self):
        responses.add(
            responses.POST,
            f"{TELEGRAM_API_BASE}/bottesttoken/unbanChatMember",
            json=dict(ok=False, description="not enough rights"),
            status=400,
        )

        with pytest.raises(HTTPError):
            remove_chat_member(MRR_CHAT_ID, 555)


class TestWorkerSchedule:
    @pytest.fixture
    def calls(self, monkeypatch):
        calls = []

        def fake_process_lurkers():
            calls.append(1)
            return dict(warned=0, removed=0)

        monkeypatch.setattr(run, "_last_lurker_check_on", None)
        monkeypatch.setattr(run, "process_lurkers", fake_process_lurkers)
        return calls

    def test_runs_once_a_day_at_check_hour(self, calls):
        at_hour = arrow.get(2026, 11, 13, run.LURKER_CHECK_HOUR)

        run.process_lurker_check(at_hour)
        run.process_lurker_check(at_hour.shift(minutes=30))
        run.process_lurker_check(at_hour.shift(days=1))

        assert len(calls) == 2

    def test_skips_other_hours(self, calls):
        other = arrow.get(2026, 11, 13, run.LURKER_CHECK_HOUR + 1)

        run.process_lurker_check(other)

        assert calls == []
