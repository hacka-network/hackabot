import json

import pytest
from django.test import Client

from hackabot.apps.bot.models import (
    ActivityDay,
    Group,
    GroupPerson,
    Message,
    Person,
    Poll,
    PollAnswer,
)

TEST_WEBHOOK_SECRET = "test-webhook-secret-123"


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def set_webhook_secret(monkeypatch):
    monkeypatch.setattr(
        "hackabot.apps.bot.telegram.TELEGRAM_WEBHOOK_SECRET",
        TEST_WEBHOOK_SECRET,
    )


def post_webhook(client, data):
    return client.post(
        "/webhook/telegram/",
        data=json.dumps(data),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TEST_WEBHOOK_SECRET,
    )


class TestWebhookBasicMessage:
    def test_basic_text_message(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {
                        "id": 12345,
                        "first_name": "Alice",
                        "username": "alice123",
                        "is_bot": False,
                    },
                    "chat": {
                        "id": -1001234567890,
                        "type": "supergroup",
                        "title": "Test Group",
                    },
                    "date": 1704067200,
                    "text": "Hello everyone!",
                },
            },
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True}

        person = Person.objects.get(telegram_id=12345)
        assert person.first_name == "Alice"
        assert person.username == "alice123"
        assert person.is_bot is False

        group = Group.objects.get(telegram_id=-1001234567890)
        assert group.display_name == "Test Group"

        message = Message.objects.get(telegram_id=1, group=group)
        assert message.text == "Hello everyone!"
        assert message.person == person

        gp = GroupPerson.objects.get(group=group, person=person)
        assert gp.left is False
        assert gp.last_message_at is not None

        activity = ActivityDay.objects.get(person=person, group=group)
        assert activity.message_count == 1

    def test_message_deduplication(self, client, db):
        data = {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {"id": 12345, "first_name": "Alice"},
                "chat": {"id": -1001234567890, "type": "supergroup"},
                "date": 1704067200,
                "text": "First message",
            },
        }

        post_webhook(client, data)

        data["message"]["text"] = "Updated message"
        post_webhook(client, data)

        assert Message.objects.count() == 1
        message = Message.objects.first()
        assert message.text == "Updated message"

    def test_activity_not_incremented_on_duplicate(self, client, db):
        data = {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {"id": 12345, "first_name": "Alice"},
                "chat": {"id": -1001234567890, "type": "supergroup"},
                "date": 1704067200,
                "text": "First message",
            },
        }

        post_webhook(client, data)
        post_webhook(client, data)
        post_webhook(client, data)

        activity = ActivityDay.objects.first()
        assert activity.message_count == 1

    def test_second_message_increments_activity(self, client, db):
        for i in range(3):
            post_webhook(
                client,
                {
                    "update_id": 1000 + i,
                    "message": {
                        "message_id": i + 1,
                        "from": {"id": 12345, "first_name": "Alice"},
                        "chat": {"id": -1001234567890, "type": "supergroup"},
                        "date": 1704067200,
                        "text": f"Message {i + 1}",
                    },
                },
            )

        activity = ActivityDay.objects.first()
        assert activity.message_count == 3
        assert Message.objects.count() == 3

    def test_private_chat_ignored(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": 12345, "type": "private"},
                    "date": 1704067200,
                    "text": "Private message",
                },
            },
        )

        assert response.status_code == 200
        assert Message.objects.count() == 0
        assert Group.objects.count() == 0

    def test_message_without_text_ignored(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "photo": [
                        {"file_id": "abc123", "width": 640, "height": 480}
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert Message.objects.count() == 0

    def test_empty_text_ignored(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "",
                },
            },
        )

        assert response.status_code == 200
        assert Message.objects.count() == 0

    def test_message_without_sender(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "Message without sender",
                },
            },
        )

        assert response.status_code == 200
        message = Message.objects.first()
        assert message.person is None
        assert message.text == "Message without sender"

    def test_unicode_message(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice 🎉"},
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "Hello 世界! 🎉🚀💻 Привет мир! مرحبا",
                },
            },
        )

        assert response.status_code == 200
        message = Message.objects.first()
        assert "世界" in message.text
        assert "🎉" in message.text

    def test_long_message(self, client, db):
        long_text = "A" * 5000
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "text": long_text,
                },
            },
        )

        assert response.status_code == 200
        message = Message.objects.first()
        assert len(message.text) == 5000

    def test_user_profile_update(self, client, db):
        post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {
                        "id": 12345,
                        "first_name": "Alice",
                        "username": "alice123",
                    },
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "First message",
                },
            },
        )

        post_webhook(
            client,
            {
                "update_id": 1002,
                "message": {
                    "message_id": 2,
                    "from": {
                        "id": 12345,
                        "first_name": "Alice Updated",
                        "username": "alice_new",
                    },
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067300,
                    "text": "Second message",
                },
            },
        )

        person = Person.objects.get(telegram_id=12345)
        assert person.first_name == "Alice Updated"
        assert person.username == "alice_new"


