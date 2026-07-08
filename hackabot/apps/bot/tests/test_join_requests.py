import json
from datetime import date, timedelta

import pytest
import responses
from django.test import Client
from django.utils import timezone as django_timezone
from requests import HTTPError

from hackabot.apps.bot.models import JoinRequest, Person
from hackabot.apps.bot.views import expire_stale_join_requests
from hackabot.apps.bot.stripe_mrr import (
    FX_RATES_URL,
    _fx_cache,
    extract_stripe_link,
    verify_mrr,
)

TEST_WEBHOOK_SECRET = "test-webhook-secret-123"
MRR_CHAT_ID = -1009999999999
ADMIN_CHAT_ID = -1008888888888


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def set_webhook_secret(monkeypatch):
    monkeypatch.setattr(
        "hackabot.apps.bot.telegram.TELEGRAM_WEBHOOK_SECRET",
        TEST_WEBHOOK_SECRET,
    )


@pytest.fixture(autouse=True)
def mrr_settings(settings):
    settings.MRR_10K_CHAT_ID = MRR_CHAT_ID
    settings.MRR_ADMIN_CHAT_ID = ADMIN_CHAT_ID


@pytest.fixture(autouse=True)
def clear_fx_cache():
    _fx_cache.update(date=None, rates=None)


@pytest.fixture
def sent_messages(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "hackabot.apps.bot.views.send",
        lambda chat_id, text, parse_mode="Markdown": sent.append(
            (chat_id, text)
        ),
    )
    return sent


@pytest.fixture
def sent_keyboards(monkeypatch):
    sent = []

    def fake_send_with_keyboard(chat_id, text, keyboard):
        sent.append((chat_id, text, keyboard))
        return 999

    monkeypatch.setattr(
        "hackabot.apps.bot.views.send_with_keyboard",
        fake_send_with_keyboard,
    )
    return sent


@pytest.fixture(autouse=True)
def deleted_messages(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hackabot.apps.bot.views.delete_message",
        lambda chat_id, message_id: calls.append((chat_id, message_id)),
    )
    return calls


@pytest.fixture(autouse=True)
def edited_messages(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hackabot.apps.bot.views.edit_message_text",
        lambda chat_id, message_id, text: calls.append(
            (chat_id, message_id, text)
        ),
    )
    return calls


@pytest.fixture
def answered_callbacks(monkeypatch):
    answered = []
    monkeypatch.setattr(
        "hackabot.apps.bot.views.answer_callback_query",
        lambda callback_query_id, text=None: answered.append(
            (callback_query_id, text)
        ),
    )
    return answered


@pytest.fixture
def approved(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hackabot.apps.bot.views.approve_chat_join_request",
        lambda chat_id, user_id: calls.append((chat_id, user_id)),
    )
    return calls


@pytest.fixture
def declined(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hackabot.apps.bot.views.decline_chat_join_request",
        lambda chat_id, user_id: calls.append((chat_id, user_id)),
    )
    return calls


@pytest.fixture
def copied(monkeypatch):
    calls = []

    def fake_copy(chat_id, from_chat_id, message_id, caption, keyboard=None):
        calls.append((chat_id, from_chat_id, message_id, caption, keyboard))
        return 888

    monkeypatch.setattr("hackabot.apps.bot.views.copy_message", fake_copy)
    return calls


def post_webhook(client, data):
    return client.post(
        "/webhook/telegram/",
        data=json.dumps(data),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TEST_WEBHOOK_SECRET,
    )


def join_request_update(chat_id=MRR_CHAT_ID, user_id=555):
    return {
        "update_id": 2001,
        "chat_join_request": {
            "chat": {"id": chat_id, "type": "supergroup"},
            "from": {
                "id": user_id,
                "first_name": "Bob",
                "username": "bob10k",
                "is_bot": False,
            },
            "date": 1704067200,
        },
    }


def dm_update(user_id=555, text="hello"):
    return {
        "update_id": 2002,
        "message": {
            "message_id": 10,
            "from": {
                "id": user_id,
                "first_name": "Bob",
                "username": "bob10k",
                "is_bot": False,
            },
            "chat": {"id": user_id, "type": "private"},
            "date": 1704067200,
            "text": text,
        },
    }


