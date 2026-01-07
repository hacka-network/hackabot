import hmac
import os
import sys

import requests

REQUEST_TIMEOUT = 30

TESTING = "pytest" in sys.modules

if TESTING:
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "test-token")
    TELEGRAM_WEBHOOK_URL = os.environ.get("TELEGRAM_WEBHOOK_URL", "")
    TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
else:
    TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_WEBHOOK_URL = os.environ["TELEGRAM_WEBHOOK_URL"]
    TELEGRAM_WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
TELEGRAM_API_BASE = "https://api.telegram.org"
HACKA_NETWORK_GLOBAL_CHAT_ID = "-1002257954378"

ALLOWED_UPDATES = [
    "message",
    "poll",
    "poll_answer",
    "chat_member",
    "callback_query",
]


def verify_webhook_secret(request):
    header_value = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    is_valid = hmac.compare_digest(header_value, TELEGRAM_WEBHOOK_SECRET)
    if is_valid:
        print("🔐 Webhook secret: valid")
    else:
        print("🔐 Webhook secret: INVALID")
    return is_valid


def _get_bot_token():
    token = TELEGRAM_BOT_TOKEN
    if not token.startswith("bot"):
        token = f"bot{token}"
    return token


def verify_webhook():
    token = _get_bot_token()

    # Get current webhook info
    print("📤 Calling Telegram API: getWebhookInfo")
    resp = requests.get(
        f"{TELEGRAM_API_BASE}/{token}/getWebhookInfo",
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    info = resp.json()
    print(f"📥 getWebhookInfo response: {info}")
    result = info.get("result", {})
    current_url = result.get("url", "")
    current_updates = set(result.get("allowed_updates", []))
    expected_updates = set(ALLOWED_UPDATES)

    if (
        current_url == TELEGRAM_WEBHOOK_URL
        and current_updates == expected_updates
    ):
        print(f"✅ Webhook already set to: {TELEGRAM_WEBHOOK_URL}")
        return True

    # Set the webhook
    print(f"📤 Calling Telegram API: setWebhook -> {TELEGRAM_WEBHOOK_URL}")
    payload = dict(
        url=TELEGRAM_WEBHOOK_URL,
        allowed_updates=ALLOWED_UPDATES,
    )
    if TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = TELEGRAM_WEBHOOK_SECRET
    resp = requests.post(
        f"{TELEGRAM_API_BASE}/{token}/setWebhook",
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"📥 setWebhook response: {result}")
    if result.get("ok"):
        print("✅ Webhook set successfully")
        return True
    else:
        print(f"❌ Failed to set webhook: {result}")
        return False


def send(chat_id, text):
    print(f"📤 Calling Telegram API: sendMessage to chat {chat_id}")
    print(f"📤 Message: {text[:100]}{'...' if len(text) > 100 else ''}")
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
        timeout=REQUEST_TIMEOUT,
    )
    print(f"📥 sendMessage response: {resp.text}")
    resp.raise_for_status()
    print("✅ Message sent successfully")


def send_with_keyboard(chat_id, text, keyboard):
    print(
        f"📤 Calling Telegram API: sendMessage with keyboard to chat {chat_id}"
    )
    print(f"📤 Message: {text[:100]}{'...' if len(text) > 100 else ''}")
    token = _get_bot_token()
    url = f"{TELEGRAM_API_BASE}/{token}/sendMessage"
    resp = requests.post(
        url,
        json=dict(
            chat_id=chat_id,
            parse_mode="Markdown",
            text=text,
            disable_web_page_preview=True,
            reply_markup=dict(inline_keyboard=keyboard),
        ),
        timeout=REQUEST_TIMEOUT,
    )
    print(f"📥 sendMessage response: {resp.text}")
    resp.raise_for_status()
    print("✅ Message with keyboard sent successfully")


def answer_callback_query(callback_query_id, text=None):
    print(f"📤 Calling Telegram API: answerCallbackQuery {callback_query_id}")
    token = _get_bot_token()
    url = f"{TELEGRAM_API_BASE}/{token}/answerCallbackQuery"
    payload = dict(callback_query_id=callback_query_id)
    if text:
        payload["text"] = text
    resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    print(f"📥 answerCallbackQuery response: {resp.text}")
    resp.raise_for_status()
    print("✅ Callback query answered")


def export_chat_invite_link(chat_id):
    print(f"📤 Calling Telegram API: exportChatInviteLink for chat {chat_id}")
    token = _get_bot_token()
    url = f"{TELEGRAM_API_BASE}/{token}/exportChatInviteLink"
    resp = requests.post(
        url,
        json=dict(chat_id=chat_id),
        timeout=REQUEST_TIMEOUT,
    )
    print(f"📥 exportChatInviteLink response: {resp.text}")
    resp.raise_for_status()
    result = resp.json()
    return result.get("result")


