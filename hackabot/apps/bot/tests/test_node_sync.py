import hashlib
import hmac
import json
from datetime import time
from unittest.mock import MagicMock, patch

import pytest
import responses
from django.core.management import call_command
from django.utils import timezone

from hackabot.apps.bot.models import Event, Node
from hackabot.apps.bot import node_sync
from hackabot.apps.bot.node_sync import (
    is_flag_emoji,
    reconcile_nodes,
    sync_nodes_from_url,
    validate_entry,
    valid_timezone,
)

SCOTLAND_FLAG = (
    "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"
)


def make_entry(**overrides):
    entry = dict(
        name="Hackalona",
        emoji="🇪🇸",
        established=2026,
        location="Barcelona",
        timezone="Europe/Madrid",
        signup_url="https://hackalona.com",
    )
    entry.update(overrides)
    return entry


@pytest.fixture(autouse=True)
def _clear_seeded_nodes(db):
    Node.objects.all().delete()


class TestIsFlagEmoji:
    def test_country_flags_pass(self):
        for flag in ["🇮🇩", "🇹🇭", "🇬🇧", "🇺🇸", "🇪🇸"]:
            assert is_flag_emoji(flag)

    def test_subdivision_flag_passes(self):
        assert is_flag_emoji(SCOTLAND_FLAG)

    def test_non_flags_rejected(self):
        for value in ["🌴", "🦎", "🏁", "🚩", ""]:
            assert not is_flag_emoji(value)

    def test_two_flags_rejected(self):
        assert not is_flag_emoji("🇪🇸🇫🇷")

    def test_ascii_rejected(self):
        assert not is_flag_emoji("AB")

    def test_lone_regional_indicator_rejected(self):
        assert not is_flag_emoji("🇪")


class TestValidTimezone:
    def test_valid(self):
        assert valid_timezone("Europe/Madrid")
        assert valid_timezone("UTC")

    def test_invalid(self):
        assert not valid_timezone("Mars/Olympus")
        assert not valid_timezone("not a zone")


class TestValidateEntry:
    def test_valid_entry(self):
        name, cleaned, error = validate_entry(make_entry())
        assert error is None
        assert name == "Hackalona"
        assert cleaned["name"] == "Hackalona"
        assert cleaned["established"] == 2026

    def test_name_is_stripped(self):
        name, cleaned, error = validate_entry(make_entry(name="  Hacka  "))
        assert error is None
        assert name == "Hacka"
        assert cleaned["name"] == "Hacka"

    def test_non_dict_entry(self):
        name, cleaned, error = validate_entry("nope")
        assert name is None
        assert cleaned is None
        assert error

    def test_missing_field(self):
        entry = make_entry()
        del entry["location"]
        name, cleaned, error = validate_entry(entry)
        assert cleaned is None
        assert "missing" in error
        assert name == "Hackalona"

    def test_unknown_key_rejected(self):
        name, cleaned, error = validate_entry(
            make_entry(signupurl="https://typo.com")
        )
        assert cleaned is None
        assert "unknown" in error

    def test_bad_emoji(self):
        for bad in ["🌴", "🇪🇸🇫🇷", "X", ""]:
            name, cleaned, error = validate_entry(make_entry(emoji=bad))
            assert cleaned is None
            assert "flag" in error

    def test_established_not_int(self):
        name, cleaned, error = validate_entry(make_entry(established="2026"))
        assert cleaned is None

    def test_established_bool_rejected(self):
        name, cleaned, error = validate_entry(make_entry(established=True))
        assert cleaned is None

    def test_established_out_of_range(self):
        name, cleaned, error = validate_entry(make_entry(established=1800))
        assert cleaned is None
        future = timezone.now().year + 5
        name, cleaned, error = validate_entry(make_entry(established=future))
        assert cleaned is None

    def test_bad_timezone(self):
        name, cleaned, error = validate_entry(
            make_entry(timezone="Nowhere/Nope")
        )
        assert cleaned is None
        assert "timezone" in error

    def test_bad_signup_url(self):
        bad_urls = [
            "http://insecure.com",
            "ftp://x",
            "hackalona.com",
            "https://",
            "https:///path",
        ]
        for bad in bad_urls:
            name, cleaned, error = validate_entry(make_entry(signup_url=bad))
            assert cleaned is None
            assert "https" in error

    def test_empty_name(self):
        name, cleaned, error = validate_entry(make_entry(name="   "))
        assert name is None
        assert cleaned is None


