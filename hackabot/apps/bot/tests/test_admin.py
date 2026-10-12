import pytest
from django.urls import reverse

from hackabot.apps.bot.admin import NODES_JSON_NOTE
from hackabot.apps.bot.models import Node


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    storages = dict(settings.STORAGES)
    storages["staticfiles"] = dict(
        BACKEND="django.contrib.staticfiles.storage.StaticFilesStorage"
    )
    settings.STORAGES = storages


@pytest.fixture
def listed_node(db):
    return Node.objects.create(
        name="Hackalona",
        emoji="🇪🇸",
        established=2026,
        location="Barcelona",
        timezone="Europe/Madrid",
        signup_url="https://hackalona.com",
        lat=41.3874,
        lon=2.1686,
    )


def change_form_data(node, **overrides):
    data = {
        "name": node.name,
        "emoji": node.emoji,
        "established": node.established,
        "location": node.location,
        "timezone": node.timezone,
        "signup_url": node.signup_url,
        "lat": node.lat,
        "lon": node.lon,
        "event_day": node.event_day,
        "group": "",
        "last_poll_sent_at_0": "",
        "last_poll_sent_at_1": "",
        "event_set-TOTAL_FORMS": "0",
        "event_set-INITIAL_FORMS": "0",
        "event_set-MIN_NUM_FORMS": "0",
        "event_set-MAX_NUM_FORMS": "1000",
    }
    data.update(overrides)
    return data


class TestNodeAdmin:
    def test_changelist_shows_nodes_json_note(self, admin_client, listed_node):
        response = admin_client.get(reverse("admin:bot_node_changelist"))
        assert response.status_code == 200
        notes = [str(m) for m in response.context["messages"]]
        assert notes == [NODES_JSON_NOTE]

    def test_changelist_unlisted_not_editable(self, admin_client, listed_node):
        response = admin_client.get(reverse("admin:bot_node_changelist"))
        content = response.content.decode()
        assert 'name="form-0-disabled"' in content
        assert 'name="form-0-unlisted"' not in content

    def test_change_form_shows_nodes_json_note(
        self, admin_client, listed_node
    ):
        url = reverse("admin:bot_node_change", args=[listed_node.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        notes = [str(m) for m in response.context["messages"]]
        assert notes == [NODES_JSON_NOTE]

    def test_change_form_unlisted_not_editable(
        self, admin_client, listed_node
    ):
        url = reverse("admin:bot_node_change", args=[listed_node.pk])
        content = admin_client.get(url).content.decode()
        assert 'name="disabled"' in content
        assert 'name="unlisted"' not in content

    def test_add_form_shows_nodes_json_note(self, admin_client):
        response = admin_client.get(reverse("admin:bot_node_add"))
        assert response.status_code == 200
        notes = [str(m) for m in response.context["messages"]]
        assert notes == [NODES_JSON_NOTE]

    def test_posted_unlisted_is_ignored(self, admin_client, listed_node):
        url = reverse("admin:bot_node_change", args=[listed_node.pk])
        data = change_form_data(listed_node, disabled="on", unlisted="on")
        response = admin_client.post(url, data)
        assert response.status_code == 302
        listed_node.refresh_from_db()
        assert listed_node.disabled is True
        assert listed_node.unlisted is False
