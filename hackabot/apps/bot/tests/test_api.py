import pytest
from datetime import date, timedelta
from django.test import Client

from hackabot.apps.bot.models import (
    ActivityDay,
    Group,
    GroupPerson,
    Node,
    Person,
)


@pytest.fixture
def client():
    return Client()


class TestApiNodes:
    def test_empty_nodes_list(self, client, db):
        response = client.get("/api/nodes/")

        assert response.status_code == 200
        assert response.json() == {"nodes": []}

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
        assert node_data["activity_level"] == 0
        assert node_data["people"] == []

    def test_nodes_ordered_by_established(self, client, db):
        Node.objects.create(name="Third", established=2022)
        Node.objects.create(name="First", established=2018)
        Node.objects.create(name="Second", established=2020)

        response = client.get("/api/nodes/")

        data = response.json()
        names = [n["name"] for n in data["nodes"]]
        assert names == ["First", "Second", "Third"]

    def test_nodes_null_established_comes_last(self, client, db):
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

        GroupPerson.objects.create(
            group=group, person=public_person, left=False
        )
        GroupPerson.objects.create(
            group=group, person=private_person, left=False
        )

        response = client.get("/api/nodes/")

        data = response.json()
        people = data["nodes"][0]["people"]
        assert len(people) == 1
        assert people[0]["display_name"] == "Public Person"

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

        GroupPerson.objects.create(
            group=group, person=person_with_name, left=False
        )
        GroupPerson.objects.create(
            group=group, person=person_with_x, left=False
        )
        GroupPerson.objects.create(
            group=group, person=person_with_nothing, left=False
        )

        response = client.get("/api/nodes/")

        data = response.json()
        people = data["nodes"][0]["people"]
        assert len(people) == 2

        display_names = [p["display_name"] for p in people]
        assert "Has Name" in display_names
        assert "" in display_names

    def test_people_excludes_left_members(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

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
        people = data["nodes"][0]["people"]
        assert len(people) == 1
        assert people[0]["display_name"] == "Active"

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
        GroupPerson.objects.create(group=group, person=person, left=False)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["nodes"][0]["people"][0]
        assert person_data["display_name"] == "Alice"
        assert person_data["username_x"] == "alice_x"
        assert person_data["bio"] == "Building cool stuff"

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
        GroupPerson.objects.create(group=group, person=person, left=False)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["nodes"][0]["people"][0]
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
        GroupPerson.objects.create(group=group, person=person, left=False)

        response = client.get("/api/nodes/")

        data = response.json()
        person_data = data["nodes"][0]["people"][0]
        assert person_data["username_x"] is None

    def test_node_without_group_has_empty_people(self, client, db):
        node = Node.objects.create(name="Orphan Node")

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["nodes"][0]["people"] == []

    def test_activity_level_zero_with_no_activity(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["nodes"][0]["activity_level"] == 0

    def test_activity_level_low(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)
        person = Person.objects.create(telegram_id=1, first_name="Alice")

        ActivityDay.objects.create(
            person=person,
            group=group,
            date=date.today(),
            message_count=5,
        )

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["nodes"][0]["activity_level"] == 1

    def test_activity_level_high(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)
        person = Person.objects.create(telegram_id=1, first_name="Alice")

        ActivityDay.objects.create(
            person=person,
            group=group,
            date=date.today(),
            message_count=2000,
        )

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["nodes"][0]["activity_level"] == 10

    def test_activity_level_ignores_old_activity(self, client, db):
        group = Group.objects.create(
            telegram_id=-1001234567890,
            display_name="Test Group",
        )
        node = Node.objects.create(name="Test Node", group=group)
        person = Person.objects.create(telegram_id=1, first_name="Alice")

        old_date = date.today() - timedelta(days=60)
        ActivityDay.objects.create(
            person=person,
            group=group,
            date=old_date,
            message_count=2000,
        )

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["nodes"][0]["activity_level"] == 0

    def test_activity_level_no_group(self, client, db):
        node = Node.objects.create(name="No Group Node")

        response = client.get("/api/nodes/")

        data = response.json()
        assert data["nodes"][0]["activity_level"] == 0
