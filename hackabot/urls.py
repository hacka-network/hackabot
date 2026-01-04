from django.contrib import admin
from django.urls import path

from hackabot.apps.bot.views import telegram_webhook

urlpatterns = [
    path("admin/", admin.site.urls),
    path("webhook/telegram/", telegram_webhook, name="telegram_webhook"),
]