def send_poll(node, when="Thursday"):
    from .models import Poll

    chat_id = node.group.telegram_id
    name = f"{node.emoji} {node.name}" if node.emoji else node.name

    print(f"📊 Sending poll to {name} (chat {chat_id})")
    print(f"📤 Calling Telegram API: sendPoll")
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
        timeout=REQUEST_TIMEOUT,
    )
    print(f"📥 sendPoll response: {resp.text}")
    resp.raise_for_status()
    print("✅ Poll sent successfully")
    obj = resp.json()
    result = obj["result"]
    message_id = result["message_id"]
    poll_data = result.get("poll", {})

    # Save the poll to DB
    if poll_data:
        print(f"💾 Saving poll to database: {poll_data['id']}")
        Poll.objects.update_or_create(
            telegram_id=poll_data["id"],
            defaults=dict(
                node=node,
                question=poll_data.get("question", ""),
                yes_count=0,
                no_count=0,
            ),
        )
        print("✅ Poll saved to database")

    # Send an invite to the global group
    print("📤 Sending global chat invite message...")
    hacka_network_global_invite_url = "https://t.me/+XTK6oIHCVZFkNmY1"
    send(
        chat_id,
        "...you can also join the [Hacka* global chat 🌏💻🤓]"
        f"({hacka_network_global_invite_url})",
    )

    # Try to pin it
    try:
        print(
            f"📤 Calling Telegram API: pinChatMessage (message {message_id})"
        )
        resp = requests.post(
            f"{TELEGRAM_API_BASE}/{token}/pinChatMessage",
            json=dict(
                chat_id=chat_id,
                message_id=message_id,
                disable_notification=False,
            ),
            timeout=REQUEST_TIMEOUT,
        )
        print(f"📥 pinChatMessage response: {resp.json()}")
        print("📌 Poll pinned successfully")
    except Exception as e:
        print(f"❌ Failed to pin poll: {e}")


def send_event_reminder(event):
    node = event.node
    chat_id = node.group.telegram_id
    time_str = event.time.strftime("%-I:%M%p").lower().replace(":00", "")

    print(f"🔔 Sending event reminder: {event.type} for {node.name}")

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

    print(f"✅ Event reminder sent for {event.type}")


def send_weekly_attendance_summary():
    from datetime import timedelta

    from django.db.models import Sum
    from django.utils import timezone

    from .models import ActivityDay, Group, PollAnswer

    print("📊 Preparing weekly attendance summary...")

    try:
        global_group = Group.objects.get(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID)
        )
    except Group.DoesNotExist:
        print("❌ Global group not found, skipping weekly summary")
        return False

    one_week_ago = timezone.now() - timedelta(days=7)
    yes_answers = PollAnswer.objects.filter(
        yes=True,
        poll__created__gte=one_week_ago,
        poll__node__isnull=False,
    ).select_related("poll__node", "person")

    node_attendance = {}
    all_person_ids = set()

    for answer in yes_answers:
        node = answer.poll.node
        if node.id not in node_attendance:
            node_attendance[node.id] = dict(
                node=node,
                person_ids=set(),
            )
        node_attendance[node.id]["person_ids"].add(answer.person_id)
        all_person_ids.add(answer.person_id)

    nodes_with_attendance = [
        data
        for data in node_attendance.values()
        if len(data["person_ids"]) > 0
    ]

    if not nodes_with_attendance:
        print("📊 No attendance this week, skipping summary")
        return False

    top_talker = (
        ActivityDay.objects.filter(
            group=global_group,
            date__gte=one_week_ago.date(),
        )
        .values("person", "person__username", "person__first_name")
        .annotate(total_messages=Sum("message_count"))
        .order_by("-total_messages")
        .first()
    )

    total_attendees = len(all_person_ids)
    lines = [f"📊 *Hacka\\* Network Weekly Stats*"]
    lines.append("")
    lines.append(f"🌍 *{total_attendees} people* joined a weekly meetup!")
    lines.append("")

    nodes_with_attendance.sort(
        key=lambda x: len(x["person_ids"]),
        reverse=True,
    )

    for data in nodes_with_attendance:
        node = data["node"]
        count = len(data["person_ids"])
        name = f"{node.emoji} {node.name}" if node.emoji else node.name
        lines.append(f"• {name}: {count}")

    if top_talker and top_talker["total_messages"] > 0:
        username = top_talker["person__username"]
        first_name = top_talker["person__first_name"]
        msg_count = top_talker["total_messages"]
        if username:
            escaped_username = username.replace("_", "\\_")
            display_name = f"@{escaped_username}"
        else:
            display_name = first_name or "Someone"
        lines.append("")
        lines.append(
            f"🏆 Biggest yapper of the week is {display_name} "
            f"({msg_count} messages)"
        )

    message = "\n".join(lines)

    print(
        f"📤 Sending weekly summary to global group {global_group.telegram_id}"
    )
    send(global_group.telegram_id, message)
    print("✅ Weekly attendance summary sent")
    return True
