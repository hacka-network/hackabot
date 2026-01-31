import io
import json

import pytest
from django.test import Client
from PIL import Image

from hackabot.apps.bot.images import process_image
from hackabot.apps.bot.models import Group, MeetupPhoto, Node, Person

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


@pytest.fixture
def test_group(db):
    return Group.objects.create(
        telegram_id=-5117513714,
        display_name="Hackatestville Group",
    )


@pytest.fixture
def test_node(db, test_group):
    return Node.objects.create(
        group=test_group,
        name="Hackatestville",
        emoji="🧪",
        location="Test City",
        timezone="UTC",
    )


@pytest.fixture
def test_person(db):
    return Person.objects.create(
        telegram_id=12345,
        first_name="Alice",
        username="alice123",
    )


def create_test_image(width=800, height=600, format="JPEG"):
    img = Image.new("RGB", (width, height), color="blue")
    output = io.BytesIO()
    img.save(output, format=format)
    output.seek(0)
    return output.read()


def post_webhook(client, data):
    return client.post(
        "/webhook/telegram/",
        data=json.dumps(data),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TEST_WEBHOOK_SECRET,
    )


class TestImageProcessing:
    def test_process_valid_jpeg(self):
        image_bytes = create_test_image(800, 600, "JPEG")
        result = process_image(image_bytes)

        assert result is not None
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_process_png_converts_to_jpeg(self):
        image_bytes = create_test_image(800, 600, "PNG")
        result = process_image(image_bytes)

        assert result is not None
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"

    def test_process_large_image_resizes(self):
        image_bytes = create_test_image(2000, 1500, "JPEG")
        result = process_image(image_bytes)

        assert result is not None
        img = Image.open(io.BytesIO(result))
        assert img.width <= 1200
        assert img.height <= 1200

    def test_process_image_too_large_returns_none(self):
        large_bytes = b"x" * (10 * 1024 * 1024 + 1)
        result = process_image(large_bytes)
        assert result is None

    def test_process_invalid_image_returns_none(self):
        result = process_image(b"not an image")
        assert result is None

    def test_process_rgba_converts_to_rgb(self):
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        output = io.BytesIO()
        img.save(output, format="PNG")
        output.seek(0)

        result = process_image(output.read())
        assert result is not None
        processed = Image.open(io.BytesIO(result))
        assert processed.mode == "RGB"


