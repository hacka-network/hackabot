import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "testtoken123")
os.environ.setdefault(
    "TELEGRAM_WEBHOOK_URL", "https://test.example.com/webhook/"
)

pytest_plugins = ["hackabot.apps.bot.tests.conftest"]
