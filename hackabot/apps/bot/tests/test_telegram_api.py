import os
from datetime import time
from unittest.mock import patch

import pytest
import responses

from hackabot.apps.bot.models import Event, Poll
from hackabot.apps.bot.telegram import (
    TELEGRAM_API_BASE,
    _get_bot_token,
    send,
    send_event_reminder,
    send_poll,
    verify_webhook,
)


class TestGetBotToken:
    def test_returns_token_with_bot_prefix(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:ABC"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "123456:ABC"

            token = _get_bot_token()
            assert token == "bot123456:ABC"

    def test_token_already_has_bot_prefix(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "bot123456:ABC"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "bot123456:ABC"

            token = _get_bot_token()
            assert token == "bot123456:ABC"

    def test_missing_token_raises_error(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = ""

            with pytest.raises(RuntimeError) as exc_info:
                _get_bot_token()
            assert "TELEGRAM_BOT_TOKEN not set" in str(exc_info.value)


class TestVerifyWebhook:
    @responses.activate
    def test_webhook_already_set(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "testtoken",
                "TELEGRAM_WEBHOOK_URL": "https://example.com/webhook/",
            },
        ):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"
            telegram.TELEGRAM_WEBHOOK_URL = "https://example.com/webhook/"

            responses.add(
                responses.GET,
                f"{TELEGRAM_API_BASE}/bottesttoken/getWebhookInfo",
                json={
                    "ok": True,
                    "result": {"url": "https://example.com/webhook/"},
                },
                status=200,
            )

            result = verify_webhook()

            assert result is True
            assert len(responses.calls) == 1

    @responses.activate
    def test_webhook_needs_setting(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "testtoken",
                "TELEGRAM_WEBHOOK_URL": "https://example.com/webhook/",
            },
        ):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"
            telegram.TELEGRAM_WEBHOOK_URL = "https://example.com/webhook/"

            responses.add(
                responses.GET,
                f"{TELEGRAM_API_BASE}/bottesttoken/getWebhookInfo",
                json={"ok": True, "result": {"url": ""}},
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/setWebhook",
                json={"ok": True, "result": True},
                status=200,
            )

            result = verify_webhook()

            assert result is True
            assert len(responses.calls) == 2

            set_webhook_call = responses.calls[1]
            body = set_webhook_call.request.body
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            assert "url" in body
            assert "allowed_updates" in body

    @responses.activate
    def test_webhook_set_failed(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "testtoken",
                "TELEGRAM_WEBHOOK_URL": "https://example.com/webhook/",
            },
        ):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"
            telegram.TELEGRAM_WEBHOOK_URL = "https://example.com/webhook/"

            responses.add(
                responses.GET,
                f"{TELEGRAM_API_BASE}/bottesttoken/getWebhookInfo",
                json={"ok": True, "result": {"url": ""}},
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/setWebhook",
                json={"ok": False, "description": "Bad Request"},
                status=200,
            )

            result = verify_webhook()

            assert result is False

    def test_missing_webhook_url(self):
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_URL": ""}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_WEBHOOK_URL = ""

            result = verify_webhook()

            assert result is False