class TestReconcile:
    def test_creates_new_node(self, db):
        summary = reconcile_nodes(dict(nodes=[make_entry()]))
        assert summary["aborted"] is False
        assert summary["created"] == 1
        node = Node.objects.get(name="Hackalona")
        assert node.emoji == "🇪🇸"
        assert node.location == "Barcelona"
        assert node.timezone == "Europe/Madrid"
        assert node.group_id is None
        assert node.event_day == 3
        assert node.unlisted is False
        assert node.disabled is False

    def test_updates_existing_node_json_wins(self, db):
        Node.objects.create(
            name="Hackalona",
            emoji="🇪🇸",
            established=2025,
            location="Old City",
            timezone="UTC",
            signup_url="https://old.com",
        )
        summary = reconcile_nodes(
            dict(nodes=[make_entry(location="Barcelona")])
        )
        assert summary["updated"] == 1
        node = Node.objects.get(name="Hackalona")
        assert node.location == "Barcelona"
        assert node.established == 2026

    def test_unchanged_when_identical(self, db):
        reconcile_nodes(dict(nodes=[make_entry()]))
        summary = reconcile_nodes(dict(nodes=[make_entry()]))
        assert summary["unchanged"] == 1
        assert summary["updated"] == 0
        assert summary["created"] == 0

    def test_missing_node_disabled_not_deleted(self, db):
        keep = Node.objects.create(name="Keeper")
        gone = Node.objects.create(name="Gone")
        event = Event.objects.create(node=gone, type="intros", time=time(9, 0))
        summary = reconcile_nodes(
            dict(nodes=[make_entry(name="Keeper", emoji="🇪🇸")])
        )
        assert summary["disabled"] == 1
        gone.refresh_from_db()
        assert gone.unlisted is True
        assert gone.disabled is True
        assert Node.objects.filter(pk=gone.pk).exists()
        assert Event.objects.filter(pk=event.pk).exists()
        keep.refresh_from_db()
        assert keep.unlisted is False

    def test_revives_previously_removed_node(self, db):
        Node.objects.create(
            name="Hackalona",
            emoji="🇪🇸",
            established=2026,
            location="Barcelona",
            timezone="Europe/Madrid",
            signup_url="https://hackalona.com",
            unlisted=True,
            disabled=True,
        )
        summary = reconcile_nodes(dict(nodes=[make_entry()]))
        assert summary["updated"] == 1
        node = Node.objects.get(name="Hackalona")
        assert node.unlisted is False
        assert node.disabled is False

    def test_listed_disabled_node_left_paused(self, db):
        Node.objects.create(
            name="Hackalona",
            emoji="🇪🇸",
            established=2026,
            location="Barcelona",
            timezone="Europe/Madrid",
            signup_url="https://hackalona.com",
            unlisted=False,
            disabled=True,
        )
        summary = reconcile_nodes(dict(nodes=[make_entry()]))
        assert summary["unchanged"] == 1
        node = Node.objects.get(name="Hackalona")
        assert node.unlisted is False
        assert node.disabled is True

    def test_malformed_entry_does_not_disable_live_node(self, db, monkeypatch):
        captured = []
        monkeypatch.setattr(
            node_sync.sentry_sdk,
            "capture_message",
            lambda msg, **kw: captured.append(msg),
        )
        live = Node.objects.create(name="Hackalona")
        data = dict(
            nodes=[
                make_entry(name="Hacktest"),
                make_entry(name="Hackalona", signup_url="http://bad"),
            ]
        )
        summary = reconcile_nodes(data)
        assert summary["created"] == 1
        assert summary["skipped"] == 1
        live.refresh_from_db()
        assert live.unlisted is False
        assert live.disabled is False
        assert captured

    def test_abort_on_bad_shape(self, db):
        Node.objects.create(name="Keeper")
        summary = reconcile_nodes(["not", "a", "dict"])
        assert summary == dict(aborted=True, reason="bad_shape")
        assert Node.objects.get(name="Keeper").unlisted is False

    def test_abort_on_no_valid_nodes(self, db):
        summary = reconcile_nodes(dict(nodes=[make_entry(emoji="🌴")]))
        assert summary["aborted"] is True
        assert summary["reason"] == "no_valid_nodes"

    def test_abort_on_suspicious_drop(self, db):
        for i in range(5):
            Node.objects.create(name=f"Node{i}")
        summary = reconcile_nodes(
            dict(nodes=[make_entry(name="Node0", emoji="🇪🇸")])
        )
        assert summary["aborted"] is True
        assert summary["reason"] == "suspicious_drop"
        for i in range(5):
            assert Node.objects.get(name=f"Node{i}").disabled is False

    def test_small_network_not_guarded(self, db):
        Node.objects.create(name="Node0")
        Node.objects.create(name="Node1")
        summary = reconcile_nodes(
            dict(nodes=[make_entry(name="Node0", emoji="🇪🇸")])
        )
        assert summary["aborted"] is False
        assert Node.objects.get(name="Node1").disabled is True

    def test_duplicate_name_skipped(self, db):
        data = dict(nodes=[make_entry(), make_entry(location="Dup")])
        summary = reconcile_nodes(data)
        assert summary["created"] == 1
        assert summary["skipped"] == 1
        assert Node.objects.get(name="Hackalona").location == "Barcelona"

    def test_takes_advisory_lock_on_postgres(self, db):
        fake_conn = MagicMock()
        fake_conn.vendor = "postgresql"
        cursor = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = cursor
        with patch("hackabot.apps.bot.node_sync.connection", fake_conn):
            summary = reconcile_nodes(dict(nodes=[make_entry()]))
        assert summary["aborted"] is False
        assert cursor.execute.called
        assert "pg_advisory_xact_lock" in cursor.execute.call_args[0][0]