def photo_dm_update(user_id=555, caption="", message_id=42):
    return {
        "update_id": 2004,
        "message": {
            "message_id": message_id,
            "from": {
                "id": user_id,
                "first_name": "Bob",
                "username": "bob10k",
                "is_bot": False,
            },
            "chat": {"id": user_id, "type": "private"},
            "date": 1704067200,
            "photo": [{"file_id": "photo123", "width": 90, "height": 60}],
            "caption": caption,
        },
    }


def callback_update(callback_data, callback_query_id="cb1"):
    return {
        "update_id": 2003,
        "callback_query": {
            "id": callback_query_id,
            "from": {"id": 999, "first_name": "Admin"},
            "data": callback_data,
            "message": {
                "message_id": 20,
                "chat": {"id": ADMIN_CHAT_ID, "type": "supergroup"},
            },
        },
    }


def pending_request(user_id=555):
    person = Person.objects.create(
        telegram_id=user_id, first_name="Bob", username="bob10k"
    )
    return JoinRequest.objects.create(
        person=person,
        chat_id=MRR_CHAT_ID,
        status=JoinRequest.STATUS_PENDING,
    )


def chat_member_update(
    user_id=555,
    username="bob10k",
    first_name="Bob",
    old_status="left",
    new_status="member",
    chat_id=MRR_CHAT_ID,
    is_bot=False,
):
    user = dict(id=user_id, first_name=first_name, is_bot=is_bot)
    if username:
        user["username"] = username
    return {
        "update_id": 3001,
        "chat_member": {
            "chat": {
                "id": chat_id,
                "type": "supergroup",
                "title": "Hacka+ | $10k MRR",
            },
            "from": {"id": 999, "first_name": "Admin"},
            "date": 1704067200,
            "old_chat_member": {"status": old_status, "user": user},
            "new_chat_member": {"status": new_status, "user": user},
        },
    }


class TestChatJoinRequest:
    def test_creates_pending_request_and_dms_user(
        self, client, db, sent_messages
    ):
        response = post_webhook(client, join_request_update())

        assert response.status_code == 200
        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_PENDING
        assert join_request.chat_id == MRR_CHAT_ID
        assert join_request.person.telegram_id == 555
        assert len(sent_messages) == 1
        assert sent_messages[0][0] == 555
        assert "Stripe" in sent_messages[0][1]

    def test_ignores_other_chats(self, client, db, sent_messages):
        post_webhook(client, join_request_update(chat_id=-123))

        assert JoinRequest.objects.count() == 0
        assert sent_messages == []

    def test_repeat_request_resets_row(self, client, db, sent_messages):
        join_request = pending_request()
        join_request.status = JoinRequest.STATUS_DECLINED
        join_request.proof_text = "old proof"
        join_request.save()

        post_webhook(client, join_request_update())

        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_PENDING
        assert join_request.proof_text == ""
        assert JoinRequest.objects.count() == 1

    def test_undmable_user_routes_to_review(
        self, client, db, sent_keyboards, monkeypatch
    ):
        def raise_http_error(chat_id, text):
            raise HTTPError("403 Forbidden")

        monkeypatch.setattr("hackabot.apps.bot.views.send", raise_http_error)

        post_webhook(client, join_request_update())

        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_REVIEW
        assert "couldn't DM" in join_request.reason
        assert len(sent_keyboards) == 1
        assert sent_keyboards[0][0] == ADMIN_CHAT_ID