class TestSend:
    @responses.activate
    def test_send_message(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1001,
                        "chat": {"id": 12345},
                        "text": "Hello!",
                    },
                },
                status=200,
            )

            send(12345, "Hello!")

            assert len(responses.calls) == 1
            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert body["chat_id"] == 12345
            assert body["text"] == "Hello!"
            assert body["parse_mode"] == "Markdown"
            assert body["disable_web_page_preview"] is True

    @responses.activate
    def test_send_message_with_markdown(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            send(12345, "*Bold* and _italic_")

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert body["text"] == "*Bold* and _italic_"

    @responses.activate
    def test_send_message_with_emojis(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            send(12345, "Hello 🎉🚀")

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "🎉" in body["text"]

    @responses.activate
    def test_send_message_api_error(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": False, "description": "Bad Request"},
                status=400,
            )

            import requests

            with pytest.raises(requests.HTTPError):
                send(12345, "Hello!")


class TestSendPoll:
    @responses.activate
    def test_send_poll_creates_db_entry(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1002,
                        "poll": {
                            "id": "poll_new_123",
                            "question": f"Who's coming to {node.emoji} {node.name} this Thursday?",
                            "options": [
                                {"text": "Yes", "voter_count": 0},
                                {"text": "No", "voter_count": 0},
                            ],
                        },
                    },
                },
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1003}},
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/pinChatMessage",
                json={"ok": True, "result": True},
                status=200,
            )

            send_poll(node)

            poll = Poll.objects.get(telegram_id="poll_new_123")
            assert poll.node == node
            assert "Thursday" in poll.question

    @responses.activate
    def test_send_poll_custom_day(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1002,
                        "poll": {
                            "id": "poll_friday_123",
                            "question": f"Who's coming to {node.name} this Friday?",
                            "options": [],
                        },
                    },
                },
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1003}},
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/pinChatMessage",
                json={"ok": True, "result": True},
                status=200,
            )

            send_poll(node, when="Friday")

            poll_call = responses.calls[0]
            import json

            body = json.loads(poll_call.request.body)
            assert "Friday" in body["question"]

    @responses.activate
    def test_send_poll_sends_invite(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1002,
                        "poll": {"id": "poll_123", "question": "Test?"},
                    },
                },
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1003}},
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/pinChatMessage",
                json={"ok": True, "result": True},
                status=200,
            )

            send_poll(node)

            message_call = responses.calls[1]
            import json

            body = json.loads(message_call.request.body)
            assert "global chat" in body["text"]
            assert "t.me" in body["text"]

    @responses.activate
    def test_send_poll_pins_message(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1002,
                        "poll": {"id": "poll_123", "question": "Test?"},
                    },
                },
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1003}},
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/pinChatMessage",
                json={"ok": True, "result": True},
                status=200,
            )

            send_poll(node)

            pin_call = responses.calls[2]
            import json

            body = json.loads(pin_call.request.body)
            assert body["message_id"] == 1002
            assert body["chat_id"] == node.group.telegram_id

    @responses.activate
    def test_send_poll_pin_failure_handled(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1002,
                        "poll": {"id": "poll_123", "question": "Test?"},
                    },
                },
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1003}},
                status=200,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/pinChatMessage",
                json={"ok": False, "description": "Not enough rights"},
                status=200,
            )

            send_poll(node)

            assert Poll.objects.filter(telegram_id="poll_123").exists()


class TestSendEventReminder:
    @responses.activate
    def test_intros_reminder(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            event = Event(node=node, type="intros", time=time(9, 30), where="")
            send_event_reminder(event)

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "Intros" in body["text"]
            assert "9:30am" in body["text"]
            assert "🔔👋" in body["text"]

    @responses.activate
    def test_demos_reminder(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            event = Event(node=node, type="demos", time=time(16, 0), where="")
            send_event_reminder(event)

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "Demos" in body["text"]
            assert "4pm" in body["text"]
            assert "🔔💻" in body["text"]

    @responses.activate
    def test_lunch_reminder_with_location(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            event = Event(
                node=node, type="lunch", time=time(12, 0), where="Cafeteria"
            )
            send_event_reminder(event)

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "Lunch" in body["text"]
            assert "12pm" in body["text"]
            assert "Cafeteria" in body["text"]
            assert "🔔🍔" in body["text"]

    @responses.activate
    def test_lunch_reminder_without_location(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            event = Event(node=node, type="lunch", time=time(12, 0), where="")
            send_event_reminder(event)

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "Lunch" in body["text"]
            assert "12pm" in body["text"]
            assert "🔔🍔" in body["text"]

    @responses.activate
    def test_drinks_reminder_with_location(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            event = Event(
                node=node, type="drinks", time=time(18, 0), where="Rooftop Bar"
            )
            send_event_reminder(event)

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "Rooftop Bar" in body["text"]
            assert "let's go" in body["text"]
            assert "🍺🍻🍷" in body["text"]

    @responses.activate
    def test_drinks_reminder_without_location(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            event = Event(node=node, type="drinks", time=time(18, 0), where="")
            send_event_reminder(event)

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "Drinks time" in body["text"]
            assert "let's go" in body["text"]
            assert "🍺🍻🍷" in body["text"]

    @responses.activate
    def test_time_formatting_removes_minutes_when_zero(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            event = Event(node=node, type="intros", time=time(10, 0), where="")
            send_event_reminder(event)

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "10am" in body["text"]
            assert ":00" not in body["text"]

    @responses.activate
    def test_time_formatting_keeps_minutes_when_nonzero(self, db, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            event = Event(node=node, type="intros", time=time(9, 45), where="")
            send_event_reminder(event)

            call = responses.calls[0]
            import json

            body = json.loads(call.request.body)
            assert "9:45am" in body["text"]