class TestPhotoUploadWebhook:
    def test_photo_upload_with_valid_hashtag(
        self, client, db, test_node, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send_chat_action",
            lambda chat_id, action: None,
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.download_file",
            lambda file_id: create_test_image(),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067200,
                    "photo": [
                        {"file_id": "small123", "width": 320, "height": 240},
                        {"file_id": "large456", "width": 1280, "height": 960},
                    ],
                    "caption": "#hackatestville meetup today!",
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 1
        photo = MeetupPhoto.objects.first()
        assert photo.node == test_node
        assert photo.telegram_file_id == "large456"
        assert len(sent_messages) == 1
        assert "Thanks!" in sent_messages[0][1]
        assert "hacka.network" in sent_messages[0][1]

    def test_photo_upload_wrong_group_ignored(
        self, client, db, test_node, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -999999999, "type": "supergroup"},
                    "date": 1704067200,
                    "photo": [{"file_id": "test123", "width": 640, "height": 480}],
                    "caption": "#hackatestville",
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 0

    def test_photo_upload_no_caption_ignored(
        self, client, db, test_node, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067200,
                    "photo": [{"file_id": "test123", "width": 640, "height": 480}],
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 0

    def test_photo_upload_unknown_hashtag_ignored(
        self, client, db, test_node, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067200,
                    "photo": [{"file_id": "test123", "width": 640, "height": 480}],
                    "caption": "#hackaunknown",
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 0

    def test_duplicate_photo_ignored(
        self, client, db, test_node, monkeypatch
    ):
        MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="existing123",
            image_data=b"test",
        )

        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 12345, "first_name": "Alice"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067200,
                    "photo": [
                        {"file_id": "existing123", "width": 640, "height": 480}
                    ],
                    "caption": "#hackatestville",
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 1

    def test_hashtag_reply_uploads_photo(
        self, client, db, test_node, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send_chat_action",
            lambda chat_id, action: None,
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.download_file",
            lambda file_id: create_test_image(),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1002,
                "message": {
                    "message_id": 2,
                    "from": {"id": 67890, "first_name": "Bob"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067300,
                    "text": "#hackatestville",
                    "reply_to_message": {
                        "message_id": 1,
                        "from": {"id": 12345, "first_name": "Alice"},
                        "chat": {"id": -5117513714, "type": "supergroup"},
                        "date": 1704067200,
                        "photo": [
                            {"file_id": "small123", "width": 320, "height": 240},
                            {"file_id": "reply456", "width": 1280, "height": 960},
                        ],
                    },
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 1
        photo = MeetupPhoto.objects.first()
        assert photo.node == test_node
        assert photo.telegram_file_id == "reply456"
        assert len(sent_messages) == 1
        assert "Thanks!" in sent_messages[0][1]

    def test_hashtag_reply_ignores_non_photo_message(
        self, client, db, test_node, monkeypatch
    ):
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1002,
                "message": {
                    "message_id": 2,
                    "from": {"id": 67890, "first_name": "Bob"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067300,
                    "text": "#hackatestville",
                    "reply_to_message": {
                        "message_id": 1,
                        "from": {"id": 12345, "first_name": "Alice"},
                        "chat": {"id": -5117513714, "type": "supergroup"},
                        "date": 1704067200,
                        "text": "Just a text message",
                    },
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 0

    def test_hashtag_reply_ignores_already_uploaded_photo(
        self, client, db, test_node, monkeypatch
    ):
        MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="already123",
            image_data=b"test",
        )

        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1002,
                "message": {
                    "message_id": 2,
                    "from": {"id": 67890, "first_name": "Bob"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067300,
                    "text": "#hackatestville",
                    "reply_to_message": {
                        "message_id": 1,
                        "from": {"id": 12345, "first_name": "Alice"},
                        "chat": {"id": -5117513714, "type": "supergroup"},
                        "date": 1704067200,
                        "photo": [
                            {"file_id": "already123", "width": 640, "height": 480},
                        ],
                    },
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 1


class TestDeletePhotoCommand:
    def test_admin_can_delete_photo(
        self, client, db, test_node, monkeypatch
    ):
        photo = MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="photo123",
            image_data=b"test",
        )

        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.is_chat_admin",
            lambda chat_id, user_id: True,
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 2,
                    "from": {"id": 12345, "first_name": "Admin"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "delete",
                    "reply_to_message": {
                        "message_id": 1,
                        "photo": [
                            {"file_id": "photo123", "width": 640, "height": 480}
                        ],
                    },
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 0
        assert len(sent_messages) == 1
        assert "Removed" in sent_messages[0][1]

    def test_non_admin_cannot_delete_photo(
        self, client, db, test_node, monkeypatch
    ):
        photo = MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="photo123",
            image_data=b"test",
        )

        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.is_chat_admin",
            lambda chat_id, user_id: False,
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 2,
                    "from": {"id": 12345, "first_name": "User"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "delete",
                    "reply_to_message": {
                        "message_id": 1,
                        "photo": [
                            {"file_id": "photo123", "width": 640, "height": 480}
                        ],
                    },
                },
            },
        )

        assert response.status_code == 200
        assert MeetupPhoto.objects.count() == 1
        assert "Only group admins" in sent_messages[0][1]

    def test_delete_nonexistent_photo(
        self, client, db, test_node, monkeypatch
    ):
        sent_messages = []
        monkeypatch.setattr(
            "hackabot.apps.bot.views.send",
            lambda chat_id, text: sent_messages.append((chat_id, text)),
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.is_chat_admin",
            lambda chat_id, user_id: True,
        )
        monkeypatch.setattr(
            "hackabot.apps.bot.views.PHOTO_UPLOAD_CHAT_ID",
            -5117513714,
        )

        response = post_webhook(
            client,
            {
                "update_id": 1001,
                "message": {
                    "message_id": 2,
                    "from": {"id": 12345, "first_name": "Admin"},
                    "chat": {"id": -5117513714, "type": "supergroup"},
                    "date": 1704067200,
                    "text": "delete",
                    "reply_to_message": {
                        "message_id": 1,
                        "photo": [
                            {"file_id": "notexist", "width": 640, "height": 480}
                        ],
                    },
                },
            },
        )

        assert response.status_code == 200
        assert "isn't on the website" in sent_messages[0][1]


class TestPhotoAPI:
    def test_api_recent_photos_empty(self, client, db):
        response = client.get("/api/photos/")

        assert response.status_code == 200
        data = response.json()
        assert data["photos"] == []

    def test_api_recent_photos_returns_photos(self, client, db, test_node):
        MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="photo1",
            image_data=b"test1",
        )
        MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="photo2",
            image_data=b"test2",
        )

        response = client.get("/api/photos/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["photos"]) == 2
        assert data["photos"][0]["node_name"] == "Hackatestville"
        assert data["photos"][0]["node_emoji"] == "🧪"

    def test_api_recent_photos_limits_to_12(self, client, db, test_node):
        for i in range(20):
            MeetupPhoto.objects.create(
                node=test_node,
                telegram_file_id=f"photo{i}",
                image_data=b"test",
            )

        response = client.get("/api/photos/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["photos"]) == 12

    def test_api_recent_photos_cors_headers(self, client, db):
        response = client.get("/api/photos/")

        assert response["Access-Control-Allow-Origin"] == "*"

    def test_api_recent_photos_options(self, client, db):
        response = client.options("/api/photos/")

        assert response.status_code == 200
        assert response["Access-Control-Allow-Origin"] == "*"

    def test_api_photo_image_returns_jpeg(self, client, db, test_node):
        image_data = create_test_image()
        photo = MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="photo1",
            image_data=image_data,
        )

        response = client.get(f"/api/photos/{photo.id}/image")

        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"
        assert response["Cache-Control"] == "public, max-age=86400"

    def test_api_photo_image_not_found(self, client, db):
        response = client.get("/api/photos/99999/image")

        assert response.status_code == 404


class TestMeetupPhotoModel:
    def test_create_meetup_photo(self, db, test_node, test_person):
        photo = MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="test123",
            image_data=b"test data",
            uploaded_by=test_person,
        )

        assert photo.id is not None
        assert photo.node == test_node
        assert photo.telegram_file_id == "test123"
        assert photo.image_data == b"test data"
        assert photo.uploaded_by == test_person
        assert photo.created is not None

    def test_meetup_photo_str(self, db, test_node):
        photo = MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="test123",
            image_data=b"test",
        )

        assert "Hackatestville" in str(photo)

    def test_meetup_photo_to_dict(self, db, test_node, test_person):
        photo = MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="test123",
            image_data=b"test",
            uploaded_by=test_person,
        )

        data = photo.to_dict()
        assert data["id"] == photo.id
        assert data["node_id"] == test_node.id
        assert data["telegram_file_id"] == "test123"
        assert data["uploaded_by_id"] == test_person.id

    def test_meetup_photo_ordering(self, db, test_node):
        photo1 = MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="old",
            image_data=b"test",
        )
        photo2 = MeetupPhoto.objects.create(
            node=test_node,
            telegram_file_id="new",
            image_data=b"test",
        )

        photos = list(MeetupPhoto.objects.all())
        assert photos[0] == photo2
        assert photos[1] == photo1