class TestProofDM:
    def test_valid_link_auto_approves(
        self, client, db, sent_messages, approved, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.verify_mrr",
            lambda slug, token: (True, "MRR verified at $12,000"),
        )
        pending_request()

        post_webhook(
            client,
            dm_update(text="https://profile.stripe.com/acme/AbC123"),
        )

        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_APPROVED
        assert join_request.proof_text == ""
        assert join_request.reason == "auto-verified"
        assert approved == [(MRR_CHAT_ID, 555)]
        assert "Welcome" in sent_messages[0][1]

    def test_failed_verification_goes_to_review(
        self, client, db, sent_messages, sent_keyboards, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.verify_mrr",
            lambda slug, token: (False, "MRR is $500, below $10,000"),
        )
        pending_request()

        post_webhook(
            client,
            dm_update(text="https://profile.stripe.com/acme/AbC123"),
        )

        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_REVIEW
        assert "below" in join_request.reason
        assert len(sent_keyboards) == 1
        chat_id, text, keyboard = sent_keyboards[0]
        assert chat_id == ADMIN_CHAT_ID
        assert "below" in text
        assert keyboard[0][0]["callback_data"] == (
            f"jr_approve:{join_request.id}"
        )
        assert keyboard[0][1]["callback_data"] == (
            f"jr_decline:{join_request.id}"
        )

    def test_no_link_goes_to_review(
        self, client, db, sent_messages, sent_keyboards
    ):
        pending_request()

        post_webhook(client, dm_update(text="I use Paddle, MRR is $15k"))

        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_REVIEW
        assert join_request.proof_text == "I use Paddle, MRR is $15k"
        assert len(sent_keyboards) == 1
        assert "Paddle" in sent_keyboards[0][1]

    def test_review_card_escapes_markdown(
        self, client, db, sent_messages, sent_keyboards
    ):
        person = Person.objects.create(
            telegram_id=777, first_name="Al", username="al_pha"
        )
        JoinRequest.objects.create(
            person=person,
            chat_id=MRR_CHAT_ID,
            status=JoinRequest.STATUS_PENDING,
        )

        update = dm_update(user_id=777, text="MRR is 12k_month *honest*")
        update["message"]["from"]["username"] = "al_pha"
        post_webhook(client, update)

        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_REVIEW
        assert len(sent_keyboards) == 1
        card_text = sent_keyboards[0][1]
        assert "@al\\_pha" in card_text
        assert "12k\\_month" in card_text
        assert "\\*honest\\*" in card_text

    def test_admin_notify_failure_does_not_crash_webhook(
        self, client, db, sent_messages, monkeypatch
    ):
        def raise_http_error(chat_id, text, keyboard):
            raise HTTPError("400 Bad Request")

        monkeypatch.setattr(
            "hackabot.apps.bot.views.send_with_keyboard",
            raise_http_error,
        )
        pending_request()

        response = post_webhook(
            client, dm_update(text="I use Paddle, no stripe")
        )

        assert response.status_code == 200
        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_REVIEW

    def test_photo_proof_forwarded_to_admin(
        self, client, db, sent_messages, sent_keyboards, copied
    ):
        pending_request()

        post_webhook(
            client,
            photo_dm_update(caption="here is my dashboard", message_id=42),
        )

        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_REVIEW
        assert join_request.proof_text == "here is my dashboard"
        # text card carries the buttons, photo forwarded separately
        assert len(sent_keyboards) == 1
        card_chat, card_text, card_keyboard = sent_keyboards[0]
        assert card_chat == ADMIN_CHAT_ID
        assert card_keyboard[0][0]["callback_data"] == (
            f"jr_approve:{join_request.id}"
        )
        assert len(copied) == 1
        chat_id, from_chat_id, message_id, caption, keyboard = copied[0]
        assert chat_id == ADMIN_CHAT_ID
        assert from_chat_id == 555
        assert message_id == 42
        assert keyboard is None
        assert "Proof from" in caption
        assert join_request.admin_message_ids == [888]

    def test_photo_with_valid_link_caption_auto_approves(
        self, client, db, sent_messages, approved, copied, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.verify_mrr",
            lambda slug, token: (True, "MRR verified at $12,000"),
        )
        pending_request()

        post_webhook(
            client,
            photo_dm_update(caption="https://profile.stripe.com/acme/AbC123"),
        )

        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_APPROVED
        assert approved == [(MRR_CHAT_ID, 555)]
        assert copied == []

    def test_approve_api_failure_falls_back_to_review(
        self, client, db, sent_messages, sent_keyboards, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.verify_mrr",
            lambda slug, token: (True, "MRR verified at $12,000"),
        )

        def raise_http_error(chat_id, user_id):
            raise HTTPError("400 Bad Request")

        monkeypatch.setattr(
            "hackabot.apps.bot.views.approve_chat_join_request",
            raise_http_error,
        )
        pending_request()

        post_webhook(
            client,
            dm_update(text="https://profile.stripe.com/acme/AbC123"),
        )

        join_request = JoinRequest.objects.get()
        assert join_request.status == JoinRequest.STATUS_REVIEW
        assert len(sent_keyboards) == 1

    def test_dm_without_pending_request_gets_node_prompt(
        self, client, db, sent_messages
    ):
        post_webhook(client, dm_update(text="hello"))

        assert JoinRequest.objects.count() == 0
        assert "hacka.network" in sent_messages[0][1]

    def test_extra_proof_within_window_forwarded(
        self, client, db, sent_messages, copied
    ):
        person = Person.objects.create(
            telegram_id=555, first_name="Bob", username="bob10k"
        )
        JoinRequest.objects.create(
            person=person,
            chat_id=MRR_CHAT_ID,
            status=JoinRequest.STATUS_REVIEW,
            proof_started_at=django_timezone.now(),
        )

        post_webhook(
            client, photo_dm_update(caption="another pic", message_id=43)
        )

        assert len(copied) == 1
        chat_id, from_chat_id, message_id, caption, keyboard = copied[0]
        assert chat_id == ADMIN_CHAT_ID
        assert from_chat_id == 555
        assert message_id == 43
        assert keyboard is None

    def test_extra_proof_after_window_ignored(
        self, client, db, sent_messages, copied
    ):
        person = Person.objects.create(
            telegram_id=555, first_name="Bob", username="bob10k"
        )
        JoinRequest.objects.create(
            person=person,
            chat_id=MRR_CHAT_ID,
            status=JoinRequest.STATUS_REVIEW,
            proof_started_at=(django_timezone.now() - timedelta(seconds=30)),
        )

        post_webhook(
            client, photo_dm_update(caption="late pic", message_id=44)
        )

        assert copied == []
        assert "hacka.network" in sent_messages[0][1]


