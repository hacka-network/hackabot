import os
from datetime import time, timedelta
from unittest.mock import MagicMock, patch

import arrow
import pytest
import responses
from django.utils import timezone

from hackabot.apps.bot.models import Event, Node
from hackabot.apps.bot.telegram import TELEGRAM_API_BASE
from hackabot.apps.worker.run import (
    POLL_DAY,
    POLL_HOUR,
    POLL_MINUTE,
    EVENT_DAY,
    check_all_nodes,
    process_node_events,
    process_node_poll,
    should_send_event_reminder,
    should_send_poll,
)


class TestShouldSendPoll:
    def test_returns_true_on_monday_at_correct_time(self, node):
        monday_3pm = arrow.Arrow(2024, 1, 8, 15, 0, 0, tzinfo="America/New_York")
        assert should_send_poll(node, monday_3pm) is True

    def test_returns_false_on_wrong_day(self, node):
        tuesday_3pm = arrow.Arrow(2024, 1, 9, 15, 0, 0, tzinfo="America/New_York")
        assert should_send_poll(node, tuesday_3pm) is False

    def test_returns_false_on_wrong_hour(self, node):
        monday_4pm = arrow.Arrow(2024, 1, 8, 16, 0, 0, tzinfo="America/New_York")
        assert should_send_poll(node, monday_4pm) is False

    def test_returns_false_on_wrong_minute(self, node):
        monday_3_30pm = arrow.Arrow(2024, 1, 8, 15, 30, 0, tzinfo="America/New_York")
        assert should_send_poll(node, monday_3_30pm) is False

    def test_returns_false_if_poll_sent_recently(self, node):
        node.last_poll_sent_at = timezone.now() - timedelta(days=3)
        monday_3pm = arrow.Arrow(2024, 1, 8, 15, 0, 0, tzinfo="America/New_York")
        assert should_send_poll(node, monday_3pm) is False

    def test_returns_true_if_poll_sent_over_6_days_ago(self, node):
        node.last_poll_sent_at = timezone.now() - timedelta(days=7)
        monday_3pm = arrow.Arrow(2024, 1, 8, 15, 0, 0, tzinfo="America/New_York")
        assert should_send_poll(node, monday_3pm) is True

    def test_returns_true_if_never_sent_poll(self, node):
        node.last_poll_sent_at = None
        monday_3pm = arrow.Arrow(2024, 1, 8, 15, 0, 0, tzinfo="America/New_York")
        assert should_send_poll(node, monday_3pm) is True