class TestHelperFunctions:
    def test_find_node_from_hashtags(self, db, test_node):
        from hackabot.apps.bot.views import _find_node_from_hashtags

        assert _find_node_from_hashtags("#Hackatestville") == test_node
        assert _find_node_from_hashtags("#hackatestville") == test_node
        assert _find_node_from_hashtags("#HACKATESTVILLE") == test_node
        assert _find_node_from_hashtags("Check out #Hackatestville!") == test_node
        assert _find_node_from_hashtags("#unknown") is None
        assert _find_node_from_hashtags("No hashtag here") is None
        assert _find_node_from_hashtags("") is None
        assert _find_node_from_hashtags(None) is None

    def test_escape_markdown(self):
        from hackabot.apps.bot.views import _escape_markdown

        result = _escape_markdown("Test_Node")
        assert result == "Test\\_Node"

        result = _escape_markdown("Test*Bold*")
        assert result == "Test\\*Bold\\*"


class TestPhotoCleanup:
    def test_cleanup_removes_oldest_photos(self, db, test_node, monkeypatch):
        from hackabot.apps.worker.run import process_photo_cleanup

        monkeypatch.setattr(
            "hackabot.apps.worker.run.MAX_PHOTOS", 3
        )
        monkeypatch.setattr(
            "hackabot.apps.worker.run.should_cleanup_photos",
            lambda now: True,
        )

        for i in range(5):
            MeetupPhoto.objects.create(
                node=test_node,
                telegram_file_id=f"photo{i}",
                image_data=b"test",
            )

        assert MeetupPhoto.objects.count() == 5

        process_photo_cleanup()

        assert MeetupPhoto.objects.count() == 3
        remaining_ids = list(
            MeetupPhoto.objects.order_by("created").values_list(
                "telegram_file_id", flat=True
            )
        )
        assert "photo0" not in remaining_ids
        assert "photo1" not in remaining_ids

    def test_cleanup_does_nothing_under_limit(self, db, test_node, monkeypatch):
        from hackabot.apps.worker.run import process_photo_cleanup

        monkeypatch.setattr(
            "hackabot.apps.worker.run.MAX_PHOTOS", 10
        )
        monkeypatch.setattr(
            "hackabot.apps.worker.run.should_cleanup_photos",
            lambda now: True,
        )

        for i in range(5):
            MeetupPhoto.objects.create(
                node=test_node,
                telegram_file_id=f"photo{i}",
                image_data=b"test",
            )

        process_photo_cleanup()

        assert MeetupPhoto.objects.count() == 5