class TestWebhookJoinLeave:
    def test_new_chat_members(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 11111, "first_name": "Admin"},
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "new_chat_members": [
                        {
                            "id": 12345,
                            "first_name": "Alice",
                            "username": "alice",
                        },
                        {"id": 67890, "first_name": "Bob", "username": "bob"},
                    ],
                },
            },
        )

        assert response.status_code == 200

        alice = Person.objects.get(telegram_id=12345)
        bob = Person.objects.get(telegram_id=67890)

        assert alice.first_name == "Alice"
        assert bob.first_name == "Bob"

        group = Group.objects.get(telegram_id=-1001234567890)
        alice_gp = GroupPerson.objects.get(group=group, person=alice)
        bob_gp = GroupPerson.objects.get(group=group, person=bob)

        assert alice_gp.left is False
        assert bob_gp.left is False

    def test_left_chat_member(self, client, db, group, person):
        GroupPerson.objects.create(group=group, person=person, left=False)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": group.telegram_id, "type": "supergroup"},
                    "date": 1704067200,
                    "left_chat_member": {
                        "id": person.telegram_id,
                        "first_name": person.first_name,
                    },
                },
            },
        )

        assert response.status_code == 200

        gp = GroupPerson.objects.get(group=group, person=person)
        assert gp.left is True

    def test_chat_member_update_join(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "chat_member": {
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "from": {"id": 11111, "first_name": "Admin"},
                    "date": 1704067200,
                    "new_chat_member": {
                        "user": {"id": 12345, "first_name": "Alice"},
                        "status": "member",
                    },
                },
            },
        )

        assert response.status_code == 200

        person = Person.objects.get(telegram_id=12345)
        group = Group.objects.get(telegram_id=-1001234567890)
        gp = GroupPerson.objects.get(group=group, person=person)

        assert gp.left is False

    def test_chat_member_update_kicked(self, client, db, group, person):
        GroupPerson.objects.create(group=group, person=person, left=False)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "chat_member": {
                    "chat": {"id": group.telegram_id, "type": "supergroup"},
                    "from": {"id": 11111, "first_name": "Admin"},
                    "date": 1704067200,
                    "new_chat_member": {
                        "user": {
                            "id": person.telegram_id,
                            "first_name": person.first_name,
                        },
                        "status": "kicked",
                    },
                },
            },
        )

        assert response.status_code == 200

        gp = GroupPerson.objects.get(group=group, person=person)
        assert gp.left is True

    def test_chat_member_update_left_status(self, client, db, group, person):
        GroupPerson.objects.create(group=group, person=person, left=False)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "chat_member": {
                    "chat": {"id": group.telegram_id, "type": "supergroup"},
                    "from": {"id": 11111, "first_name": "Admin"},
                    "date": 1704067200,
                    "new_chat_member": {
                        "user": {
                            "id": person.telegram_id,
                            "first_name": person.first_name,
                        },
                        "status": "left",
                    },
                },
            },
        )

        assert response.status_code == 200

        gp = GroupPerson.objects.get(group=group, person=person)
        assert gp.left is True

    def test_chat_member_administrator_not_left(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "chat_member": {
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "from": {"id": 11111, "first_name": "Admin"},
                    "date": 1704067200,
                    "new_chat_member": {
                        "user": {"id": 12345, "first_name": "NewAdmin"},
                        "status": "administrator",
                    },
                },
            },
        )

        assert response.status_code == 200

        person = Person.objects.get(telegram_id=12345)
        group = Group.objects.get(telegram_id=-1001234567890)
        gp = GroupPerson.objects.get(group=group, person=person)

        assert gp.left is False

    def test_my_chat_member_creates_group(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "my_chat_member": {
                    "chat": {
                        "id": -1009999888777,
                        "type": "supergroup",
                        "title": "New Bot Group",
                    },
                    "from": {"id": 11111, "first_name": "Admin"},
                    "date": 1704067200,
                    "new_chat_member": {
                        "user": {"id": 999999, "is_bot": True},
                        "status": "member",
                    },
                },
            },
        )

        assert response.status_code == 200

        group = Group.objects.get(telegram_id=-1009999888777)
        assert group.display_name == "New Bot Group"


