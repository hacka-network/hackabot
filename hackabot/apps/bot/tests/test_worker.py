import os
from datetime import time, timedelta
from unittest.mock import MagicMock, patch

import arrow
import pytest
import responses
from django.utils import timezone

from hackabot.apps.bot.models import Event, Group, Node, Person, Poll, PollAnswer
from hackabot.apps.bot.telegram import (
    HACKA_NETWORK_GLOBAL_CHAT_ID,
    TELEGRAM_API_BASE,
)
from hackabot.apps.worker.run import (
    POLL_DAY,
    POLL_HOUR,
    POLL_MINUTE,
    EVENT_DAY,
    SUMMARY_DAY,
    SUMMARY_HOUR,
    SUMMARY_MINUTE,
    check_all_nodes,
    process_node_events,
    process_node_poll,
    process_weekly_summary,
    should_send_event_reminder,
    should_send_poll,
    should_send_weekly_summary,
)


class TestShouldSendPoll:
    def test_returns_true_on_monday_at_correct_time(self, node):
        monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
        assert should_send_poll(node, monday_7am_utc) is True

    def test_returns_false_on_wrong_day(self, node):
        tuesday_7am_utc = arrow.Arrow(2024, 1, 9, 7, 0, 0, tzinfo="UTC")
        assert should_send_poll(node, tuesday_7am_utc) is False

    def test_returns_false_on_wrong_hour(self, node):
        monday_8am_utc = arrow.Arrow(2024, 1, 8, 8, 0, 0, tzinfo="UTC")
        assert should_send_poll(node, monday_8am_utc) is False

    def test_returns_false_on_wrong_minute(self, node):
        monday_7_30am_utc = arrow.Arrow(2024, 1, 8, 7, 30, 0, tzinfo="UTC")
        assert should_send_poll(node, monday_7_30am_utc) is False

    def test_returns_false_if_poll_sent_recently(self, node):
        node.last_poll_sent_at = timezone.now() - timedelta(days=3)
        monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
        assert should_send_poll(node, monday_7am_utc) is False

    def test_returns_true_if_poll_sent_over_6_days_ago(self, node):
        node.last_poll_sent_at = timezone.now() - timedelta(days=7)
        monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
        assert should_send_poll(node, monday_7am_utc) is True

    def test_returns_true_if_never_sent_poll(self, node):
        node.last_poll_sent_at = None
        monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
        assert should_send_poll(node, monday_7am_utc) is True


