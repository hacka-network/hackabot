"""
To get the group's chat ID: https://api.telegram.org/BOT-TOKEN/getUpdates
"""

import os

import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_URL = os.environ.get("TELEGRAM_WEBHOOK_URL", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_API_BASE = "https://api.telegram.org"

ALLOWED_UPDATES = ["message", "poll", "poll_answer", "chat_member"]


def verify_webhook_secret(request):
    header_value = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return header_value == TELEGRAM_WEBHOOK_SECRET


def _get_bot_token():
    token = TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment")
    if not token.startswith("bot"):
        token = f"bot{token}"
    return token


def verify_webhook():
    if not TELEGRAM_WEBHOOK_URL:
        print("[hackabot] TELEGRAM_WEBHOOK_URL not set, skipping webhook setup")
        return False

    token = _get_bot_token()

    # Get current webhook info
    resp = requests.get(f"{TELEGRAM_API_BASE}/{token}/getWebhookInfo")
    resp.raise_for_status()
    info = resp.json()
    current_url = info.get("result", {}).get("url", "")

    if current_url == TELEGRAM_WEBHOOK_URL:
        print(f"[hackabot] Webhook already set to: {TELEGRAM_WEBHOOK_URL}")
        return True

    # Set the webhook
    print(f"[hackabot] Setting webhook to: {TELEGRAM_WEBHOOK_URL}")
    payload = dict(
        url=TELEGRAM_WEBHOOK_URL,
        allowed_updates=ALLOWED_UPDATES,
    )
    if TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = TELEGRAM_WEBHOOK_SECRET
    resp = requests.post(
        f"{TELEGRAM_API_BASE}/{token}/setWebhook",
        json=payload,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("ok"):
        print("[hackabot] Webhook set successfully")
        return True
    else:
        print(f"[hackabot] Failed to set webhook: {result}")
        return False


def send(chat_id, text):
    print(f"[hackabot] [{chat_id}] SEND: {text}")
    token = _get_bot_token()
    url = f"{TELEGRAM_API_BASE}/{token}/sendMessage"
    resp = requests.post(
        url,
        json=dict(
            chat_id=chat_id,
            parse_mode="Markdown",
            text=text,
            disable_web_page_preview=True,
        ),
    )
    print(f"[hackabot] response: {resp.text}")
    resp.raise_for_status()


def send_poll(node, when="Thursday"):
    from .models import Poll

    chat_id = node.group.telegram_id
    name = f"{node.emoji} {node.name}" if node.emoji else node.name

    token = _get_bot_token()
    resp = requests.post(
        f"{TELEGRAM_API_BASE}/{token}/sendPoll",
        json=dict(
            chat_id=chat_id,
            question=f"Who's coming to {name} this {when}?",
            options=["✅  Yes", "👎  Not this week"],
            is_anonymous=False,
            allows_multiple_answers=False,
        ),
    )
    print(f"[hackabot-poll] response: {resp.text}")
    resp.raise_for_status()
    obj = resp.json()
    result = obj["result"]
    message_id = result["message_id"]
    poll_data = result.get("poll", {})

    # Save the poll to DB
    if poll_data:
        Poll.objects.update_or_create(
            telegram_id=poll_data["id"],
            defaults=dict(
                node=node,
                question=poll_data.get("question", ""),
                yes_count=0,
                no_count=0,
            ),
        )

    # Send an invite to the global group
    hacka_network_global_invite_url = "https://t.me/+XTK6oIHCVZFkNmY1"
    send(
        chat_id,
        "...you can also join the [Hacka* global chat 🌏💻🤓]"
        f"({hacka_network_global_invite_url})",
    )

    # Try to pin it
    try:
        print("[hackabot] trying to pin it")
        resp = requests.post(
            f"{TELEGRAM_API_BASE}/{token}/pinChatMessage",
            json=dict(
                chat_id=chat_id,
                message_id=message_id,
                disable_notification=False,
            ),
        )
        print("[hackabot] got: ", resp.json())
    except Exception as e:
        print(f"[hackabot] failed to pin it: {e}")


def send_event_reminder(event):
    node = event.node
    chat_id = node.group.telegram_id
    time_str = event.time.strftime("%-I:%M%p").lower().replace(":00", "")

    if event.type == "intros":
        send(chat_id, f"🔔👋  Reminder! *Intros are at {time_str}*")
    elif event.type == "demos":
        send(chat_id, f"🔔💻  Reminder! *Demos are at {time_str}*")
    elif event.type == "lunch":
        if event.where:
            send(chat_id, f"🔔🍔  *Lunch at {time_str}* in {event.where}")
        else:
            send(chat_id, f"🔔🍔  *Lunch at {time_str}*")
    elif event.type == "drinks":
        if event.where:
            send(chat_id, f"🍺🍻🍷  {event.where} — let's go!")
        else:
            send(chat_id, "🍺🍻🍷  Drinks time — let's go!")
