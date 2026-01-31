from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from hackabot.apps.bot.views import (
    api_nodes,
    api_photo_image,
    api_recent_photos,
    telegram_webhook,
)

urlpatterns = [
    path("", RedirectView.as_view(url="https://hacka.network")),
    path("admin/", admin.site.urls),
    path("webhook/telegram/", telegram_webhook, name="telegram_webhook"),
    path("api/nodes/", api_nodes, name="api_nodes"),
    path("api/photos/", api_recent_photos, name="api_recent_photos"),
    path(
        "api/photos/<int:photo_id>/image",
        api_photo_image,
        name="api_photo_image",
    ),
]