class TestShouldSendEventReminder:
    def test_returns_true_30_mins_before_event_time(self, node):
        # Event at 9:30am, reminder should be sent at 9:00am (30 mins before)
        event = Event(
            node=node, type="intros", time=time(9, 30), where="Main Hall"
        )
        thursday_9am = arrow.Arrow(
            2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_9am) is True

    def test_returns_false_at_event_time(self, node):
        # At the actual event time, reminder should NOT be sent (already sent)
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        thursday_930am = arrow.Arrow(
            2024, 1, 11, 9, 30, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_930am) is False

    def test_returns_false_on_wrong_day(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        wednesday_9am = arrow.Arrow(
            2024, 1, 10, 9, 0, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, wednesday_9am) is False

    def test_returns_false_on_wrong_hour(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        thursday_10am = arrow.Arrow(
            2024, 1, 11, 10, 0, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_10am) is False

    def test_returns_false_on_wrong_minute(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        thursday_915am = arrow.Arrow(
            2024, 1, 11, 9, 15, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_915am) is False

    def test_returns_false_if_reminder_sent_recently(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        event.last_reminder_sent_at = timezone.now() - timedelta(days=3)
        thursday_9am = arrow.Arrow(
            2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_9am) is False

    def test_returns_true_if_reminder_sent_over_6_days_ago(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        event.last_reminder_sent_at = timezone.now() - timedelta(days=7)
        thursday_9am = arrow.Arrow(
            2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_9am) is True

    def test_returns_true_if_never_sent_reminder(self, node):
        event = Event(node=node, type="intros", time=time(9, 30), where="")
        event.last_reminder_sent_at = None
        thursday_9am = arrow.Arrow(
            2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_9am) is True


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

            monday_7am_utc = arrow.Arrow(
                2024, 1, 8, 7, 0, 0, tzinfo="UTC"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_7am_utc
                process_node_poll(node)

            node.refresh_from_db()
            assert node.last_poll_sent_at is not None
            assert len(responses.calls) == 3

    @responses.activate
    def test_does_not_send_poll_on_wrong_day(self, node):
        tuesday_7am_utc = arrow.Arrow(2024, 1, 9, 7, 0, 0, tzinfo="UTC")
        with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
            mock_arrow.now.return_value = tuesday_7am_utc
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

            # intros_event is at 9:30am, reminder sent 30 mins before at 9:00am
            intros_event = events[0]
            thursday_9am = arrow.Arrow(
                2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_9am
                process_node_events(node)

            intros_event.refresh_from_db()
            assert intros_event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1

    @responses.activate
    def test_does_not_send_reminder_on_wrong_day(self, node, events):
        wednesday = arrow.Arrow(2024, 1, 10, 9, 0, 0, tzinfo="America/New_York")
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

            # Event at 9:30am, reminder sent 30 mins before at 9:00am
            event = Event.objects.create(
                node=node, type="intros", time=time(9, 30), where="Main Hall"
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_9am = arrow.Arrow(
                2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_9am
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

            # Event at 4pm (16:00), reminder sent 30 mins before at 3:30pm
            event = Event.objects.create(
                node=node, type="demos", time=time(16, 0), where="Demo Stage"
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_330pm = arrow.Arrow(
                2024, 1, 11, 15, 30, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_330pm
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

            # Event at 12pm, reminder sent 30 mins before at 11:30am
            event = Event.objects.create(
                node=node, type="lunch", time=time(12, 0), where="Cafeteria"
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_1130am = arrow.Arrow(
                2024, 1, 11, 11, 30, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_1130am
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

            # Event at 12:30pm, reminder sent 30 mins before at 12:00pm
            event = Event.objects.create(
                node=node, type="lunch", time=time(12, 30), where=""
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
            assert "Lunch at 12:30pm" in request_body
            assert "in" not in request_body

    @responses.activate
    def test_drinks_reminder_with_location(self, node, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            # Event at 6pm (18:00), reminder sent 30 mins before at 5:30pm
            event = Event.objects.create(
                node=node, type="drinks", time=time(18, 0), where="Rooftop Bar"
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            thursday_530pm = arrow.Arrow(
                2024, 1, 11, 17, 30, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_530pm
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

            # Event at 6:30pm (18:30), reminder sent 30 mins before at 6pm
            event = Event.objects.create(
                node=node, type="drinks", time=time(18, 30), where=""
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

            monday_7am_utc = arrow.Arrow(
                2024, 1, 8, 7, 0, 0, tzinfo="UTC"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_7am_utc
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

            monday_7am_utc = arrow.Arrow(
                2024, 1, 8, 7, 0, 0, tzinfo="UTC"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_7am_utc
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

            # intros_event is at 9:30am, reminder sent 30 mins before at 9:00am
            intros_event = events[0]
            thursday_9am = arrow.Arrow(
                2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = thursday_9am
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

            monday_7am_utc = arrow.Arrow(
                2024, 1, 8, 7, 0, 0, tzinfo="UTC"
            )
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_7am_utc
                check_all_nodes()

            assert len(responses.calls) == 0

            new_node = Node.objects.create(
                group=group,
                name="New Node",
                timezone="America/New_York",
            )

            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_7am_utc
                check_all_nodes()

            new_node.refresh_from_db()
            assert new_node.last_poll_sent_at is not None
            assert len(responses.calls) == 3


class TestShouldSendWeeklySummary:
    @pytest.fixture
    def global_group(self, db):
        return Group.objects.create(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID),
            display_name="Hacka* Network Global",
        )

    def test_returns_true_on_friday_at_correct_time(self, global_group):
        friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
        assert should_send_weekly_summary(global_group, friday_7am_utc) is True

    def test_returns_false_on_wrong_day(self, global_group):
        thursday_7am_utc = arrow.Arrow(2024, 1, 11, 7, 0, 0, tzinfo="UTC")
        assert should_send_weekly_summary(global_group, thursday_7am_utc) is False

    def test_returns_false_on_wrong_hour(self, global_group):
        friday_8am_utc = arrow.Arrow(2024, 1, 12, 8, 0, 0, tzinfo="UTC")
        assert should_send_weekly_summary(global_group, friday_8am_utc) is False

    def test_returns_false_on_wrong_minute(self, global_group):
        friday_7_30am_utc = arrow.Arrow(2024, 1, 12, 7, 30, 0, tzinfo="UTC")
        assert should_send_weekly_summary(global_group, friday_7_30am_utc) is False

    def test_returns_false_if_summary_sent_recently(self, global_group):
        global_group.last_weekly_summary_sent_at = timezone.now() - timedelta(days=3)
        friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
        assert should_send_weekly_summary(global_group, friday_7am_utc) is False

    def test_returns_true_if_summary_sent_over_6_days_ago(self, global_group):
        global_group.last_weekly_summary_sent_at = timezone.now() - timedelta(days=7)
        friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
        assert should_send_weekly_summary(global_group, friday_7am_utc) is True

    def test_returns_true_if_never_sent_summary(self, global_group):
        global_group.last_weekly_summary_sent_at = None
        friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
        assert should_send_weekly_summary(global_group, friday_7am_utc) is True


class TestProcessWeeklySummary:
    @pytest.fixture
    def global_group(self, db):
        return Group.objects.create(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID),
            display_name="Hacka* Network Global",
        )

    @pytest.fixture
    def node_with_attendance(self, db, group):
        node = Node.objects.create(
            group=group,
            name="Test Node",
            emoji="🚀",
            timezone="UTC",
        )
        poll = Poll.objects.create(
            telegram_id="poll_summary_test",
            node=node,
            question="Who's coming?",
        )
        person = Person.objects.create(
            telegram_id=99999,
            first_name="TestPerson",
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)
        return node

    @responses.activate
    def test_sends_summary_and_updates_timestamp(
        self, global_group, node_with_attendance
    ):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = friday_7am_utc
                process_weekly_summary()

            global_group.refresh_from_db()
            assert global_group.last_weekly_summary_sent_at is not None
            assert len(responses.calls) == 1

    @responses.activate
    def test_does_not_send_summary_on_wrong_day(self, global_group):
        thursday_7am_utc = arrow.Arrow(2024, 1, 11, 7, 0, 0, tzinfo="UTC")
        with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
            mock_arrow.now.return_value = thursday_7am_utc
            process_weekly_summary()

        global_group.refresh_from_db()
        assert global_group.last_weekly_summary_sent_at is None
        assert len(responses.calls) == 0

    @responses.activate
    def test_does_not_send_if_global_group_missing(self, db):
        friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
        with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
            mock_arrow.now.return_value = friday_7am_utc
            process_weekly_summary()

        assert len(responses.calls) == 0


class TestWeeklySummaryMessage:
    @pytest.fixture
    def global_group(self, db):
        return Group.objects.create(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID),
            display_name="Hacka* Network Global",
        )

    @responses.activate
    def test_summary_includes_total_count_and_nodes(self, db, global_group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node_group = Group.objects.create(
                telegram_id=-1009999999,
                display_name="Node Group",
            )
            node = Node.objects.create(
                group=node_group,
                name="Bali",
                emoji="🌴",
                timezone="UTC",
            )
            poll = Poll.objects.create(
                telegram_id="poll_msg_test",
                node=node,
                question="Who's coming?",
            )
            person1 = Person.objects.create(telegram_id=11111, first_name="Alice")
            person2 = Person.objects.create(telegram_id=22222, first_name="Bob")
            PollAnswer.objects.create(poll=poll, person=person1, yes=True)
            PollAnswer.objects.create(poll=poll, person=person2, yes=True)

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = friday_7am_utc
                process_weekly_summary()

            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "2 people" in request_body
            assert "Bali" in request_body

    @responses.activate
    def test_summary_not_sent_if_no_attendance(self, db, global_group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = friday_7am_utc
                process_weekly_summary()

            global_group.refresh_from_db()
            assert global_group.last_weekly_summary_sent_at is None
            assert len(responses.calls) == 0

    @responses.activate
    def test_summary_only_includes_nodes_with_attendance(self, db, global_group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            group1 = Group.objects.create(telegram_id=-1008888888)
            group2 = Group.objects.create(telegram_id=-1007777777)

            node1 = Node.objects.create(
                group=group1, name="Active Node", emoji="✅", timezone="UTC"
            )
            Node.objects.create(
                group=group2, name="Empty Node", emoji="❌", timezone="UTC"
            )

            poll = Poll.objects.create(
                telegram_id="poll_active",
                node=node1,
                question="Who's coming?",
            )
            person = Person.objects.create(telegram_id=33333, first_name="Charlie")
            PollAnswer.objects.create(poll=poll, person=person, yes=True)

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = friday_7am_utc
                process_weekly_summary()

            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Active Node" in request_body
            assert "Empty Node" not in request_body