class TestAdminCallbacks:
    def test_approve_callback(
        self, client, db, sent_messages, answered_callbacks, approved
    ):
        join_request = pending_request()
        join_request.status = JoinRequest.STATUS_REVIEW
        join_request.save()

        post_webhook(client, callback_update(f"jr_approve:{join_request.id}"))

        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_APPROVED
        assert join_request.proof_text == ""
        assert join_request.admin_message_ids == []
        assert approved == [(MRR_CHAT_ID, 555)]
        assert answered_callbacks == [("cb1", "Approved ✅")]
        user_msgs = [m for m in sent_messages if m[0] == 555]
        assert "approved" in user_msgs[0][1]

    def test_approve_deletes_evidence_and_edits_card(
        self,
        client,
        db,
        sent_messages,
        answered_callbacks,
        approved,
        deleted_messages,
        edited_messages,
    ):
        join_request = pending_request()
        join_request.status = JoinRequest.STATUS_REVIEW
        join_request.admin_message_ids = [18, 19]
        join_request.save()

        post_webhook(client, callback_update(f"jr_approve:{join_request.id}"))

        assert deleted_messages == [
            (ADMIN_CHAT_ID, 18),
            (ADMIN_CHAT_ID, 19),
        ]
        assert edited_messages[0][0] == ADMIN_CHAT_ID
        assert edited_messages[0][1] == 20
        assert "✅ Approved" in edited_messages[0][2]
        assert "Evidence removed" in edited_messages[0][2]
        join_request.refresh_from_db()
        assert join_request.admin_message_ids == []

    def test_decline_callback(
        self, client, db, sent_messages, answered_callbacks, declined
    ):
        join_request = pending_request()
        join_request.status = JoinRequest.STATUS_REVIEW
        join_request.save()

        post_webhook(client, callback_update(f"jr_decline:{join_request.id}"))

        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_DECLINED
        assert declined == [(MRR_CHAT_ID, 555)]
        assert answered_callbacks == [("cb1", "Declined ❌")]
        user_msgs = [m for m in sent_messages if m[0] == 555]
        assert "declined" in user_msgs[0][1]

    def test_double_tap_answers_without_action(
        self, client, db, sent_messages, answered_callbacks, approved
    ):
        join_request = pending_request()
        join_request.status = JoinRequest.STATUS_APPROVED
        join_request.save()

        post_webhook(client, callback_update(f"jr_approve:{join_request.id}"))

        assert approved == []
        assert answered_callbacks == [("cb1", "Already approved.")]
        assert sent_messages == []

    def test_unknown_request_id(
        self, client, db, answered_callbacks, approved
    ):
        post_webhook(client, callback_update("jr_approve:99999"))

        assert approved == []
        assert answered_callbacks == [("cb1", "Request not found.")]

    def test_expired_approve_shows_failure_toast(
        self, client, db, sent_messages, answered_callbacks, monkeypatch
    ):
        def raise_http_error(chat_id, user_id):
            raise HTTPError("400 Bad Request")

        monkeypatch.setattr(
            "hackabot.apps.bot.views.approve_chat_join_request",
            raise_http_error,
        )
        join_request = pending_request()
        join_request.status = JoinRequest.STATUS_REVIEW
        join_request.save()

        post_webhook(client, callback_update(f"jr_approve:{join_request.id}"))

        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_REVIEW
        assert "expired" in answered_callbacks[0][1]
        assert sent_messages == []


