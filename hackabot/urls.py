from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from hackabot.apps.bot.views import api_nodes, telegram_webhook

urlpatterns = [
    path("", RedirectView.as_view(url="https://hacka.network")),
    path("admin/", admin.site.urls),
    path("webhook/telegram/", telegram_webhook, name="telegram_webhook"),
    path("api/nodes/", api_nodes, name="api_nodes"),
]