class TestShouldSendEventReminder:
    def test_returns_true_on_thursday_at_event_time(self, node):
        event = Event(
            node=node, type="intros", time=time(9, 30), where="Main Hall"
        )
        thursday_930am = arrow.Arrow(
            2024, 1, 11, 9, 30, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_930am) is True

    def test_returns_false_on_wrong_day(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        wednesday_930am = arrow.Arrow(
            2024, 1, 10, 9, 30, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, wednesday_930am) is False

    def test_returns_false_on_wrong_hour(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        thursday_1030am = arrow.Arrow(
            2024, 1, 11, 10, 30, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_1030am) is False

    def test_returns_false_on_wrong_minute(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        thursday_900am = arrow.Arrow(
            2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_900am) is False

    def test_returns_false_if_reminder_sent_recently(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        event.last_reminder_sent_at = timezone.now() - timedelta(days=3)
        thursday_930am = arrow.Arrow(
            2024, 1, 11, 9, 30, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_930am) is False

    def test_returns_true_if_reminder_sent_over_6_days_ago(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        event.last_reminder_sent_at = timezone.now() - timedelta(days=7)
        thursday_930am = arrow.Arrow(
            2024, 1, 11, 9, 30, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_930am) is True

    def test_returns_true_if_never_sent_reminder(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        event.last_reminder_sent_at = None
        thursday_930am = arrow.Arrow(
            2024, 1, 11, 9, 30, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_930am) is True


class TestProcessNodePoll:
    @responses.activate
    def test_sends_poll_and_updates_timestamp(self, node):
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

            monday_3pm = arrow.Arrow(
                2024, 1, 8, 15, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_3pm
                process_node_poll(node)

            node.refresh_from_db()
            assert node.last_poll_sent_at is not None
            assert len(responses.calls) == 3

    @responses.activate
    def test_does_not_send_poll_on_wrong_day(self, node):
        tuesday_3pm = arrow.Arrow(2024, 1, 9, 15, 0, 0, tzinfo="America/New_York")
        with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
            mock_arrow.now.return_value = tuesday_3pm
            process_node_poll(node)

        node.refresh_from_db()
        assert node.last_poll_sent_at is None
        assert len(responses.calls) == 0


class TestProcessNodeEvents:
    @responses.activate
    def test_sends_reminder_and_updates_timestamp(self, node, events):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            intros_event = events[0]
            thursday_930am = arrow.Arrow(
                2024, 1, 11, 9, 30, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_930am
                process_node_events(node)

            intros_event.refresh_from_db()
            assert intros_event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1

    @responses.activate
    def test_does_not_send_reminder_on_wrong_day(self, node, events):
        wednesday = arrow.Arrow(2024, 1, 10, 9, 30, 0, tzinfo="America/New_York")
        with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
            mock_arrow.now.return_value = wednesday
            process_node_events(node)

        for event in events:
            event.refresh_from_db()
            assert event.last_reminder_sent_at is None
        assert len(responses.calls) == 0


class TestEventReminderMessages:
    @responses.activate
    def test_intros_reminder_message(self, node, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            event = Event.objects.create(
                node=node, type="intros", time=time(9, 30), where="Main Hall"
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_930am = arrow.Arrow(
                2024, 1, 11, 9, 30, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_930am
                process_node_events(node)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Intros are at 9:30am" in request_body

    @responses.activate
    def test_demos_reminder_message(self, node, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            event = Event.objects.create(
                node=node, type="demos", time=time(16, 0), where="Demo Stage"
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_4pm = arrow.Arrow(
                2024, 1, 11, 16, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_4pm
                process_node_events(node)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Demos are at 4pm" in request_body

    @responses.activate
    def test_lunch_reminder_with_location(self, node, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            event = Event.objects.create(
                node=node, type="lunch", time=time(12, 0), where="Cafeteria"
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_noon = arrow.Arrow(
                2024, 1, 11, 12, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_noon
                process_node_events(node)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Lunch at 12pm" in request_body
            assert "Cafeteria" in request_body

    @responses.activate
    def test_lunch_reminder_without_location(self, node, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            event = Event.objects.create(
                node=node, type="lunch", time=time(12, 30), where=""
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_1230 = arrow.Arrow(
                2024, 1, 11, 12, 30, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_1230
                process_node_events(node)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Lunch at 12:30pm" in request_body
            assert "in" not in request_body

    @responses.activate
    def test_drinks_reminder_with_location(self, node, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            event = Event.objects.create(
                node=node, type="drinks", time=time(18, 0), where="Rooftop Bar"
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_6pm = arrow.Arrow(
                2024, 1, 11, 18, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_6pm
                process_node_events(node)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Rooftop Bar" in request_body

    @responses.activate
    def test_drinks_reminder_without_location(self, node, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            event = Event.objects.create(
                node=node, type="drinks", time=time(18, 30), where=""
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_630pm = arrow.Arrow(
                2024, 1, 11, 18, 30, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_630pm
                process_node_events(node)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Drinks time" in request_body


class TestCheckAllNodes:
    @responses.activate
    def test_processes_all_nodes_with_groups(self, db, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node1 = Node.objects.create(
                group=group,
                name="Node 1",
                timezone="America/New_York",
            )
            node2 = Node.objects.create(
                group=group,
                name="Node 2",
                timezone="America/New_York",
            )
            Node.objects.create(
                group=None,
                name="Node without group",
                timezone="UTC",
            )

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

            monday_3pm = arrow.Arrow(
                2024, 1, 8, 15, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_3pm
                check_all_nodes()

            node1.refresh_from_db()
            node2.refresh_from_db()
            assert node1.last_poll_sent_at is not None
            assert node2.last_poll_sent_at is not None


class TestErrorHandling:
    @responses.activate
    def test_poll_error_does_not_update_timestamp(self, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={"ok": False, "description": "Bad Request"},
                status=400,
            )

            monday_3pm = arrow.Arrow(
                2024, 1, 8, 15, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_3pm
                with patch("hackabot.apps.worker.run.sentry_sdk") as mock_sentry:
                    process_node_poll(node)
                    assert mock_sentry.capture_exception.called

            node.refresh_from_db()
            assert node.last_poll_sent_at is None

    @responses.activate
    def test_event_reminder_error_does_not_update_timestamp(self, node, events):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": False, "description": "Bad Request"},
                status=400,
            )

            intros_event = events[0]
            thursday_930am = arrow.Arrow(
                2024, 1, 11, 9, 30, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_930am
                with patch("hackabot.apps.worker.run.sentry_sdk") as mock_sentry:
                    process_node_events(node)
                    assert mock_sentry.capture_exception.called

            intros_event.refresh_from_db()
            assert intros_event.last_reminder_sent_at is None


class TestDynamicNodeHandling:
    @responses.activate
    def test_new_nodes_are_picked_up(self, db, group):
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

            monday_3pm = arrow.Arrow(
                2024, 1, 8, 15, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_3pm
                check_all_nodes()

            assert len(responses.calls) == 0

            new_node = Node.objects.create(
                group=group,
                name="New Node",
                timezone="America/New_York",
            )

            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_3pm
                check_all_nodes()

            new_node.refresh_from_db()
            assert new_node.last_poll_sent_at is not None
            assert len(responses.calls) == 3