class TestSyncFromUrl:
    URL = "https://example.com/nodes.json"

    def test_success(self, db):
        with responses.RequestsMock() as rsps:
            rsps.add(
                rsps.GET,
                self.URL,
                json=dict(nodes=[make_entry()]),
                status=200,
            )
            summary = sync_nodes_from_url(self.URL)
        assert summary["aborted"] is False
        assert summary["created"] == 1

    def test_fetch_failure(self, db):
        with responses.RequestsMock() as rsps:
            rsps.add(rsps.GET, self.URL, status=500)
            summary = sync_nodes_from_url(self.URL)
        assert summary == dict(aborted=True, reason="fetch_failed")

    def test_parse_failure(self, db):
        with responses.RequestsMock() as rsps:
            rsps.add(
                rsps.GET,
                self.URL,
                body="<html>not json</html>",
                status=200,
                content_type="application/json",
            )
            summary = sync_nodes_from_url(self.URL)
        assert summary == dict(aborted=True, reason="parse_failed")


def sign(body, secret):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestGithubWebhook:
    SECRET = "s3cret"

    def post(self, client, body, event="push", signature=None):
        if signature is None:
            signature = sign(body, self.SECRET)
        return client.post(
            "/webhook/github/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
            HTTP_X_GITHUB_EVENT=event,
        )

    @patch("hackabot.apps.bot.views.sync_nodes_from_url")
    def test_push_to_main_triggers_sync(self, mock_sync, client, settings):
        settings.GITHUB_WEBHOOK_SECRET = self.SECRET
        mock_sync.return_value = dict(aborted=False, created=1)
        body = json.dumps(dict(ref="refs/heads/main")).encode()
        resp = self.post(client, body)
        assert resp.status_code == 200
        mock_sync.assert_called_once()

    @patch("hackabot.apps.bot.views.sync_nodes_from_url")
    def test_bad_signature_rejected(self, mock_sync, client, settings):
        settings.GITHUB_WEBHOOK_SECRET = self.SECRET
        body = json.dumps(dict(ref="refs/heads/main")).encode()
        resp = self.post(client, body, signature="sha256=deadbeef")
        assert resp.status_code == 403
        mock_sync.assert_not_called()

    @patch("hackabot.apps.bot.views.sync_nodes_from_url")
    def test_no_secret_rejected(self, mock_sync, client, settings):
        settings.GITHUB_WEBHOOK_SECRET = ""
        body = json.dumps(dict(ref="refs/heads/main")).encode()
        resp = self.post(client, body, signature="sha256=whatever")
        assert resp.status_code == 403
        mock_sync.assert_not_called()

    @patch("hackabot.apps.bot.views.sync_nodes_from_url")
    def test_ping_event_ok(self, mock_sync, client, settings):
        settings.GITHUB_WEBHOOK_SECRET = self.SECRET
        body = json.dumps(dict(zen="hi")).encode()
        resp = self.post(client, body, event="ping")
        assert resp.status_code == 200
        mock_sync.assert_not_called()

    @patch("hackabot.apps.bot.views.sync_nodes_from_url")
    def test_non_push_skipped(self, mock_sync, client, settings):
        settings.GITHUB_WEBHOOK_SECRET = self.SECRET
        body = json.dumps(dict(action="opened")).encode()
        resp = self.post(client, body, event="issues")
        assert resp.status_code == 200
        mock_sync.assert_not_called()

    @patch("hackabot.apps.bot.views.sync_nodes_from_url")
    def test_non_main_ref_skipped(self, mock_sync, client, settings):
        settings.GITHUB_WEBHOOK_SECRET = self.SECRET
        body = json.dumps(dict(ref="refs/heads/dev")).encode()
        resp = self.post(client, body)
        assert resp.status_code == 200
        mock_sync.assert_not_called()


class TestManagementCommand:
    def test_sync_from_file(self, db, tmp_path):
        path = tmp_path / "nodes.json"
        path.write_text(
            json.dumps(dict(nodes=[make_entry()])), encoding="utf-8"
        )
        call_command("sync_nodes", file=str(path))
        assert Node.objects.filter(name="Hackalona").exists()
