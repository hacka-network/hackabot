import os
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch

import arrow
import pytest
import responses
from django.utils import timezone

from hackabot.apps.bot.models import (
    ActivityDay,
    Event,
    Group,
    MeetupPhoto,
    Node,
    Person,
    Poll,
    PollAnswer,
)
from hackabot.apps.bot.telegram import (
    HACKA_NETWORK_GLOBAL_CHAT_ID,
    TELEGRAM_API_BASE,
)
from hackabot.apps.worker.run import (
    INVITE_GRACE_PERIOD_DAYS,
    POLL_DAY,
    POLL_HOUR,
    SUMMARY_DAY,
    SUMMARY_HOUR,
    check_all_nodes,
    has_yes_responses_this_week,
    process_node_events,
    process_node_poll,
    process_weekly_summary,
    process_yearly_summary,
    should_send_event_reminder,
    should_send_global_invite,
    should_send_poll,
    should_send_weekly_summary,
    should_send_yearly_summary,
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

    def test_returns_true_at_any_minute_in_poll_hour(self, node):
        monday_7_30am_utc = arrow.Arrow(2024, 1, 8, 7, 30, 0, tzinfo="UTC")
        assert should_send_poll(node, monday_7_30am_utc) is True

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

    def test_drinks_fires_at_event_time(self, node):
        event = Event(node=node, type="drinks", time=time(18, 0), where="")
        thursday_6pm = arrow.Arrow(
            2024, 1, 11, 18, 0, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_6pm) is True

    def test_drinks_does_not_fire_30_mins_before(self, node):
        event = Event(node=node, type="drinks", time=time(18, 0), where="")
        thursday_530pm = arrow.Arrow(
            2024, 1, 11, 17, 30, 0, tzinfo="America/New_York"
        )
        assert should_send_event_reminder(event, thursday_530pm) is False


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

            monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
            process_node_poll(node, monday_7am_utc)

            node.refresh_from_db()
            assert node.last_poll_sent_at is not None
            assert len(responses.calls) == 3

    @responses.activate
    def test_does_not_send_poll_on_wrong_day(self, node):
        tuesday_7am_utc = arrow.Arrow(2024, 1, 9, 7, 0, 0, tzinfo="UTC")
        process_node_poll(node, tuesday_7am_utc)

        node.refresh_from_db()
        assert node.last_poll_sent_at is None
        assert len(responses.calls) == 0


class TestProcessNodeEvents:
    @responses.activate
    def test_sends_reminder_and_updates_timestamp(
        self, node, events, poll_with_yes
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

            # intros_event is at 9:30am, reminder sent 30 mins before at 9:00am
            intros_event = events[0]
            thursday_9am = arrow.Arrow(
                2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
            )
            process_node_events(node, thursday_9am)

            intros_event.refresh_from_db()
            assert intros_event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1

    @responses.activate
    def test_does_not_send_reminder_on_wrong_day(self, node, events):
        wednesday = arrow.Arrow(
            2024, 1, 10, 9, 0, 0, tzinfo="America/New_York"
        )
        process_node_events(node, wednesday)

        for event in events:
            event.refresh_from_db()
            assert event.last_reminder_sent_at is None
        assert len(responses.calls) == 0

    @responses.activate
    def test_skips_reminders_when_no_yes_responses(self, node, events):
        Poll.objects.create(
            telegram_id="poll_no_yes",
            node=node,
            question="Who's coming?",
            yes_count=0,
            no_count=5,
        )

        thursday_9am = arrow.Arrow(
            2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
        )
        process_node_events(node, thursday_9am)

        for event in events:
            event.refresh_from_db()
            assert event.last_reminder_sent_at is None
        assert len(responses.calls) == 0

    @responses.activate
    def test_skips_reminders_when_no_poll_exists(self, node, events):
        thursday_9am = arrow.Arrow(
            2024, 1, 11, 9, 0, 0, tzinfo="America/New_York"
        )
        process_node_events(node, thursday_9am)

        for event in events:
            event.refresh_from_db()
            assert event.last_reminder_sent_at is None
        assert len(responses.calls) == 0


class TestEventReminderMessages:
    @responses.activate
    def test_intros_reminder_message(self, node, group, poll_with_yes):
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
            process_node_events(node, thursday_9am)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Intros are at 9:30am" in request_body

    @responses.activate
    def test_demos_reminder_message(self, node, group, poll_with_yes):
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
            process_node_events(node, thursday_330pm)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Demos are at 4pm" in request_body

    @responses.activate
    def test_lunch_reminder_with_location(self, node, group, poll_with_yes):
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
            process_node_events(node, thursday_1130am)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Lunch at 12pm" in request_body
            assert "Cafeteria" in request_body

    @responses.activate
    def test_lunch_reminder_without_location(self, node, group, poll_with_yes):
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
            process_node_events(node, thursday_noon)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Lunch at 12:30pm" in request_body
            assert "in" not in request_body

    @responses.activate
    def test_drinks_reminder_with_location(self, node, group, poll_with_yes):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            # Event at 6pm (18:00), drinks fire at event time
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
            process_node_events(node, thursday_6pm)

            event.refresh_from_db()
            assert event.last_reminder_sent_at is not None
            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Rooftop Bar" in request_body

    @responses.activate
    def test_drinks_reminder_without_location(
        self, node, group, poll_with_yes
    ):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            # Event at 6:30pm (18:30), drinks fire at event time
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
            process_node_events(node, thursday_630pm)

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
            Node.objects.create(
                group=group,
                name="Disabled Node",
                timezone="America/New_York",
                disabled=True,
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

            monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
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

            monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
            with patch("hackabot.apps.worker.run.sentry_sdk") as mock_sentry:
                process_node_poll(node, monday_7am_utc)
                assert mock_sentry.capture_exception.called

            node.refresh_from_db()
            assert node.last_poll_sent_at is None

    @responses.activate
    def test_event_reminder_error_does_not_update_timestamp(
        self, node, events, poll_with_yes
    ):
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
            with patch("hackabot.apps.worker.run.sentry_sdk") as mock_sentry:
                process_node_events(node, thursday_9am)
                assert mock_sentry.capture_exception.called

            intros_event.refresh_from_db()
            assert intros_event.last_reminder_sent_at is None


class TestDynamicNodeHandling:
    @responses.activate
    def test_new_nodes_are_picked_up(self, db, group):
        Node.objects.all().delete()
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

            monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_7am_utc
                check_all_nodes()

            assert len(responses.calls) == 0

            new_node = Node.objects.create(
                group=group,
                name="New Node",
                timezone="America/New_York",
            )
            Node.objects.filter(pk=new_node.pk).update(
                created=timezone.now() - timedelta(days=90)
            )

            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_7am_utc
                check_all_nodes()

            new_node.refresh_from_db()
            assert new_node.last_poll_sent_at is not None
            assert len(responses.calls) == 3


class TestDisabledNodes:
    @responses.activate
    def test_disabled_nodes_skipped_by_check_all_nodes(self, db, group):
        Node.objects.all().delete()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            Node.objects.create(
                group=group,
                name="Disabled Node",
                timezone="America/New_York",
                disabled=True,
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

            monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
            with patch("hackabot.apps.worker.run.arrow") as mock_arrow:
                mock_arrow.now.return_value = monday_7am_utc
                check_all_nodes()

            assert len(responses.calls) == 0


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
        assert (
            should_send_weekly_summary(global_group, thursday_7am_utc) is False
        )

    def test_returns_false_on_wrong_hour(self, global_group):
        friday_8am_utc = arrow.Arrow(2024, 1, 12, 8, 0, 0, tzinfo="UTC")
        assert (
            should_send_weekly_summary(global_group, friday_8am_utc) is False
        )

    def test_returns_true_at_any_minute_in_summary_hour(self, global_group):
        friday_7_30am_utc = arrow.Arrow(2024, 1, 12, 7, 30, 0, tzinfo="UTC")
        assert (
            should_send_weekly_summary(global_group, friday_7_30am_utc) is True
        )

    def test_returns_false_if_summary_sent_recently(self, global_group):
        global_group.last_weekly_summary_sent_at = timezone.now() - timedelta(
            days=3
        )
        friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
        assert (
            should_send_weekly_summary(global_group, friday_7am_utc) is False
        )

    def test_returns_true_if_summary_sent_over_6_days_ago(self, global_group):
        global_group.last_weekly_summary_sent_at = timezone.now() - timedelta(
            days=7
        )
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
            process_weekly_summary(friday_7am_utc)

            global_group.refresh_from_db()
            assert global_group.last_weekly_summary_sent_at is not None
            assert len(responses.calls) == 1

    @responses.activate
    def test_does_not_send_summary_on_wrong_day(self, global_group):
        thursday_7am_utc = arrow.Arrow(2024, 1, 11, 7, 0, 0, tzinfo="UTC")
        process_weekly_summary(thursday_7am_utc)

        global_group.refresh_from_db()
        assert global_group.last_weekly_summary_sent_at is None
        assert len(responses.calls) == 0

    @responses.activate
    def test_does_not_send_if_global_group_missing(self, db):
        friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
        process_weekly_summary(friday_7am_utc)

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
            person1 = Person.objects.create(
                telegram_id=11111, first_name="Alice"
            )
            person2 = Person.objects.create(
                telegram_id=22222, first_name="Bob"
            )
            PollAnswer.objects.create(poll=poll, person=person1, yes=True)
            PollAnswer.objects.create(poll=poll, person=person2, yes=True)

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

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
            process_weekly_summary(friday_7am_utc)

            global_group.refresh_from_db()
            assert global_group.last_weekly_summary_sent_at is None
            assert len(responses.calls) == 0

    @responses.activate
    def test_summary_only_includes_nodes_with_attendance(
        self, db, global_group
    ):
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
            person = Person.objects.create(
                telegram_id=33333, first_name="Charlie"
            )
            PollAnswer.objects.create(poll=poll, person=person, yes=True)

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Active Node" in request_body
            assert "Empty Node" not in request_body

    @responses.activate
    def test_summary_includes_top_talker(self, db, global_group):
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
                telegram_id="poll_top_talker_test",
                node=node,
                question="Who's coming?",
            )
            person1 = Person.objects.create(
                telegram_id=11111, first_name="Alice", username="alice_test"
            )
            person2 = Person.objects.create(
                telegram_id=22222, first_name="Bob", username="bob"
            )
            PollAnswer.objects.create(poll=poll, person=person1, yes=True)
            PollAnswer.objects.create(poll=poll, person=person2, yes=True)

            today = timezone.now().date()
            ActivityDay.objects.create(
                person=person1,
                group=global_group,
                date=today,
                message_count=50,
            )
            ActivityDay.objects.create(
                person=person2,
                group=global_group,
                date=today,
                message_count=10,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Biggest yapper" in request_body
            assert "alice\\\\_test" in request_body
            assert "50 messages" in request_body

    @responses.activate
    def test_summary_shows_first_name_when_no_username(self, db, global_group):
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
                telegram_id="poll_first_name_test",
                node=node,
                question="Who's coming?",
            )
            person = Person.objects.create(
                telegram_id=33333, first_name="Charlie", username=""
            )
            PollAnswer.objects.create(poll=poll, person=person, yes=True)

            today = timezone.now().date()
            ActivityDay.objects.create(
                person=person,
                group=global_group,
                date=today,
                message_count=25,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Biggest yapper" in request_body
            assert "Charlie" in request_body
            assert "25 messages" in request_body

    @responses.activate
    def test_summary_excludes_activity_from_seven_days_ago(
        self, db, global_group
    ):
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
                telegram_id="poll_boundary_test",
                node=node,
                question="Who's coming?",
            )
            person = Person.objects.create(
                telegram_id=44444, first_name="Dana", username="dana"
            )
            PollAnswer.objects.create(poll=poll, person=person, yes=True)

            now = timezone.now()
            seven_days_ago = (now - timedelta(days=7)).date()
            six_days_ago = (now - timedelta(days=6)).date()
            ActivityDay.objects.create(
                person=person,
                group=global_group,
                date=seven_days_ago,
                message_count=999,
            )
            ActivityDay.objects.create(
                person=person,
                group=global_group,
                date=six_days_ago,
                message_count=3,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

            assert len(responses.calls) == 1
            request_body = responses.calls[0].request.body.decode()
            assert "Biggest yapper" in request_body
            assert "3 messages" in request_body
            assert "999 messages" not in request_body

    @responses.activate
    def test_summary_includes_country_count(self, db, global_group, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            other_group = Group.objects.create(
                telegram_id=-1009998888, display_name="Other Group"
            )
            node1 = Node.objects.create(
                group=group,
                name="Hackagu",
                emoji="🇮🇩",
                timezone="UTC",
            )
            node2 = Node.objects.create(
                group=other_group,
                name="Hackaboa",
                emoji="🇵🇹",
                timezone="UTC",
            )
            poll1 = Poll.objects.create(
                telegram_id="poll_c1", node=node1, question="?"
            )
            poll2 = Poll.objects.create(
                telegram_id="poll_c2", node=node2, question="?"
            )
            person1 = Person.objects.create(
                telegram_id=70001, first_name="P1", username="p1"
            )
            person2 = Person.objects.create(
                telegram_id=70002, first_name="P2", username="p2"
            )
            PollAnswer.objects.create(poll=poll1, person=person1, yes=True)
            PollAnswer.objects.create(poll=poll2, person=person2, yes=True)

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

            body = responses.calls[0].request.body.decode()
            assert "*2 countries*" in body

    @responses.activate
    def test_summary_includes_yappiest_group_chat(
        self, db, global_group, group
    ):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node = Node.objects.create(
                group=group,
                name="Hackaigon",
                emoji="🇻🇳",
                timezone="UTC",
            )
            poll = Poll.objects.create(
                telegram_id="poll_yappiest", node=node, question="?"
            )
            person = Person.objects.create(
                telegram_id=70003, first_name="P", username="p_yap"
            )
            PollAnswer.objects.create(poll=poll, person=person, yes=True)
            ActivityDay.objects.create(
                person=person,
                group=group,
                date=timezone.now().date(),
                message_count=42,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

            body = responses.calls[0].request.body.decode()
            assert "Yappiest group chat of the week is" in body
            assert "Hackaigon" in body
            assert "(42 messages)" in body

    @responses.activate
    def test_summary_includes_first_timers(self, db, global_group, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node = Node.objects.create(
                group=group,
                name="Hackagu",
                emoji="🇮🇩",
                timezone="UTC",
            )
            new_face = Person.objects.create(
                telegram_id=70004,
                first_name="New",
                username="new_face",
            )
            returning = Person.objects.create(
                telegram_id=70005,
                first_name="Returning",
                username="returning",
            )
            poll = Poll.objects.create(
                telegram_id="poll_ft", node=node, question="?"
            )
            PollAnswer.objects.create(poll=poll, person=new_face, yes=True)
            PollAnswer.objects.create(poll=poll, person=returning, yes=True)

            old_poll = Poll.objects.create(
                telegram_id="poll_ft_old", node=node, question="?"
            )
            old_dt = timezone.now() - timedelta(days=30)
            Poll.objects.filter(pk=old_poll.pk).update(created=old_dt)
            PollAnswer.objects.create(
                poll=old_poll, person=returning, yes=True
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

            body = responses.calls[0].request.body.decode()
            assert "First-timers this week:" in body
            assert "new\\\\_face" in body
            assert "@returning" not in body

    @responses.activate
    def test_summary_includes_longest_streak(self, db, global_group, group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node = Node.objects.create(
                group=group,
                name="Hackagu",
                emoji="🇮🇩",
                timezone="UTC",
            )
            person = Person.objects.create(
                telegram_id=70006,
                first_name="Streaker",
                username="streaker",
            )

            now = timezone.now()
            current_monday = now.date() - timedelta(days=now.weekday())
            for weeks_back in range(3):
                p = Poll.objects.create(
                    telegram_id=f"poll_streak_{weeks_back}",
                    node=node,
                    question="?",
                )
                target_date = current_monday - timedelta(days=7 * weeks_back)
                target_dt = timezone.make_aware(
                    datetime.combine(target_date, time(12, 0))
                )
                Poll.objects.filter(pk=p.pk).update(created=target_dt)
                PollAnswer.objects.create(poll=p, person=person, yes=True)

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            friday_7am_utc = arrow.Arrow(2024, 1, 12, 7, 0, 0, tzinfo="UTC")
            process_weekly_summary(friday_7am_utc)

            body = responses.calls[0].request.body.decode()
            assert "Longest streak is" in body
            assert "@streaker" in body
            assert "(3 attendances in a row!)" in body


class TestShouldSendYearlySummary:
    @pytest.fixture
    def global_group(self, db):
        return Group.objects.create(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID),
            display_name="Hacka* Network Global",
        )

    def test_returns_true_on_dec_31_at_correct_time(self, global_group):
        dec_31_noon_utc = arrow.Arrow(2026, 12, 31, 12, 0, 0, tzinfo="UTC")
        assert should_send_yearly_summary(global_group, dec_31_noon_utc) is True

    def test_returns_false_on_dec_30(self, global_group):
        dec_30_7am_utc = arrow.Arrow(2026, 12, 30, 7, 0, 0, tzinfo="UTC")
        assert (
            should_send_yearly_summary(global_group, dec_30_7am_utc) is False
        )

    def test_returns_false_on_jan_1(self, global_group):
        jan_1_7am_utc = arrow.Arrow(2027, 1, 1, 7, 0, 0, tzinfo="UTC")
        assert should_send_yearly_summary(global_group, jan_1_7am_utc) is False

    def test_returns_false_on_wrong_hour(self, global_group):
        dec_31_1pm_utc = arrow.Arrow(2026, 12, 31, 13, 0, 0, tzinfo="UTC")
        assert (
            should_send_yearly_summary(global_group, dec_31_1pm_utc) is False
        )

    def test_returns_false_if_sent_recently(self, global_group):
        global_group.last_yearly_summary_sent_at = timezone.now() - timedelta(
            days=10
        )
        dec_31_noon_utc = arrow.Arrow(2026, 12, 31, 12, 0, 0, tzinfo="UTC")
        assert (
            should_send_yearly_summary(global_group, dec_31_noon_utc) is False
        )

    def test_returns_true_if_never_sent(self, global_group):
        global_group.last_yearly_summary_sent_at = None
        dec_31_noon_utc = arrow.Arrow(2026, 12, 31, 12, 0, 0, tzinfo="UTC")
        assert should_send_yearly_summary(global_group, dec_31_noon_utc) is True


class TestProcessYearlySummary:
    @pytest.fixture
    def global_group(self, db):
        return Group.objects.create(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID),
            display_name="Hacka* Network Global",
        )

    @responses.activate
    def test_sends_summary_and_updates_timestamp(
        self, db, global_group, group
    ):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node = Node.objects.create(
                group=group,
                name="Test Node",
                emoji="🚀",
                timezone="UTC",
            )
            poll = Poll.objects.create(
                telegram_id="poll_yearly_proc_test",
                node=node,
                question="Who's coming?",
            )
            person = Person.objects.create(
                telegram_id=88888, first_name="Eve", username="eve"
            )
            PollAnswer.objects.create(poll=poll, person=person, yes=True)

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            dec_31_noon_utc = arrow.Arrow(2026, 12, 31, 12, 0, 0, tzinfo="UTC")
            process_yearly_summary(dec_31_noon_utc)

            global_group.refresh_from_db()
            assert global_group.last_yearly_summary_sent_at is not None
            assert len(responses.calls) == 1

    @responses.activate
    def test_does_not_send_on_wrong_day(self, global_group):
        nov_15_7am_utc = arrow.Arrow(2026, 11, 15, 7, 0, 0, tzinfo="UTC")
        process_yearly_summary(nov_15_7am_utc)

        global_group.refresh_from_db()
        assert global_group.last_yearly_summary_sent_at is None
        assert len(responses.calls) == 0

    @responses.activate
    def test_does_not_send_if_global_group_missing(self, db):
        dec_31_noon_utc = arrow.Arrow(2026, 12, 31, 12, 0, 0, tzinfo="UTC")
        process_yearly_summary(dec_31_noon_utc)

        assert len(responses.calls) == 0


class TestYearlySummaryMessage:
    @pytest.fixture
    def global_group(self, db):
        return Group.objects.create(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID),
            display_name="Hacka* Network Global",
        )

    @responses.activate
    def test_summary_includes_all_year_in_review_sections(
        self, db, global_group, group
    ):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node_a = Node.objects.create(
                group=group,
                name="Bali",
                emoji="🌴",
                timezone="UTC",
            )
            other_group = Group.objects.create(
                telegram_id=-1009998888,
                display_name="Other Node Group",
            )
            node_b = Node.objects.create(
                group=other_group,
                name="Lisbon",
                emoji="🇵🇹",
                timezone="UTC",
            )

            now = timezone.now()
            year = now.year
            top_yapper = Person.objects.create(
                telegram_id=10001,
                first_name="Talker",
                username="big_talker",
            )
            quiet = Person.objects.create(
                telegram_id=10002, first_name="Quiet", username="quiet"
            )
            for i in range(5):
                p = Poll.objects.create(
                    telegram_id=f"poll_year_a_{i}",
                    node=node_a,
                    question="?",
                )
                PollAnswer.objects.create(poll=p, person=top_yapper, yes=True)
                if i == 0:
                    PollAnswer.objects.create(poll=p, person=quiet, yes=True)
            for i in range(3):
                p = Poll.objects.create(
                    telegram_id=f"poll_year_b_{i}",
                    node=node_b,
                    question="?",
                )
                PollAnswer.objects.create(poll=p, person=quiet, yes=True)

            ActivityDay.objects.create(
                person=top_yapper,
                group=global_group,
                date=now.date(),
                message_count=500,
            )
            ActivityDay.objects.create(
                person=quiet,
                group=global_group,
                date=now.date(),
                message_count=10,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            from hackabot.apps.bot.telegram import send_yearly_summary

            assert send_yearly_summary() is True

            assert len(responses.calls) == 1
            body = responses.calls[0].request.body.decode()
            assert f"*Hacka\\uff0a Network {year} Year in Review*" in body
            assert "Top yappers:" in body
            assert "big\\\\_talker" in body
            assert "500 messages" in body
            assert "Top nodes:" in body
            assert "Bali" in body
            assert "6 attendances" in body
            assert "Lisbon" in body
            assert "3 attendances" in body
            assert "New nodes this year:" in body
            assert "The Regular:" in body
            assert "5 times" in body
            assert "The Explorer:" in body
            assert "2 different nodes" in body
            assert "Attendance record:" in body
            assert "2 attendees" in body
            assert "Happy New Year" not in body

    @responses.activate
    def test_summary_excludes_activity_from_other_years(
        self, db, global_group
    ):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            person = Person.objects.create(
                telegram_id=20001, first_name="Loud", username="loud"
            )
            now = timezone.now()
            ActivityDay.objects.create(
                person=person,
                group=global_group,
                date=now.date().replace(year=now.year - 1),
                message_count=9999,
            )
            ActivityDay.objects.create(
                person=person,
                group=global_group,
                date=now.date(),
                message_count=7,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            from hackabot.apps.bot.telegram import send_yearly_summary

            assert send_yearly_summary() is True
            body = responses.calls[0].request.body.decode()
            assert "7 messages" in body
            assert "9999 messages" not in body

    @responses.activate
    def test_summary_skipped_if_no_activity(self, db, global_group):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            from hackabot.apps.bot.telegram import send_yearly_summary

            assert send_yearly_summary() is False
            assert len(responses.calls) == 0

    @responses.activate
    def test_summary_includes_photographer_of_the_year(
        self, db, global_group, group
    ):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node = Node.objects.create(
                group=group,
                name="Bali",
                emoji="🌴",
                timezone="UTC",
            )
            shutter = Person.objects.create(
                telegram_id=30001,
                first_name="Shutter",
                username="shutter_bug",
            )
            other = Person.objects.create(
                telegram_id=30002,
                first_name="Other",
                username="other",
            )
            for i in range(5):
                MeetupPhoto.objects.create(
                    node=node,
                    telegram_file_id=f"file_shutter_{i}",
                    image_data=b"data",
                    uploaded_by=shutter,
                )
            MeetupPhoto.objects.create(
                node=node,
                telegram_file_id="file_other_0",
                image_data=b"data",
                uploaded_by=other,
            )

            ActivityDay.objects.create(
                person=shutter,
                group=global_group,
                date=timezone.now().date(),
                message_count=1,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            from hackabot.apps.bot.telegram import send_yearly_summary

            assert send_yearly_summary() is True
            body = responses.calls[0].request.body.decode()
            assert "Photographer of the Year:" in body
            assert "shutter\\\\_bug" in body
            assert "5 photos" in body

    @responses.activate
    def test_summary_excludes_new_nodes_from_prior_years(
        self, db, global_group, group
    ):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            old_node = Node.objects.create(
                group=group,
                name="OldNode",
                emoji="👴",
                timezone="UTC",
            )
            Node.objects.filter(pk=old_node.pk).update(
                created=timezone.now() - timedelta(days=400)
            )
            new_group = Group.objects.create(
                telegram_id=-2002, display_name="New"
            )
            new_node = Node.objects.create(
                group=new_group,
                name="NewNode",
                emoji="🆕",
                timezone="UTC",
            )

            person = Person.objects.create(
                telegram_id=40001, first_name="P", username="p"
            )
            poll = Poll.objects.create(
                telegram_id="poll_y_new", node=new_node, question="?"
            )
            PollAnswer.objects.create(poll=poll, person=person, yes=True)
            ActivityDay.objects.create(
                person=person,
                group=global_group,
                date=timezone.now().date(),
                message_count=1,
            )

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={"ok": True, "result": {"message_id": 1001}},
                status=200,
            )

            from hackabot.apps.bot.telegram import send_yearly_summary

            assert send_yearly_summary() is True
            body = responses.calls[0].request.body.decode()
            assert "New nodes this year:" in body
            assert "NewNode" in body
            assert "OldNode" not in body


class TestShouldSendGlobalInvite:
    def test_returns_true_for_old_node_with_invite_enabled(self, node):
        node.send_global_invite = True
        node.created = timezone.now() - timedelta(days=90)
        node.save()
        assert should_send_global_invite(node) is True

    def test_returns_false_for_new_node(self, node):
        node.send_global_invite = True
        node.created = timezone.now() - timedelta(days=30)
        node.save()
        assert should_send_global_invite(node) is False

    def test_returns_false_when_invite_disabled(self, node):
        node.send_global_invite = False
        node.created = timezone.now() - timedelta(days=90)
        node.save()
        assert should_send_global_invite(node) is False

    def test_returns_false_for_node_exactly_at_grace_period(self, node):
        node.send_global_invite = True
        node.created = timezone.now() - timedelta(days=59)
        node.save()
        assert should_send_global_invite(node) is False

    def test_returns_true_for_node_past_grace_period(self, node):
        node.send_global_invite = True
        node.created = timezone.now() - timedelta(days=60)
        node.save()
        assert should_send_global_invite(node) is True


class TestPollGlobalInvite:
    @responses.activate
    def test_sends_invite_for_old_node(self, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node.send_global_invite = True
            node.created = timezone.now() - timedelta(days=90)
            node.save()

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1002,
                        "poll": {
                            "id": "poll_123",
                            "question": "Test?",
                        },
                    },
                },
                status=200,
            )
            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendMessage",
                json={
                    "ok": True,
                    "result": {"message_id": 1003},
                },
                status=200,
            )
            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/pinChatMessage",
                json={"ok": True, "result": True},
                status=200,
            )

            monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
            process_node_poll(node, monday_7am_utc)

            assert len(responses.calls) == 3
            invite_body = responses.calls[1].request.body.decode()
            assert "global chat" in invite_body

    @responses.activate
    def test_skips_invite_for_new_node(self, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node.send_global_invite = True
            node.created = timezone.now() - timedelta(days=30)
            node.save()

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1002,
                        "poll": {
                            "id": "poll_123",
                            "question": "Test?",
                        },
                    },
                },
                status=200,
            )
            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/pinChatMessage",
                json={"ok": True, "result": True},
                status=200,
            )

            monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
            process_node_poll(node, monday_7am_utc)

            # sendPoll + pinChatMessage, no sendMessage for invite
            assert len(responses.calls) == 2

    @responses.activate
    def test_skips_invite_when_disabled(self, node):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "testtoken"}):
            from hackabot.apps.bot import telegram

            telegram.TELEGRAM_BOT_TOKEN = "testtoken"

            node.send_global_invite = False
            node.created = timezone.now() - timedelta(days=90)
            node.save()

            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/sendPoll",
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1002,
                        "poll": {
                            "id": "poll_123",
                            "question": "Test?",
                        },
                    },
                },
                status=200,
            )
            responses.add(
                responses.POST,
                f"{TELEGRAM_API_BASE}/bottesttoken/pinChatMessage",
                json={"ok": True, "result": True},
                status=200,
            )

            monday_7am_utc = arrow.Arrow(2024, 1, 8, 7, 0, 0, tzinfo="UTC")
            process_node_poll(node, monday_7am_utc)

            # sendPoll + pinChatMessage, no sendMessage for invite
            assert len(responses.calls) == 2