class TestExtractStripeLink:
    def test_extracts_slug_and_token(self):
        link = extract_stripe_link(
            "here you go https://profile.stripe.com/acme/AbC123 thanks"
        )
        assert link == ("acme", "AbC123")

    def test_no_link(self):
        assert extract_stripe_link("no link here") is None
        assert extract_stripe_link("") is None
        assert extract_stripe_link(None) is None


def stripe_response(
    chart_identifier="bento_mrr_volume",
    livemode=True,
    currency="usd",
    total=1200000,
    latest_date=None,
):
    if latest_date is None:
        latest_date = date.today().isoformat()
    return dict(
        chart_identifier=chart_identifier,
        livemode=livemode,
        chart_configuration=json.dumps(dict(currency=currency)),
        metric_data=json.dumps(
            [dict(start_time=latest_date, total=total, group_id="All")]
        ),
    )


def mock_stripe(body, status=200):
    responses.add(
        responses.GET,
        "https://api.stripe.com/v2/xauth_/shareable_metrics/acme/AbC123",
        json=body,
        status=status,
    )


class TestVerifyMrr:
    @responses.activate
    def test_valid_usd_chart(self):
        mock_stripe(stripe_response())

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is True
        assert "$12,000" in reason

    @responses.activate
    def test_eur_conversion(self):
        mock_stripe(
            stripe_response(currency="eur", total=900000),
        )

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is True

    @responses.activate
    def test_live_fx_rates_used_when_available(self):
        # live rate says 1 USD = 1 EUR, so €9,000 is below $10k even
        # though the fallback rate (1.17) would put it above
        responses.add(
            responses.GET,
            FX_RATES_URL,
            json=dict(base="USD", rates=dict(EUR=1.0)),
            status=200,
        )
        mock_stripe(stripe_response(currency="eur", total=900000))

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is False
        assert "below" in reason

    @responses.activate
    def test_below_threshold(self):
        mock_stripe(stripe_response(total=50000))

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is False
        assert "below" in reason

    @responses.activate
    def test_wrong_chart(self):
        mock_stripe(
            stripe_response(chart_identifier="bento_net_volume"),
        )

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is False
        assert "not MRR" in reason

    @responses.activate
    def test_test_mode_chart(self):
        mock_stripe(stripe_response(livemode=False))

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is False
        assert "test mode" in reason

    @responses.activate
    def test_unknown_currency(self):
        mock_stripe(stripe_response(currency="jpy"))

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is False
        assert "currency" in reason

    @responses.activate
    def test_stale_data(self):
        old = (date.today() - timedelta(days=90)).isoformat()
        mock_stripe(stripe_response(latest_date=old))

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is False
        assert "stale" in reason

    @responses.activate
    def test_api_error(self):
        mock_stripe(dict(error="nope"), status=500)

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is False
        assert "could not fetch" in reason

    @responses.activate
    def test_malformed_response(self):
        mock_stripe(dict(unexpected="shape"))

        verified, reason = verify_mrr("acme", "AbC123")

        assert verified is False
        assert "could not fetch" in reason