class TestWebhookPolls:
    def test_poll_in_message(self, client, db, group, node):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 10,
                    "from": {"id": 999999, "is_bot": True},
                    "chat": {"id": group.telegram_id, "type": "supergroup"},
                    "date": 1704067200,
                    "poll": {
                        "id": "poll_abc123",
                        "question": "Who's coming this Thursday?",
                        "options": [
                            {"text": "Yes", "voter_count": 0},
                            {"text": "No", "voter_count": 0},
                        ],
                    },
                },
            },
        )

        assert response.status_code == 200

        poll = Poll.objects.get(telegram_id="poll_abc123")
        assert poll.question == "Who's coming this Thursday?"
        assert poll.node == node
        assert poll.yes_count == 0
        assert poll.no_count == 0

    def test_poll_state_update(self, client, db, poll):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "poll": {
                    "id": poll.telegram_id,
                    "question": poll.question,
                    "options": [
                        {"text": "Yes", "voter_count": 5},
                        {"text": "No", "voter_count": 3},
                    ],
                },
            },
        )

        assert response.status_code == 200

        poll.refresh_from_db()
        assert poll.yes_count == 5
        assert poll.no_count == 3

    def test_poll_answer_yes(self, client, db, poll, person):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "poll_answer": {
                    "poll_id": poll.telegram_id,
                    "user": {
                        "id": person.telegram_id,
                        "first_name": person.first_name,
                    },
                    "option_ids": [0],
                },
            },
        )

        assert response.status_code == 200

        answer = PollAnswer.objects.get(poll=poll, person=person)
        assert answer.yes is True

    def test_poll_answer_no(self, client, db, poll, person):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "poll_answer": {
                    "poll_id": poll.telegram_id,
                    "user": {
                        "id": person.telegram_id,
                        "first_name": person.first_name,
                    },
                    "option_ids": [1],
                },
            },
        )

        assert response.status_code == 200

        answer = PollAnswer.objects.get(poll=poll, person=person)
        assert answer.yes is False

    def test_poll_answer_change(self, client, db, poll, person):
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "poll_answer": {
                    "poll_id": poll.telegram_id,
                    "user": {
                        "id": person.telegram_id,
                        "first_name": person.first_name,
                    },
                    "option_ids": [1],
                },
            },
        )

        assert response.status_code == 200

        answer = PollAnswer.objects.get(poll=poll, person=person)
        assert answer.yes is False

    def test_poll_answer_retracted(self, client, db, poll, person):
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "poll_answer": {
                    "poll_id": poll.telegram_id,
                    "user": {
                        "id": person.telegram_id,
                        "first_name": person.first_name,
                    },
                    "option_ids": [],
                },
            },
        )

        assert response.status_code == 200

        assert not PollAnswer.objects.filter(poll=poll, person=person).exists()

    def test_poll_answer_nonexistent_poll(self, client, db, person):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "poll_answer": {
                    "poll_id": "nonexistent_poll",
                    "user": {
                        "id": person.telegram_id,
                        "first_name": person.first_name,
                    },
                    "option_ids": [0],
                },
            },
        )

        assert response.status_code == 200
        assert PollAnswer.objects.count() == 0

    def test_poll_in_group_without_node(self, client, db):
        Group.objects.create(
            telegram_id=-1009999888777,
            display_name="Group Without Node",
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 10,
                    "from": {"id": 999999, "is_bot": True},
                    "chat": {"id": -1009999888777, "type": "supergroup"},
                    "date": 1704067200,
                    "poll": {
                        "id": "poll_no_node",
                        "question": "Poll without node?",
                        "options": [
                            {"text": "Yes", "voter_count": 0},
                            {"text": "No", "voter_count": 0},
                        ],
                    },
                },
            },
        )

        assert response.status_code == 200

        poll = Poll.objects.get(telegram_id="poll_no_node")
        assert poll.node is None


