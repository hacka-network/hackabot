import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from django.test import Client
from django.utils import timezone

from hackabot.apps.bot.models import (
    ActivityDay,
    Group,
    GroupPerson,
    Node,
    Person,
    Poll,
    PollAnswer,
)


@pytest.fixture
def mock_attending_window():
    wed_10am_utc = datetime(2026, 1, 7, 10, 0, 0, tzinfo=dt_timezone.utc)
    with patch("hackabot.apps.bot.views.datetime") as mock_dt:
        mock_dt.now.return_value = wed_10am_utc
        mock_dt.fromtimestamp = datetime.fromtimestamp
        yield wed_10am_utc


@pytest.fixture
def client():
    return Client()


class TestApiNodes:
    def test_empty_nodes_list(self, client, db):
        Node.objects.all().delete()

        response = client.get("/api/nodes/")

        assert response.status_code == 200
        assert response.json() == {
            "nodes": [],
            "people": [],
            "stats": {"people_count": 0},
        }

    def test_cors_headers(self, client, db):
        response = client.get("/api/nodes/")

        assert response["Access-Control-Allow-Origin"] == "*"
        assert "GET" in response["Access-Control-Allow-Methods"]
        assert "OPTIONS" in response["Access-Control-Allow-Methods"]

    def test_cors_preflight(self, client, db):
        response = client.options("/api/nodes/")

        assert response.status_code == 200
        assert response["Access-Control-Allow-Origin"] == "*"

    def test_method_not_allowed(self, client, db):
        response = client.post("/api/nodes/")

        assert response.status_code == 405

    def test_single_node(self, client, db):
        Node.objects.all().delete()
        node = Node.objects.create(
            name="Test Node",
            emoji="🚀",
            signup_url="https://example.com",
            established=2020,
        )

        response = client.get("/api/nodes/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 1

        node_data = data["nodes"][0]
        assert node_data["id"] == str(node.slug)
        assert node_data["name"] == "Test Node"
        assert node_data["emoji"] == "🚀"
        assert node_data["url"] == "https://example.com"
        assert node_data["established"] == 2020
        assert node_data["location"] == ""
        assert node_data["timezone"] == "UTC"
        assert "people" not in node_data
        assert data["people"] == []

    def test_nodes_ordered_by_established(self, client, db):
        Node.objects.all().delete()
        Node.objects.create(name="Third", established=2022)
        Node.objects.create(name="First", established=2018)
        Node.objects.create(name="Second", established=2020)

        response = client.get("/api/nodes/")

        data = response.json()
        names = [n["name"] for n in data["nodes"]]
        assert names == ["First", "Second", "Third"]

    def test_nodes_null_established_comes_last(self, client, db):
        Node.objects.all().delete()
        Node.objects.create(name="No Year")
        Node.objects.create(name="Has Year", established=2020)

        response = client.get("/api/nodes/")

        data = response.json()
        names = [n["name"] for n in data["nodes"]]
        assert names == ["Has Year", "No Year"]

    def test_people_filtered_by_privacy(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        public_person = Person.objects.create(
            telegram_id=1,
            first_name="Public Person",
            privacy=False,
        )
        private_person = Person.objects.create(
            telegram_id=2,
            first_name="Private Person",
            privacy=True,
        )

        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=public_person, yes=True)
        PollAnswer.objects.create(poll=poll, person=private_person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        people = data["people"]
        assert len(people) == 1
        assert people[0]["display_name"] == "Public Person"
        assert people[0]["nodes"][0]["id"] == str(node.slug)

    def test_people_must_have_display_name_or_username_x(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person_with_name = Person.objects.create(
            telegram_id=1,
            first_name="Has Name",
            privacy=False,
        )
        person_with_x = Person.objects.create(
            telegram_id=2,
            first_name="",
            username_x="has_x",
            privacy=False,
        )
        person_with_nothing = Person.objects.create(
            telegram_id=3,
            first_name="",
            username_x="",
            privacy=False,
        )

        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=person_with_name, yes=True)
        PollAnswer.objects.create(poll=poll, person=person_with_x, yes=True)
        PollAnswer.objects.create(
            poll=poll, person=person_with_nothing, yes=True
        )

        response = client.get("/api/nodes/")

        data = response.json()
        people = data["people"]
        assert len(people) == 2

        display_names = [p["display_name"] for p in people]
        assert "Has Name" in display_names
        assert "" in display_names

    def test_people_only_shows_those_who_attended(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        attended_person = Person.objects.create(
            telegram_id=1,
            first_name="Attended",
            privacy=False,
        )
        never_attended_person = Person.objects.create(
            telegram_id=2,
            first_name="Never Attended",
            privacy=False,
        )

        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=attended_person, yes=True)
        PollAnswer.objects.create(
            poll=poll, person=never_attended_person, yes=False
        )

        response = client.get("/api/nodes/")

        data = response.json()
        people = data["people"]
        assert len(people) == 1
        assert people[0]["display_name"] == "Attended"

    def test_person_fallback_to_last_chatted_node(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Chatter",
            privacy=False,
        )
        GroupPerson.objects.create(
            group=group,
            person=person,
            left=False,
            last_message_at=timezone.now(),
        )

        response = client.get("/api/nodes/")

        data = response.json()
        people = data["people"]
        assert len(people) == 1
        assert people[0]["display_name"] == "Chatter"
        assert people[0]["nodes"][0]["id"] == str(node.slug)
        assert people[0]["nodes"][0]["attending"] is False

    def test_person_fields(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            username_x="alice_x",
            bio="Building cool stuff",
            privacy=False,
        )
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert person_data["display_name"] == "Alice"
        assert person_data["username_x"] == "alice_x"
        assert person_data["bio"] == "Building cool stuff"
        assert person_data["nodes"] == [
            {"id": str(node.slug), "attending": False}
        ]

    def test_person_bio_excluded_when_empty(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            bio="",
            privacy=False,
        )
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert "bio" not in person_data

    def test_person_username_x_null_when_empty(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            username_x="",
            privacy=False,
        )
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert person_data["username_x"] is None

    def test_node_without_group_has_no_people(self, client, db):
        node = Node.objects.create(name="Orphan Node")

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["people"] == []

    def test_person_attending_true_for_recent_yes_vote(
        self, client, db, mock_attending_window
    ):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            privacy=False,
        )
        GroupPerson.objects.create(group=group, person=person, left=False)

        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming this week?",
        )
        mon_7am = mock_attending_window.replace(day=5, hour=7, minute=0)
        Poll.objects.filter(pk=poll.pk).update(created=mon_7am)
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert person_data["nodes"][0]["attending"] is True

    def test_person_attending_false_when_said_no_this_week(
        self, client, db, mock_attending_window
    ):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            privacy=False,
        )

        old_poll = Poll.objects.create(
            telegram_id="poll_old",
            node=node,
            question="Coming last week?",
        )
        old_date = mock_attending_window - timedelta(days=10)
        Poll.objects.filter(pk=old_poll.pk).update(created=old_date)
        PollAnswer.objects.create(poll=old_poll, person=person, yes=True)

        current_poll = Poll.objects.create(
            telegram_id="poll_current",
            node=node,
            question="Coming this week?",
        )
        mon_7am = mock_attending_window.replace(day=5, hour=7, minute=0)
        Poll.objects.filter(pk=current_poll.pk).update(created=mon_7am)
        PollAnswer.objects.create(poll=current_poll, person=person, yes=False)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert person_data["nodes"][0]["attending"] is False

    def test_person_attending_false_for_old_poll(
        self, client, db, mock_attending_window
    ):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            privacy=False,
        )
        GroupPerson.objects.create(group=group, person=person, left=False)

        old_date = mock_attending_window - timedelta(days=10)
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming this week?",
        )
        Poll.objects.filter(pk=poll.pk).update(created=old_date)
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert person_data["nodes"][0]["attending"] is False

    def test_people_sorted_attending_first_then_alphabetically(
        self, client, db, mock_attending_window
    ):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        alice = Person.objects.create(
            telegram_id=1, first_name="Alice", privacy=False
        )
        bob = Person.objects.create(
            telegram_id=2, first_name="Bob", privacy=False
        )
        charlie = Person.objects.create(
            telegram_id=3, first_name="Charlie", privacy=False
        )
        david = Person.objects.create(
            telegram_id=4, first_name="David", privacy=False
        )

        old_poll = Poll.objects.create(
            telegram_id="poll_old",
            node=node,
            question="Coming last week?",
        )
        old_date = mock_attending_window - timedelta(days=10)
        Poll.objects.filter(pk=old_poll.pk).update(created=old_date)
        PollAnswer.objects.create(poll=old_poll, person=alice, yes=True)
        PollAnswer.objects.create(poll=old_poll, person=bob, yes=True)
        PollAnswer.objects.create(poll=old_poll, person=charlie, yes=True)
        PollAnswer.objects.create(poll=old_poll, person=david, yes=True)

        current_poll = Poll.objects.create(
            telegram_id="poll_current",
            node=node,
            question="Coming this week?",
        )
        mon_7am = mock_attending_window.replace(day=5, hour=7, minute=0)
        Poll.objects.filter(pk=current_poll.pk).update(created=mon_7am)
        PollAnswer.objects.create(poll=current_poll, person=charlie, yes=True)
        PollAnswer.objects.create(poll=current_poll, person=alice, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        names = [p["display_name"] for p in data["people"]]
        assert names == ["Alice", "Charlie", "Bob", "David"]

    def test_person_in_multiple_nodes(self, client, db, mock_attending_window):
        group1 = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Group 1",
        )
        group2 = Group.objects.create(
            telegram_id=-1001234567891,
            display_name="Group 2",
        )
        node1 = Node.objects.create(name="Node 1", group=group1)
        node2 = Node.objects.create(name="Node 2", group=group2)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            privacy=False,
        )

        old_poll2 = Poll.objects.create(
            telegram_id="poll_old_node2",
            node=node2,
            question="Coming last week?",
        )
        old_date = mock_attending_window - timedelta(days=10)
        Poll.objects.filter(pk=old_poll2.pk).update(created=old_date)
        PollAnswer.objects.create(poll=old_poll2, person=person, yes=True)

        poll1 = Poll.objects.create(
            telegram_id="poll_node1",
            node=node1,
            question="Coming this week?",
        )
        mon_7am = mock_attending_window.replace(day=5, hour=7, minute=0)
        Poll.objects.filter(pk=poll1.pk).update(created=mon_7am)
        PollAnswer.objects.create(poll=poll1, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        assert len(data["people"]) == 1
        person_data = data["people"][0]
        assert len(person_data["nodes"]) == 2

        node_map = {n["id"]: n["attending"] for n in person_data["nodes"]}
        assert node_map[str(node1.slug)] is True
        assert node_map[str(node2.slug)] is False

    def test_person_nodes_sorted_attending_first(
        self, client, db, mock_attending_window
    ):
        group1 = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Group 1",
        )
        group2 = Group.objects.create(
            telegram_id=-1001234567891,
            display_name="Group 2",
        )
        group3 = Group.objects.create(
            telegram_id=-1001234567892,
            display_name="Group 3",
        )
        node1 = Node.objects.create(name="Node 1", group=group1)
        node2 = Node.objects.create(name="Node 2", group=group2)
        node3 = Node.objects.create(name="Node 3", group=group3)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            privacy=False,
        )
        GroupPerson.objects.create(group=group1, person=person, left=False)
        GroupPerson.objects.create(group=group2, person=person, left=False)
        GroupPerson.objects.create(group=group3, person=person, left=False)

        poll2 = Poll.objects.create(
            telegram_id="poll2",
            node=node2,
            question="Coming this week?",
        )
        mon_7am = mock_attending_window.replace(day=5, hour=7, minute=0)
        Poll.objects.filter(pk=poll2.pk).update(created=mon_7am)
        PollAnswer.objects.create(poll=poll2, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        nodes = person_data["nodes"]

        assert nodes[0]["attending"] is True
        assert nodes[0]["id"] == str(node2.slug)

    def test_person_display_name_xss_sanitized(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="<script>alert('xss')</script>",
            privacy=False,
        )
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert "<script>" not in person_data["display_name"]
        assert "&lt;script&gt;" in person_data["display_name"]

    def test_person_username_x_xss_sanitized(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            username_x="<img src=x onerror=alert('xss')>",
            privacy=False,
        )
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert "<img" not in person_data["username_x"]
        assert "&lt;img" in person_data["username_x"]

    def test_person_bio_xss_sanitized(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            bio="<a href=\"javascript:alert('xss')\">Click me</a>",
            privacy=False,
        )
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert "<a href" not in person_data["bio"]
        assert "&lt;a href" in person_data["bio"]

    def test_person_quotes_escaped_in_output(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name='Test" onmouseover="alert(1)',
            privacy=False,
        )
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming?",
        )
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["people"][0]
        assert '"' not in person_data["display_name"]
        assert "&quot;" in person_data["display_name"]

    def test_node_attending_count_includes_privacy_mode_on(
        self, client, db, mock_attending_window
    ):
        Node.objects.all().delete()
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        public_person = Person.objects.create(
            telegram_id=1,
            first_name="Public",
            privacy=False,
        )
        private_person = Person.objects.create(
            telegram_id=2,
            first_name="Private",
            privacy=True,
        )

        GroupPerson.objects.create(
            group=group, person=public_person, left=False
        )
        GroupPerson.objects.create(
            group=group, person=private_person, left=False
        )

        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming this week?",
        )
        mon_7am = mock_attending_window.replace(day=5, hour=7, minute=0)
        Poll.objects.filter(pk=poll.pk).update(created=mon_7am)
        PollAnswer.objects.create(poll=poll, person=public_person, yes=True)
        PollAnswer.objects.create(poll=poll, person=private_person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        node_data = data["nodes"][0]
        assert node_data["attending_count"] == 2

    def test_node_attending_count_zero_with_no_poll(self, client, db):
        Node.objects.all().delete()
        node = Node.objects.create(name="Test Node")

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["nodes"][0]["attending_count"] == 0

    def test_node_attending_count_excludes_old_polls(
        self, client, db, mock_attending_window
    ):
        Node.objects.all().delete()
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        person = Person.objects.create(
            telegram_id=1,
            first_name="Alice",
            privacy=False,
        )
        GroupPerson.objects.create(group=group, person=person, left=False)

        old_date = mock_attending_window - timedelta(days=10)
        poll = Poll.objects.create(
            telegram_id="poll123",
            node=node,
            question="Coming this week?",
        )
        Poll.objects.filter(pk=poll.pk).update(created=old_date)
        PollAnswer.objects.create(poll=poll, person=person, yes=True)

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["nodes"][0]["attending_count"] == 0

    def test_attending_resets_after_friday_7am_utc(self, client, db):
        sat_10am_utc = datetime(2026, 1, 10, 10, 0, 0, tzinfo=dt_timezone.utc)
        with patch("hackabot.apps.bot.views.datetime") as mock_dt:
            mock_dt.now.return_value = sat_10am_utc
            mock_dt.fromtimestamp = datetime.fromtimestamp

            group = Group.objects.create(
                telegram_id=-1001234567890,
                display_name="Test Group",
            )
            node = Node.objects.create(name="Test Node", group=group)

            person = Person.objects.create(
                telegram_id=1,
                first_name="Alice",
                privacy=False,
            )
            GroupPerson.objects.create(group=group, person=person, left=False)

            mon_7am = sat_10am_utc.replace(day=5, hour=7, minute=0)
            poll = Poll.objects.create(
                telegram_id="poll123",
                node=node,
                question="Coming this week?",
            )
            Poll.objects.filter(pk=poll.pk).update(created=mon_7am)
            PollAnswer.objects.create(poll=poll, person=person, yes=True)

            response = client.get("/api/nodes/")

            data = response.json()
            assert data["nodes"][0]["attending_count"] == 0
            person_data = data["people"][0]
            assert person_data["nodes"][0]["attending"] is False

    def test_stats_people_count_includes_all_users_in_node_groups(
        self, client, db
    ):
        Node.objects.all().delete()
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        Node.objects.create(name="Test Node", group=group)

        public_person = Person.objects.create(
            telegram_id=1,
            first_name="Public",
            privacy=False,
        )
        private_person = Person.objects.create(
            telegram_id=2,
            first_name="Private",
            privacy=True,
        )

        GroupPerson.objects.create(
            group=group, person=public_person, left=False
        )
        GroupPerson.objects.create(
            group=group, person=private_person, left=False
        )

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["stats"]["people_count"] == 2

    def test_stats_people_count_excludes_left_members(self, client, db):
        Node.objects.all().delete()
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        Node.objects.create(name="Test Node", group=group)

        active_person = Person.objects.create(
            telegram_id=1,
            first_name="Active",
            privacy=False,
        )
        left_person = Person.objects.create(
            telegram_id=2,
            first_name="Left",
            privacy=False,
        )

        GroupPerson.objects.create(
            group=group, person=active_person, left=False
        )
        GroupPerson.objects.create(group=group, person=left_person, left=True)

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["stats"]["people_count"] == 1

    def test_stats_people_count_excludes_groups_without_nodes(self, client, db):
        Node.objects.all().delete()
        node_group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Node Group",
        )
        orphan_group = Group.objects.create(
            telegram_id=-1001234567891,
            display_name="Orphan Group",
        )
        Node.objects.create(name="Test Node", group=node_group)

        node_person = Person.objects.create(
            telegram_id=1,
            first_name="Node Person",
        )
        orphan_person = Person.objects.create(
            telegram_id=2,
            first_name="Orphan Person",
        )

        GroupPerson.objects.create(
            group=node_group, person=node_person, left=False
        )
        GroupPerson.objects.create(
            group=orphan_group, person=orphan_person, left=False
        )

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["stats"]["people_count"] == 1

    def test_stats_people_count_counts_unique_people_across_nodes(
        self, client, db
    ):
        Node.objects.all().delete()
        group1 = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Group 1",
        )
        group2 = Group.objects.create(
            telegram_id=-1001234567891,
            display_name="Group 2",
        )
        Node.objects.create(name="Node 1", group=group1)
        Node.objects.create(name="Node 2", group=group2)

        shared_person = Person.objects.create(
            telegram_id=1,
            first_name="Shared",
        )
        person1 = Person.objects.create(
            telegram_id=2,
            first_name="Person 1",
        )
        person2 = Person.objects.create(
            telegram_id=3,
            first_name="Person 2",
        )

        GroupPerson.objects.create(
            group=group1, person=shared_person, left=False
        )
        GroupPerson.objects.create(
            group=group2, person=shared_person, left=False
        )
        GroupPerson.objects.create(group=group1, person=person1, left=False)
        GroupPerson.objects.create(group=group2, person=person2, left=False)

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["stats"]["people_count"] == 3