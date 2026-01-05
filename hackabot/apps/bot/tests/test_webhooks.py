import json

import pytest
from django.test import Client

from hackabot.apps.bot.models import (
    ActivityDay,
    Group,
    GroupPerson,
    Message,
    Node,
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

    def test_private_chat_does_not_store_message(
        self, client, db, monkeypatch
    ):
        monkeypatch.setattr("hackabot.apps.bot.views.send", lambda *args: None)
        Group.objects.all().delete()

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
    def test_new_chat_members(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )

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

    def test_chat_member_update_join(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )

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

    def test_chat_member_administrator_not_left(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )

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

    def test_callback_query_handled(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.answer_callback_query",
            lambda qid, text=None: None,
        )

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


class TestWebhookDMs:
    def _make_dm(
        self, text, user_id=12345, first_name="Alice", username="alice"
    ):
        return {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {
                    "id": user_id,
                    "first_name": first_name,
                    "username": username,
                },
                "chat": {"id": user_id, "type": "private"},
                "date": 1704067200,
                "text": text,
            },
        }

    def _setup_member(self, telegram_id=12345, first_name="Alice", **kwargs):
        from hackabot.apps.bot.models import Group, GroupPerson, Node, Person

        person = Person.objects.create(
            telegram_id=telegram_id, first_name=first_name, **kwargs
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="Test Node")
        GroupPerson.objects.create(group=group, person=person, left=False)
        return person

    def test_dm_non_member_gets_join_prompt(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        assert len(sent_messages) == 1
        text = sent_messages[0][1]
        assert "member of at least one Hacka* node" in text
        assert "hacka.network" in text

    def test_dm_unrecognized_command(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("hello"))

        assert response.status_code == 200
        assert len(sent_messages) == 1
        assert sent_messages[0][0] == 12345
        assert "/help" in sent_messages[0][1]

    def test_dm_help_command(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        assert len(sent_messages) == 1
        assert sent_messages[0][0] == 12345
        text = sent_messages[0][1]
        assert "Welcome to Hackabot" in text
        assert "hacka.network" in text
        assert "Privacy" in text
        assert "/x" in text
        assert "/privacy" in text

    def test_dm_start_command_shows_help(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/start"))

        assert response.status_code == 200
        assert len(sent_messages) == 1
        assert "Welcome to Hackabot" in sent_messages[0][1]

    def test_dm_help_shows_nodes_for_member(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        from hackabot.apps.bot.models import Group, GroupPerson, Node, Person

        person = Person.objects.create(
            telegram_id=12345, first_name="Alice", username="alice"
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="London", emoji="🇬🇧")
        GroupPerson.objects.create(group=group, person=person, left=False)

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        assert "🇬🇧 London" in sent_messages[0][1]

    def test_dm_help_shows_privacy_status(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "Privacy mode" in text
        assert "ON" in text

    def test_dm_x_command_sets_username(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/x @james"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.username_x == "james"
        assert "@james" in sent_messages[0][1]

    def test_dm_x_command_strips_at_sign(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )
        self._setup_member()

        post_webhook(client, self._make_dm("/x @johndoe"))

        person = Person.objects.get(telegram_id=12345)
        assert person.username_x == "johndoe"

    def test_dm_x_command_without_at_sign(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )
        self._setup_member()

        post_webhook(client, self._make_dm("/x johndoe"))

        person = Person.objects.get(telegram_id=12345)
        assert person.username_x == "johndoe"

    def test_dm_x_command_no_username_provided(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/x"))

        assert response.status_code == 200
        assert "Please provide" in sent_messages[0][1]

    def test_dm_x_command_empty_username(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        post_webhook(client, self._make_dm("/x @"))

        assert "Please provide a valid" in sent_messages[0][1]

    def test_dm_x_command_privacy_nudge_when_on(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(privacy=True)

        post_webhook(client, self._make_dm("/x @alice"))

        text = sent_messages[0][1]
        assert "privacy mode is ON" in text
        assert "/privacy off" in text

    def test_dm_x_command_no_privacy_nudge_when_off(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(privacy=False)

        post_webhook(client, self._make_dm("/x @alice"))

        text = sent_messages[0][1]
        assert "privacy mode" not in text.lower()

    def test_dm_x_command_rejects_html(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(
            client, self._make_dm("/x <script>alert('xss')</script>")
        )

        assert response.status_code == 200
        from hackabot.apps.bot.models import Person

        person = Person.objects.get(telegram_id=12345)
        assert person.username_x == ""
        assert "cannot contain HTML" in sent_messages[0][1]

    def test_dm_x_command_rejects_greater_than(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/x foo>bar"))

        assert response.status_code == 200
        from hackabot.apps.bot.models import Person

        person = Person.objects.get(telegram_id=12345)
        assert person.username_x == ""
        assert "cannot contain HTML" in sent_messages[0][1]

    def test_dm_x_command_rejects_invalid_characters(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/x alice!@#$%"))

        assert response.status_code == 200
        from hackabot.apps.bot.models import Person

        person = Person.objects.get(telegram_id=12345)
        assert person.username_x == ""
        assert "valid username" in sent_messages[0][1]

    def test_dm_privacy_on(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(privacy=False)

        response = post_webhook(client, self._make_dm("/privacy on"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.privacy is True
        assert "ON" in sent_messages[0][1]

    def test_dm_privacy_off(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(privacy=True)

        response = post_webhook(client, self._make_dm("/privacy off"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.privacy is False
        assert "OFF" in sent_messages[0][1]

    def test_dm_privacy_without_value_shows_status(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(privacy=True)

        response = post_webhook(client, self._make_dm("/privacy"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "currently" in text
        assert "ON" in text

    def test_dm_privacy_invalid_value_shows_status(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(privacy=False)

        response = post_webhook(client, self._make_dm("/privacy maybe"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "currently" in text
        assert "OFF" in text

    def test_dm_creates_person_if_not_exists(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )

        assert Person.objects.count() == 0

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        assert Person.objects.count() == 1
        person = Person.objects.first()
        assert person.telegram_id == 12345
        assert person.first_name == "Alice"

    def test_dm_does_not_store_message(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        assert Message.objects.count() == 0

    def test_dm_without_user_data_ignored(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 12345, "type": "private"},
                    "date": 1704067200,
                    "text": "/help",
                },
            },
        )

        assert response.status_code == 200
        assert len(sent_messages) == 0

    def test_dm_help_shows_x_username(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(username="alice", username_x="alice_x")

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        assert "@alice_x" in sent_messages[0][1]

    def test_dm_left_member_gets_join_prompt(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        from hackabot.apps.bot.models import Group, GroupPerson, Node, Person

        person = Person.objects.create(
            telegram_id=12345, first_name="Alice", username="alice"
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="London", emoji="🇬🇧")
        GroupPerson.objects.create(group=group, person=person, left=True)

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "London" not in text
        assert "member of at least one Hacka* node" in text
        assert "hacka.network" in text


class TestOnboarding:
    def test_new_member_gets_welcome_message(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        Node.objects.create(name="Test Node", group=group)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 11111, "first_name": "Admin"},
                    "chat": {
                        "id": -1001234567890,
                        "type": "supergroup",
                        "title": "Test Group",
                    },
                    "date": 1704067200,
                    "new_chat_members": [
                        {
                            "id": 12345,
                            "first_name": "Alice",
                            "username": "alice",
                        },
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert len(sent_messages) == 1
        chat_id, text = sent_messages[0]
        assert chat_id == -1001234567890
        assert "Welcome" in text
        assert "Alice" in text
        assert "Introduce yourself" in text
        assert "DM me to set up your profile" in text

        person = Person.objects.get(telegram_id=12345)
        assert person.onboarded is True

    def test_already_onboarded_member_gets_welcome(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        Person.objects.create(
            telegram_id=12345,
            first_name="Alice",
            username="alice",
            onboarded=True,
        )

        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        Node.objects.create(name="Test Node", group=group)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 11111, "first_name": "Admin"},
                    "chat": {
                        "id": -1001234567890,
                        "type": "supergroup",
                        "title": "Test Group",
                    },
                    "date": 1704067200,
                    "new_chat_members": [
                        {
                            "id": 12345,
                            "first_name": "Alice",
                            "username": "alice",
                        },
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert len(sent_messages) == 1
        assert "Welcome" in sent_messages[0][1]

    def test_bot_member_not_onboarded(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 11111, "first_name": "Admin"},
                    "chat": {
                        "id": -1001234567890,
                        "type": "supergroup",
                        "title": "Test Group",
                    },
                    "date": 1704067200,
                    "new_chat_members": [
                        {
                            "id": 888888,
                            "first_name": "ABot",
                            "is_bot": True,
                        },
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert len(sent_messages) == 0

        bot = Person.objects.get(telegram_id=888888)
        assert bot.onboarded is False

    def test_multiple_new_members_each_get_welcome(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        Node.objects.create(name="Test Node", group=group)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 11111, "first_name": "Admin"},
                    "chat": {
                        "id": -1001234567890,
                        "type": "supergroup",
                        "title": "Test Group",
                    },
                    "date": 1704067200,
                    "new_chat_members": [
                        {"id": 12345, "first_name": "Alice"},
                        {"id": 67890, "first_name": "Bob"},
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert len(sent_messages) == 2

        assert "Alice" in sent_messages[0][1]
        assert "Bob" in sent_messages[1][1]

        alice = Person.objects.get(telegram_id=12345)
        bob = Person.objects.get(telegram_id=67890)
        assert alice.onboarded is True
        assert bob.onboarded is True

    def test_chat_member_update_join_triggers_onboarding(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        Node.objects.create(name="Test Node", group=group)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "chat_member": {
                    "chat": {
                        "id": -1001234567890,
                        "type": "supergroup",
                        "title": "Test Group",
                    },
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
        assert len(sent_messages) == 1
        assert "Welcome" in sent_messages[0][1]
        assert "Alice" in sent_messages[0][1]

        person = Person.objects.get(telegram_id=12345)
        assert person.onboarded is True

    def test_chat_member_update_left_no_onboarding(
        self, client, db, group, person, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

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
        assert len(sent_messages) == 0

    def test_member_joining_second_group_gets_welcome(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        Person.objects.create(
            telegram_id=12345,
            first_name="Alice",
            onboarded=True,
        )

        group = Group.objects.create(
            telegram_id=-1009999888777,
            display_name="Second Group",
        )
        Node.objects.create(name="Second Node", group=group)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 11111, "first_name": "Admin"},
                    "chat": {
                        "id": -1009999888777,
                        "type": "supergroup",
                        "title": "Second Group",
                    },
                    "date": 1704067200,
                    "new_chat_members": [
                        {"id": 12345, "first_name": "Alice"},
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert len(sent_messages) == 1
        assert "Welcome" in sent_messages[0][1]

    def test_member_without_first_name_gets_generic_welcome(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        Node.objects.create(name="Test Node", group=group)

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 11111, "first_name": "Admin"},
                    "chat": {
                        "id": -1001234567890,
                        "type": "supergroup",
                        "title": "Test Group",
                    },
                    "date": 1704067200,
                    "new_chat_members": [
                        {"id": 12345},
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert len(sent_messages) == 1
        assert "Welcome" in sent_messages[0][1]
        assert "there" in sent_messages[0][1]

    def test_new_member_no_welcome_when_no_node(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group Without Node",
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 11111, "first_name": "Admin"},
                    "chat": {
                        "id": -1001234567890,
                        "type": "supergroup",
                        "title": "Test Group Without Node",
                    },
                    "date": 1704067200,
                    "new_chat_members": [
                        {
                            "id": 12345,
                            "first_name": "Alice",
                            "username": "alice",
                        },
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert len(sent_messages) == 0

        person = Person.objects.get(telegram_id=12345)
        assert person.onboarded is False


class TestBioCommand:
    def _make_dm(
        self, text, user_id=12345, first_name="Alice", username="alice"
    ):
        return {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {
                    "id": user_id,
                    "first_name": first_name,
                    "username": username,
                },
                "chat": {"id": user_id, "type": "private"},
                "date": 1704067200,
                "text": text,
            },
        }

    def _setup_member(self, telegram_id=12345, first_name="Alice", **kwargs):
        from hackabot.apps.bot.models import Group, GroupPerson, Node, Person

        person = Person.objects.create(
            telegram_id=telegram_id, first_name=first_name, **kwargs
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="Test Node")
        GroupPerson.objects.create(group=group, person=person, left=False)
        return person

    def test_bio_command_sets_bio(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(
            client, self._make_dm("/bio I build cool stuff")
        )

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == "I build cool stuff"
        assert "I build cool stuff" in sent_messages[0][1]

    def test_bio_command_clears_bio_with_unset(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(bio="Old bio")

        response = post_webhook(client, self._make_dm("/bio unset"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == ""
        assert "cleared" in sent_messages[0][1]

    def test_bio_command_shows_current_when_no_args(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(bio="My current bio")

        response = post_webhook(client, self._make_dm("/bio"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == "My current bio"
        text = sent_messages[0][1]
        assert "My current bio" in text
        assert "/bio unset" in text

    def test_bio_command_too_long(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        long_bio = "A" * 501
        response = post_webhook(client, self._make_dm(f"/bio {long_bio}"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == ""
        assert "too long" in sent_messages[0][1]
        assert "501" in sent_messages[0][1]
        assert "500" in sent_messages[0][1]

    def test_bio_command_max_length_accepted(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )
        self._setup_member()

        bio_500 = "B" * 500
        response = post_webhook(client, self._make_dm(f"/bio {bio_500}"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert len(person.bio) == 500

    def test_bio_command_rejects_slash_commands(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(
            client, self._make_dm("/bio Check out /mybot for more")
        )

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == ""
        assert "cannot contain Telegram commands" in sent_messages[0][1]

    def test_bio_command_rejects_html(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/bio I am <b>bold</b>"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == ""
        assert "cannot contain HTML" in sent_messages[0][1]

    def test_bio_command_rejects_greater_than(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/bio 5 > 3"))

        assert response.status_code == 200
        assert "cannot contain HTML" in sent_messages[0][1]

    def test_bio_command_unescapes_html_entities(
        self, client, db, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/bio Rock &amp; Roll"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == "Rock & Roll"

    def test_bio_shown_in_help(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(username="alice", bio="Building the future")

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        assert "Building the future" in sent_messages[0][1]

    def test_bio_not_shown_when_empty(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(username="alice", bio="")

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        assert "Bio:" not in sent_messages[0][1]

    def test_help_shows_bio_commands(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "/bio your text" in text
        assert "/bio unset" in text

    def test_start_with_args_shows_help(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/start something"))

        assert response.status_code == 200
        assert "Welcome to Hackabot" in sent_messages[0][1]

    def test_bio_with_unicode_characters(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )
        self._setup_member()

        response = post_webhook(
            client, self._make_dm("/bio Building 🚀 rockets and ✨ dreams")
        )

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == "Building 🚀 rockets and ✨ dreams"

    def test_bio_overwrites_existing(self, client, db, monkeypatch):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send", lambda chat_id, text: None
        )
        self._setup_member(bio="Old bio")

        response = post_webhook(client, self._make_dm("/bio New bio"))

        assert response.status_code == 200
        person = Person.objects.get(telegram_id=12345)
        assert person.bio == "New bio"

    def test_bio_command_privacy_nudge_when_on(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(privacy=True)

        post_webhook(client, self._make_dm("/bio Building cool stuff"))

        text = sent_messages[0][1]
        assert "privacy mode is ON" in text
        assert "/privacy off" in text

    def test_bio_command_no_privacy_nudge_when_off(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member(privacy=False)

        post_webhook(client, self._make_dm("/bio Building cool stuff"))

        text = sent_messages[0][1]
        assert "privacy mode" not in text.lower()


class TestPeopleCommand:
    def _make_dm(
        self, text, user_id=12345, first_name="Alice", username="alice"
    ):
        return {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {
                    "id": user_id,
                    "first_name": first_name,
                    "username": username,
                },
                "chat": {"id": user_id, "type": "private"},
                "date": 1704067200,
                "text": text,
            },
        }

    def _setup_member(self, telegram_id=12345, first_name="Alice", **kwargs):
        from hackabot.apps.bot.models import Group, GroupPerson, Node, Person

        person = Person.objects.create(
            telegram_id=telegram_id, first_name=first_name, **kwargs
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="Test Node")
        GroupPerson.objects.create(group=group, person=person, left=False)
        return person

    def test_people_command_non_member_gets_join_prompt(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        response = post_webhook(client, self._make_dm("/people"))

        assert response.status_code == 200
        assert len(sent_messages) == 1
        assert "member of at least one Hacka* node" in sent_messages[0][1]

    def test_people_command_shows_public_people(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        alice = Person.objects.create(
            telegram_id=12345, first_name="Alice", username="alice"
        )
        bob = Person.objects.create(
            telegram_id=67890,
            first_name="Bob",
            username="bob",
            privacy=False,
            username_x="bobx",
            bio="Building cool stuff",
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="London", emoji="🇬🇧")
        GroupPerson.objects.create(group=group, person=alice, left=False)
        GroupPerson.objects.create(group=group, person=bob, left=False)

        response = post_webhook(client, self._make_dm("/people"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "London" in text
        assert "Bob" in text
        assert "@bobx" in text
        assert "Building cool stuff" in text

    def test_people_command_excludes_private_people(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        alice = Person.objects.create(
            telegram_id=12345, first_name="Alice", username="alice"
        )
        bob = Person.objects.create(
            telegram_id=67890,
            first_name="Bob",
            username="bob",
            privacy=True,
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="London", emoji="🇬🇧")
        GroupPerson.objects.create(group=group, person=alice, left=False)
        GroupPerson.objects.create(group=group, person=bob, left=False)

        response = post_webhook(client, self._make_dm("/people"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "Bob" not in text
        assert "No public profiles" in text

    def test_people_command_includes_self(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        alice = Person.objects.create(
            telegram_id=12345,
            first_name="Alice",
            username="alice",
            privacy=False,
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="London", emoji="🇬🇧")
        GroupPerson.objects.create(group=group, person=alice, left=False)

        response = post_webhook(client, self._make_dm("/people"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "Alice" in text

    def test_people_command_excludes_left_members(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        alice = Person.objects.create(
            telegram_id=12345, first_name="Alice", username="alice"
        )
        bob = Person.objects.create(
            telegram_id=67890,
            first_name="Bob",
            username="bob",
            privacy=False,
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="London", emoji="🇬🇧")
        GroupPerson.objects.create(group=group, person=alice, left=False)
        GroupPerson.objects.create(group=group, person=bob, left=True)

        response = post_webhook(client, self._make_dm("/people"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "Bob" not in text

    def test_people_command_multiple_nodes(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        alice = Person.objects.create(
            telegram_id=12345, first_name="Alice", username="alice"
        )
        bob = Person.objects.create(
            telegram_id=67890,
            first_name="Bob",
            privacy=False,
        )
        carol = Person.objects.create(
            telegram_id=11111,
            first_name="Carol",
            privacy=False,
        )

        group1 = Group.objects.create(
            telegram_id=-100123, display_name="Test Group 1"
        )
        group2 = Group.objects.create(
            telegram_id=-100456, display_name="Test Group 2"
        )
        Node.objects.create(group=group1, name="London", emoji="🇬🇧")
        Node.objects.create(group=group2, name="Paris", emoji="🇫🇷")

        GroupPerson.objects.create(group=group1, person=alice, left=False)
        GroupPerson.objects.create(group=group1, person=bob, left=False)
        GroupPerson.objects.create(group=group2, person=alice, left=False)
        GroupPerson.objects.create(group=group2, person=carol, left=False)

        response = post_webhook(client, self._make_dm("/people"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "London" in text
        assert "Paris" in text
        assert "Bob" in text
        assert "Carol" in text

    def test_people_command_shows_footer(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )

        alice = Person.objects.create(
            telegram_id=12345, first_name="Alice", username="alice"
        )
        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(group=group, name="London", emoji="🇬🇧")
        GroupPerson.objects.create(group=group, person=alice, left=False)

        response = post_webhook(client, self._make_dm("/people"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "privacy mode OFF" in text

    def test_help_shows_people_command(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "/people" in text


class TestNodesCommand:
    def _make_dm(
        self, text, user_id=12345, first_name="Alice", username="alice"
    ):
        return {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {
                    "id": user_id,
                    "first_name": first_name,
                    "username": username,
                },
                "chat": {"id": user_id, "type": "private"},
                "date": 1704067200,
                "text": text,
            },
        }

    def _make_callback_query(
        self, callback_data, user_id=12345, first_name="Alice"
    ):
        return {
            "update_id": 1002,
            "callback_query": {
                "id": "callback123",
                "from": {"id": user_id, "first_name": first_name},
                "message": {
                    "message_id": 1,
                    "chat": {"id": user_id, "type": "private"},
                },
                "data": callback_data,
            },
        }

    def _setup_member(self, telegram_id=12345, first_name="Alice", **kwargs):
        from hackabot.apps.bot.models import Group, GroupPerson, Node, Person

        Node.objects.all().delete()
        Group.objects.all().delete()
        person = Person.objects.create(
            telegram_id=telegram_id, first_name=first_name, **kwargs
        )
        group = Group.objects.create(
            telegram_id=-100999, display_name="Home Group"
        )
        Node.objects.create(group=group, name="Home Node")
        GroupPerson.objects.create(group=group, person=person, left=False)
        return person

    def test_nodes_command_shows_available_nodes(
        self, client, db, monkeypatch
    ):
        keyboard_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send_with_keyboard",
            lambda chat_id, text, keyboard: keyboard_messages.append(
                (chat_id, text, keyboard)
            ),
        )
        self._setup_member()

        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(
            group=group,
            name="London",
            emoji="🇬🇧",
            location="UK",
        )

        response = post_webhook(client, self._make_dm("/nodes"))

        assert response.status_code == 200
        assert len(keyboard_messages) == 1
        chat_id, text, keyboard = keyboard_messages[0]
        assert chat_id == 12345
        assert "Tap a node" in text
        assert len(keyboard) == 2
        button_texts = [row[0]["text"] for row in keyboard]
        assert any("🇬🇧 London" in t and "UK" in t for t in button_texts)
        assert any(
            "node_invite:" in row[0]["callback_data"] for row in keyboard
        )

    def test_nodes_command_shows_users_home_node(
        self, client, db, monkeypatch
    ):
        keyboard_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send_with_keyboard",
            lambda chat_id, text, keyboard: keyboard_messages.append(
                (chat_id, text, keyboard)
            ),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/nodes"))

        assert response.status_code == 200
        assert len(keyboard_messages) == 1
        chat_id, text, keyboard = keyboard_messages[0]
        button_texts = [row[0]["text"] for row in keyboard]
        assert "Home Node" in button_texts

    def test_nodes_command_excludes_nodes_without_group(
        self, client, db, monkeypatch
    ):
        keyboard_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send_with_keyboard",
            lambda chat_id, text, keyboard: keyboard_messages.append(
                (chat_id, text, keyboard)
            ),
        )
        self._setup_member()

        Node.objects.create(
            group=None,
            name="London",
            emoji="🇬🇧",
        )

        response = post_webhook(client, self._make_dm("/nodes"))

        assert response.status_code == 200
        chat_id, text, keyboard = keyboard_messages[0]
        button_texts = [row[0]["text"] for row in keyboard]
        assert "Home Node" in button_texts
        assert "London" not in button_texts

    def test_nodes_command_shows_multiple_nodes(self, client, db, monkeypatch):
        keyboard_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send_with_keyboard",
            lambda chat_id, text, keyboard: keyboard_messages.append(
                (chat_id, text, keyboard)
            ),
        )
        self._setup_member()

        group1 = Group.objects.create(
            telegram_id=-100123, display_name="Group 1"
        )
        group2 = Group.objects.create(
            telegram_id=-100124, display_name="Group 2"
        )
        Node.objects.create(
            group=group1,
            name="London",
            emoji="🇬🇧",
        )
        Node.objects.create(
            group=group2,
            name="Paris",
            emoji="🇫🇷",
            location="France",
        )

        response = post_webhook(client, self._make_dm("/nodes"))

        assert response.status_code == 200
        chat_id, text, keyboard = keyboard_messages[0]
        assert len(keyboard) == 3
        button_texts = [row[0]["text"] for row in keyboard]
        assert any("London" in t for t in button_texts)
        assert any("Paris" in t for t in button_texts)
        assert any("France" in t for t in button_texts)

    def test_nodes_command_shows_node_without_emoji(
        self, client, db, monkeypatch
    ):
        keyboard_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send_with_keyboard",
            lambda chat_id, text, keyboard: keyboard_messages.append(
                (chat_id, text, keyboard)
            ),
        )
        self._setup_member()

        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        Node.objects.create(
            group=group,
            name="Remote",
            emoji="",
        )

        response = post_webhook(client, self._make_dm("/nodes"))

        assert response.status_code == 200
        chat_id, text, keyboard = keyboard_messages[0]
        button_texts = [row[0]["text"] for row in keyboard]
        assert "Remote" in button_texts

    def test_help_shows_nodes_command(self, client, db, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        self._setup_member()

        response = post_webhook(client, self._make_dm("/help"))

        assert response.status_code == 200
        text = sent_messages[0][1]
        assert "/nodes" in text

    def test_callback_query_node_invite_sends_link(
        self, client, db, monkeypatch
    ):
        sent_messages = []
        callback_answers = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.answer_callback_query",
            lambda qid, text=None: callback_answers.append((qid, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.export_chat_invite_link",
            lambda chat_id: "https://t.me/+abc123",
        )

        group = Group.objects.create(
            telegram_id=-100123, display_name="Test Group"
        )
        node = Node.objects.create(
            group=group,
            name="London",
            emoji="🇬🇧",
        )

        response = post_webhook(
            client, self._make_callback_query(f"node_invite:{node.slug}")
        )

        assert response.status_code == 200
        assert len(callback_answers) == 1
        assert callback_answers[0][0] == "callback123"
        assert callback_answers[0][1] is None
        assert len(sent_messages) == 1
        assert "https://t.me/+abc123" in sent_messages[0][1]
        assert "🇬🇧 London" in sent_messages[0][1]

    def test_callback_query_node_not_found(self, client, db, monkeypatch):
        sent_messages = []
        callback_answers = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.answer_callback_query",
            lambda qid, text=None: callback_answers.append((qid, text)),
        )

        response = post_webhook(
            client,
            self._make_callback_query(
                "node_invite:00000000-0000-0000-0000-000000000000"
            ),
        )

        assert response.status_code == 200
        assert len(callback_answers) == 1
        assert callback_answers[0][1] == "Node not found"
        assert len(sent_messages) == 0

    def test_callback_query_no_group_linked(self, client, db, monkeypatch):
        sent_messages = []
        callback_answers = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.answer_callback_query",
            lambda qid, text=None: callback_answers.append((qid, text)),
        )

        node = Node.objects.create(
            group=None,
            name="London",
            emoji="🇬🇧",
        )

        response = post_webhook(
            client, self._make_callback_query(f"node_invite:{node.slug}")
        )

        assert response.status_code == 200
        assert len(callback_answers) == 1
        assert callback_answers[0][1] == "No group linked"
        assert len(sent_messages) == 0

    def test_callback_query_unknown_type(self, client, db, monkeypatch):
        callback_answers = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.answer_callback_query",
            lambda qid, text=None: callback_answers.append((qid, text)),
        )

        response = post_webhook(
            client, self._make_callback_query("unknown_action:123")
        )

        assert response.status_code == 200
        assert len(callback_answers) == 1
        assert callback_answers[0][1] is None