class TestWebhookEdgeCases:
    def test_missing_secret_rejected(self, client, db):
        response = client.post(
            "/webhook/telegram/",
            data=json.dumps({"update_id": 1001}),
            content_type="application/json",
        )

        assert response.status_code == 403

    def test_wrong_secret_rejected(self, client, db):
        response = client.post(
            "/webhook/telegram/",
            data=json.dumps({"update_id": 1001}),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong-secret",
        )

        assert response.status_code == 403

    def test_invalid_json(self, client, db):
        response = client.post(
            "/webhook/telegram/",
            data="not valid json",
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TEST_WEBHOOK_SECRET,
        )

        assert response.status_code == 400

    def test_empty_update(self, client, db):
        response = post_webhook(client, {"update_id": 1001})

        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_channel_post_ignored(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "channel_post": {
                    "message_id": 1,
                    "chat": {"id": -1001111111111, "type": "channel"},
                    "date": 1704067200,
                    "text": "Channel post",
                },
            },
        )

        assert response.status_code == 200
        assert Message.objects.count() == 0

    def test_edited_message_ignored(self, client, db):
        post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "Original message",
                },
            },
        )

        response = post_webhook(
            client,
            {
                "update_id": 1002,
                "edited_message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "edit_date": 1704067300,
                    "text": "Edited message",
                },
            },
        )

        assert response.status_code == 200

        message = Message.objects.get(telegram_id=1)
        assert message.text == "Original message"

    def test_callback_query_handled(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "callback_query": {
                    "id": "callback_123",
                    "from": {"id": 12345, "first_name": "Alice"},
                    "message": {
                        "message_id": 1,
                        "chat": {"id": -1001234567890},
                    },
                    "data": "button_action",
                },
            },
        )

        assert response.status_code == 200

    def test_group_type_handled(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {
                        "id": -100999,
                        "type": "group",
                        "title": "Regular Group",
                    },
                    "date": 1704067200,
                    "text": "Hello!",
                },
            },
        )

        assert response.status_code == 200

        group = Group.objects.get(telegram_id=-100999)
        assert group.display_name == "Regular Group"

    def test_bot_user_handled(self, client, db):
        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {
                        "id": 999999,
                        "first_name": "BotUser",
                        "is_bot": True,
                    },
                    "chat": {"id": -1001234567890, "type": "supergroup"},
                    "date": 1704067200,
                    "new_chat_members": [
                        {
                            "id": 888888,
                            "first_name": "AnotherBot",
                            "is_bot": True,
                        },
                    ],
                },
            },
        )

        assert response.status_code == 200

        bot = Person.objects.get(telegram_id=888888)
        assert bot.is_bot is True
