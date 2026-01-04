import pytest
from datetime import time

from hackabot.apps.bot.models import (
    Group,
    Node,
    Event,
    Person,
    Poll,
)


@pytest.fixture
def group(db):
    return Group.objects.create(
        telegram_id=-1001234567890,
        display_name="Test Hackathon Group",
    )


@pytest.fixture
def node(db, group):
    return Node.objects.create(
        group=group,
        name="Test Node",
        emoji="🚀",
        location="Test City",
        timezone="America/New_York",
        established=2023,
    )


@pytest.fixture
def events(db, node):
    return [
        Event.objects.create(
            node=node,
            type="intros",
            time=time(9, 30),
            where="Main Hall",
        ),
        Event.objects.create(
            node=node,
            type="lunch",
            time=time(12, 0),
            where="Cafeteria",
        ),
        Event.objects.create(
            node=node,
            type="demos",
            time=time(16, 0),
            where="Demo Stage",
        ),
        Event.objects.create(
            node=node,
            type="drinks",
            time=time(18, 0),
            where="Rooftop Bar",
        ),
    ]


@pytest.fixture
def person(db):
    return Person.objects.create(
        telegram_id=12345,
        first_name="Alice",
        username="alice123",
        is_bot=False,
    )


@pytest.fixture
def poll(db, node):
    return Poll.objects.create(
        telegram_id="poll_123456",
        node=node,
        question="Are you coming this Thursday?",
        yes_count=0,
        no_count=0,
    )


@pytest.fixture
def mock_telegram_api(responses):
    def _setup(token="testtoken123"):
        base_url = "https://api.telegram.org"
        bot_token = f"bot{token}" if not token.startswith("bot") else token

        responses.add(
            responses.GET,
            f"{base_url}/{bot_token}/getWebhookInfo",
            json={
                "ok": True,
                "result": {
                    "url": "",
                    "has_custom_certificate": False,
                    "pending_update_count": 0,
                },
            },
            status=200,
        )

        responses.add(
            responses.POST,
            f"{base_url}/{bot_token}/setWebhook",
            json={
                "ok": True,
                "result": True,
                "description": "Webhook was set",
            },
            status=200,
        )

        responses.add(
            responses.POST,
            f"{base_url}/{bot_token}/sendMessage",
            json={
                "ok": True,
                "result": {
                    "message_id": 1001,
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "TestBot",
                    },
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "Test message",
                },
            },
            status=200,
        )

        responses.add(
            responses.POST,
            f"{base_url}/{bot_token}/sendPoll",
            json={
                "ok": True,
                "result": {
                    "message_id": 1002,
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "TestBot",
                    },
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "poll": {
                        "id": "poll_test_123",
                        "question": "Test poll question",
                        "options": [
                            {"text": "Yes", "voter_count": 0},
                            {"text": "No", "voter_count": 0},
                        ],
                        "total_voter_count": 0,
                        "is_closed": False,
                        "is_anonymous": False,
                        "type": "regular",
                        "allows_multiple_answers": False,
                    },
                },
            },
            status=200,
        )

        responses.add(
            responses.POST,
            f"{base_url}/{bot_token}/pinChatMessage",
            json={"ok": True, "result": True},
            status=200,
        )

        return responses

    return _setup