class TestExpireStaleJoinRequests:
    def _stale_pending(self, user_id=555, hours_ago=2):
        join_request = pending_request(user_id=user_id)
        join_request.pending_since = django_timezone.now() - timedelta(
            hours=hours_ago
        )
        join_request.save()
        return join_request

    def test_expires_stale_pending(self, db, declined, sent_messages):
        join_request = self._stale_pending()

        expired = expire_stale_join_requests()

        assert expired == 1
        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_DECLINED
        assert "expired" in join_request.reason
        assert declined == [(MRR_CHAT_ID, 555)]
        assert len(sent_messages) == 1
        assert sent_messages[0][0] == 555
        assert "timed out" in sent_messages[0][1]

    def test_fresh_pending_untouched(self, db, declined, sent_messages):
        join_request = pending_request()
        join_request.pending_since = django_timezone.now() - timedelta(
            minutes=30
        )
        join_request.save()

        expired = expire_stale_join_requests()

        assert expired == 0
        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_PENDING
        assert declined == []
        assert sent_messages == []

    def test_review_and_terminal_untouched(self, db, declined, sent_messages):
        old = django_timezone.now() - timedelta(hours=5)
        for user_id, status in [
            (601, JoinRequest.STATUS_REVIEW),
            (602, JoinRequest.STATUS_APPROVED),
            (603, JoinRequest.STATUS_DECLINED),
        ]:
            join_request = pending_request(user_id=user_id)
            join_request.status = status
            join_request.pending_since = old
            join_request.save()

        expired = expire_stale_join_requests()

        assert expired == 0
        assert declined == []
        assert sent_messages == []

    def test_null_pending_since_falls_back_to_created(
        self, db, declined, sent_messages
    ):
        join_request = pending_request()
        join_request.pending_since = None
        join_request.save()
        old = django_timezone.now() - timedelta(hours=3)
        JoinRequest.objects.filter(id=join_request.id).update(created=old)

        expired = expire_stale_join_requests()

        assert expired == 1
        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_DECLINED

    def test_decline_failure_still_declines(
        self, db, sent_messages, monkeypatch
    ):
        def raise_http_error(chat_id, user_id):
            raise HTTPError("400 Bad Request")

        monkeypatch.setattr(
            "hackabot.apps.bot.views.decline_chat_join_request",
            raise_http_error,
        )
        join_request = self._stale_pending()

        expired = expire_stale_join_requests()

        assert expired == 1
        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_DECLINED
        assert len(sent_messages) == 1

    def test_re_request_refreshes_pending_since(
        self, client, db, declined, sent_messages
    ):
        join_request = pending_request()
        old = django_timezone.now() - timedelta(hours=5)
        join_request.status = JoinRequest.STATUS_DECLINED
        join_request.pending_since = old
        join_request.save()
        JoinRequest.objects.filter(id=join_request.id).update(created=old)

        post_webhook(client, join_request_update())

        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_PENDING

        expired = expire_stale_join_requests()

        assert expired == 0
        assert declined == []
        join_request.refresh_from_db()
        assert join_request.status == JoinRequest.STATUS_PENDING


class TestMrrWelcome:
    def test_welcomes_joiner_with_username(self, client, db, sent_messages):
        post_webhook(client, chat_member_update(username="mitchnick"))

        assert len(sent_messages) == 1
        chat_id, text = sent_messages[0]
        assert chat_id == MRR_CHAT_ID
        assert "@mitchnick" in text
        assert "intro yourself and what you're building" in text
        assert "identifiable" in text
        assert Person.objects.get(telegram_id=555).onboarded is True

    def test_welcomes_joiner_without_username(self, client, db, sent_messages):
        post_webhook(
            client, chat_member_update(username=None, first_name="Mitch")
        )

        assert len(sent_messages) == 1
        _, text = sent_messages[0]
        assert "[Mitch](tg://user?id=555)" in text
        assert "intro yourself" in text

    def test_already_onboarded_not_rewelcomed(self, client, db, sent_messages):
        Person.objects.create(
            telegram_id=555,
            first_name="Bob",
            username="bob10k",
            onboarded=True,
        )

        post_webhook(client, chat_member_update(username="bob10k"))

        assert sent_messages == []

    def test_bot_join_not_welcomed(self, client, db, sent_messages):
        post_webhook(client, chat_member_update(is_bot=True))

        assert sent_messages == []

    def test_status_change_not_a_join(self, client, db, sent_messages):
        post_webhook(
            client,
            chat_member_update(
                old_status="member", new_status="administrator"
            ),
        )

        assert sent_messages == []

    def test_member_leaving_not_welcomed(self, client, db, sent_messages):
        post_webhook(
            client,
            chat_member_update(old_status="member", new_status="left"),
        )

        assert sent_messages == []

    def test_join_to_other_group_no_mrr_message(
        self, client, db, sent_messages
    ):
        post_webhook(
            client,
            chat_member_update(chat_id=-1002222222222, username="bob10k"),
        )

        assert sent_messages == []
