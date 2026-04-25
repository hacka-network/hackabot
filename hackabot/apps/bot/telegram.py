import hmac
import os
import sys
from collections import defaultdict
from datetime import timedelta

import requests
from django.db.models import Count, Q, Sum
from django.utils import timezone
from requests import HTTPError

from .models import (
    ActivityDay,
    Group,
    MeetupPhoto,
    Node,
    Person,
    Poll,
    PollAnswer,
)

REQUEST_TIMEOUT = 30
# Fullwidth asterisk (U+FF0A) so the whole title can sit inside a Markdown
# bold range — Telegram's MD V1 parser doesn't reliably handle \* inside *…*.
HACKA_BOLD = "Hacka＊"


def _raise_for_status(resp):
    try:
        resp.raise_for_status()
    except HTTPError as e:
        raise HTTPError(
            f"{e.response.status_code} {e.response.reason}: {e.response.text}",
            response=e.response,
        ) from e


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
    _raise_for_status(resp)
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
    _raise_for_status(resp)
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
    _raise_for_status(resp)
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
    _raise_for_status(resp)
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
    _raise_for_status(resp)
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
    _raise_for_status(resp)
    result = resp.json()
    return result.get("result")


def send_poll(node, when="Thursday", send_invite=True):
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
    _raise_for_status(resp)
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
                message_id=message_id,
                question=poll_data.get("question", ""),
                yes_count=0,
                no_count=0,
            ),
        )
        print("✅ Poll saved to database")

    if send_invite:
        print("📤 Sending global chat invite message...")
        hacka_network_global_invite_url = "https://t.me/+XTK6oIHCVZFkNmY1"
        send(
            chat_id,
            "...you can also join the "
            "[Hacka* global chat 🌏💻🤓]"
            f"({hacka_network_global_invite_url})",
        )
    else:
        print("⏭️ Skipping global chat invite message")

    # Unpin previous polls for this node
    old_polls = Poll.objects.filter(
        node=node, message_id__isnull=False
    ).exclude(message_id=message_id)
    for old_poll in old_polls:
        try:
            print(f"📤 Unpinning old poll (message {old_poll.message_id})")
            resp = requests.post(
                f"{TELEGRAM_API_BASE}/{token}/unpinChatMessage",
                json=dict(
                    chat_id=chat_id,
                    message_id=old_poll.message_id,
                ),
                timeout=REQUEST_TIMEOUT,
            )
            print(f"📥 unpinChatMessage response: {resp.json()}")
        except requests.RequestException as e:
            print(f"⚠️ Failed to unpin old poll: {e}")

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


def send_chat_action(chat_id, action="typing"):
    token = _get_bot_token()
    print(f"📤 Calling Telegram API: sendChatAction ({action}) to {chat_id}")
    resp = requests.post(
        f"{TELEGRAM_API_BASE}/{token}/sendChatAction",
        json=dict(chat_id=chat_id, action=action),
        timeout=REQUEST_TIMEOUT,
    )
    _raise_for_status(resp)
    print(f"✅ Chat action '{action}' sent")


def download_file(file_id):
    token = _get_bot_token()
    print(f"📤 Calling Telegram API: getFile for {file_id[:20]}...")
    resp = requests.post(
        f"{TELEGRAM_API_BASE}/{token}/getFile",
        json=dict(file_id=file_id),
        timeout=REQUEST_TIMEOUT,
    )
    _raise_for_status(resp)
    result = resp.json()
    file_path = result.get("result", {}).get("file_path")
    if not file_path:
        print("❌ No file_path in getFile response")
        return None
    print(f"📥 Downloading file: {file_path}")
    file_url = f"{TELEGRAM_API_BASE}/file/{token}/{file_path}"
    resp = requests.get(file_url, timeout=60)
    _raise_for_status(resp)
    print(f"✅ Downloaded {len(resp.content)} bytes")
    return resp.content


def is_chat_admin(chat_id, user_id):
    token = _get_bot_token()
    print(f"📤 Calling Telegram API: getChatMember for user {user_id}")
    resp = requests.post(
        f"{TELEGRAM_API_BASE}/{token}/getChatMember",
        json=dict(chat_id=chat_id, user_id=user_id),
        timeout=REQUEST_TIMEOUT,
    )
    _raise_for_status(resp)
    result = resp.json().get("result", {})
    status = result.get("status", "")
    is_admin = status in ("administrator", "creator")
    print(f"📥 User {user_id} status: {status} (admin={is_admin})")
    return is_admin


def restrict_chat_member(chat_id, user_id, until_date):
    print(
        f"📤 Calling Telegram API: restrictChatMember "
        f"user {user_id} until {until_date}"
    )
    token = _get_bot_token()
    url = f"{TELEGRAM_API_BASE}/{token}/restrictChatMember"
    resp = requests.post(
        url,
        json=dict(
            chat_id=chat_id,
            user_id=user_id,
            permissions=dict(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
            ),
            until_date=until_date,
        ),
        timeout=REQUEST_TIMEOUT,
    )
    print(f"📥 restrictChatMember response: {resp.text}")
    _raise_for_status(resp)
    print("✅ User restricted successfully")


def _md_escape(s):
    return (s or "").replace("_", "\\_").replace("*", "\\*")


def _display_person(username, first_name):
    if username:
        return f"@{_md_escape(username)}"
    return _md_escape(first_name or "Someone")


def _monday_of_week(d):
    return d - timedelta(days=d.weekday())


def _longest_active_streak(person_ids, current_monday):
    if not person_ids:
        return 0, None
    person_mondays = defaultdict(set)
    for pa in PollAnswer.objects.filter(
        yes=True, person_id__in=person_ids
    ).select_related("poll"):
        person_mondays[pa.person_id].add(
            _monday_of_week(pa.poll.created.date())
        )
    best_count = 0
    best_pid = None
    for pid in person_ids:
        mondays = person_mondays[pid]
        streak = 0
        m = current_monday
        while m in mondays:
            streak += 1
            m -= timedelta(days=7)
        if streak > best_count:
            best_count = streak
            best_pid = pid
    return best_count, best_pid


def send_weekly_attendance_summary():
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

    node_attendance = dict()
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

    nodes_with_attendance = sorted(
        [d for d in node_attendance.values() if d["person_ids"]],
        key=lambda x: len(x["person_ids"]),
        reverse=True,
    )

    if not nodes_with_attendance:
        print("📊 No attendance this week, skipping summary")
        return False

    top_yapper = (
        ActivityDay.objects.filter(
            group=global_group,
            date__gt=one_week_ago.date(),
        )
        .values("person", "person__username", "person__first_name")
        .annotate(total_messages=Sum("message_count"))
        .order_by("-total_messages")
        .first()
    )

    node_group_ids = list(
        Node.objects.exclude(group__isnull=True)
        .values_list("group_id", flat=True)
        .distinct()
    )
    loudest = (
        ActivityDay.objects.filter(
            date__gt=one_week_ago.date(),
            group_id__in=node_group_ids,
        )
        .exclude(group=global_group)
        .values("group", "group__display_name")
        .annotate(total=Sum("message_count"))
        .order_by("-total")
        .first()
    )

    countries = {
        d["node"].emoji for d in nodes_with_attendance if d["node"].emoji
    }

    prior_attendees = set(
        PollAnswer.objects.filter(
            yes=True,
            poll__created__lt=one_week_ago,
            person_id__in=all_person_ids,
        ).values_list("person_id", flat=True)
    )
    first_timer_ids = all_person_ids - prior_attendees
    first_timers = list(Person.objects.filter(id__in=first_timer_ids))

    current_monday = _monday_of_week(timezone.now().date())
    top_streak_count, top_streak_pid = _longest_active_streak(
        all_person_ids, current_monday
    )

    total_attendees = len(all_person_ids)
    lines = [f"📊 *{HACKA_BOLD} Network Weekly Stats*", ""]
    if countries:
        lines.append(
            f"🌍 *{total_attendees} people* across "
            f"*{len(countries)} countries* came to one of the meetups!"
        )
    else:
        lines.append(
            f"🌍 *{total_attendees} people* came to one of the meetups!"
        )
    lines.append("")

    for data in nodes_with_attendance:
        node = data["node"]
        count = len(data["person_ids"])
        name = f"{node.emoji} {node.name}" if node.emoji else node.name
        lines.append(f"• {name}: {count}")

    lines.append("")
    if top_yapper and top_yapper["total_messages"] > 0:
        name = _display_person(
            top_yapper["person__username"],
            top_yapper["person__first_name"],
        )
        lines.append(
            f"🏆 Biggest yapper of the week is {name} "
            f"({top_yapper['total_messages']} messages)"
        )

    if loudest:
        nfg = (
            Node.objects.filter(group_id=loudest["group"])
            .order_by("name")
            .first()
        )
        if nfg:
            nname = f"{nfg.emoji} {nfg.name}" if nfg.emoji else nfg.name
        else:
            nname = _md_escape(loudest["group__display_name"])
        lines.append(
            f"🗣️ Yappiest group chat of the week is {nname} "
            f"({loudest['total']} messages)"
        )

    if top_streak_count >= 2 and top_streak_pid:
        p = Person.objects.get(id=top_streak_pid)
        name = _display_person(p.username, p.first_name)
        lines.append(
            f"🔥 Longest streak is {name} "
            f"({top_streak_count} attendances in a row!)"
        )

    if first_timers:
        if len(first_timers) <= 5:
            names = ", ".join(
                _display_person(p.username, p.first_name) for p in first_timers
            )
            lines.append(f"👋 First-timers this week: {names}")
        else:
            lines.append(f"👋 {len(first_timers)} first-timers this week")

    message = "\n".join(lines)

    print(
        f"📤 Sending weekly summary to global group {global_group.telegram_id}"
    )
    send(global_group.telegram_id, message)
    print("✅ Weekly attendance summary sent")
    return True


def send_yearly_summary():
    print("🎉 Preparing yearly summary...")

    try:
        global_group = Group.objects.get(
            telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID)
        )
    except Group.DoesNotExist:
        print("❌ Global group not found, skipping yearly summary")
        return False

    year = timezone.now().year

    yes_year = PollAnswer.objects.filter(
        yes=True,
        poll__created__year=year,
        poll__node__isnull=False,
    ).select_related("poll__node")

    node_totals = dict()
    for answer in yes_year:
        node = answer.poll.node
        if node.id not in node_totals:
            node_totals[node.id] = dict(node=node, count=0)
        node_totals[node.id]["count"] += 1
    top_nodes = sorted(
        node_totals.values(), key=lambda d: d["count"], reverse=True
    )[:3]

    top_yappers = list(
        ActivityDay.objects.filter(group=global_group, date__year=year)
        .values("person", "person__username", "person__first_name")
        .annotate(total_messages=Sum("message_count"))
        .order_by("-total_messages")[:3]
    )

    top_photographer = (
        MeetupPhoto.objects.filter(
            created__year=year, uploaded_by__isnull=False
        )
        .values(
            "uploaded_by__username",
            "uploaded_by__first_name",
        )
        .annotate(c=Count("id"))
        .order_by("-c")
        .first()
    )

    explorer = (
        PollAnswer.objects.filter(
            yes=True,
            poll__created__year=year,
            poll__node__isnull=False,
        )
        .values("person__username", "person__first_name")
        .annotate(distinct_nodes=Count("poll__node", distinct=True))
        .order_by("-distinct_nodes")
        .first()
    )

    regular = (
        PollAnswer.objects.filter(
            yes=True,
            poll__created__year=year,
            poll__node__isnull=False,
        )
        .values(
            "person__username",
            "person__first_name",
            "poll__node__name",
            "poll__node__emoji",
        )
        .annotate(c=Count("id"))
        .order_by("-c")
        .first()
    )

    new_nodes = list(
        Node.objects.filter(created__year=year).order_by("created")
    )

    best_poll = (
        Poll.objects.filter(created__year=year, node__isnull=False)
        .annotate(real_yes=Count("pollanswer", filter=Q(pollanswer__yes=True)))
        .filter(real_yes__gt=0)
        .order_by("-real_yes")
        .select_related("node")
        .first()
    )

    has_yappers = bool(top_yappers and top_yappers[0]["total_messages"] > 0)
    if not top_nodes and not has_yappers:
        print("🎉 No activity this year, skipping yearly summary")
        return False

    lines = [
        f"🎉 *{HACKA_BOLD} Network {year} Year in Review*",
        "",
    ]

    if has_yappers:
        lines.append("*Top yappers:*")
        for medal, t in zip(["🥇", "🥈", "🥉"], top_yappers):
            name = _display_person(
                t["person__username"], t["person__first_name"]
            )
            lines.append(f"{medal} {name} ({t['total_messages']} messages)")
        lines.append("")

    if top_nodes:
        lines.append("*Top nodes:*")
        for medal, n_data in zip(["🥇", "🥈", "🥉"], top_nodes):
            node = n_data["node"]
            name = f"{node.emoji} {node.name}" if node.emoji else node.name
            lines.append(f"{medal} {name} ({n_data['count']} attendances)")
        lines.append("")

    if new_nodes:
        lines.append("*New nodes this year:*")
        for n in new_nodes:
            nname = f"{n.emoji} {n.name}" if n.emoji else n.name
            lines.append(f"• {nname}")
        lines.append("")

    if top_photographer and top_photographer["c"] > 0:
        name = _display_person(
            top_photographer["uploaded_by__username"],
            top_photographer["uploaded_by__first_name"],
        )
        lines.append(
            f"📸 *Photographer of the Year:* {name} "
            f"({top_photographer['c']} photos)"
        )

    if explorer and explorer["distinct_nodes"] >= 2:
        name = _display_person(
            explorer["person__username"],
            explorer["person__first_name"],
        )
        lines.append(
            f"🧭 *The Explorer:* {name} "
            f"(visited {explorer['distinct_nodes']} different nodes)"
        )

    if regular and regular["c"] >= 2:
        node_name = (
            f"{regular['poll__node__emoji']} " f"{regular['poll__node__name']}"
            if regular["poll__node__emoji"]
            else regular["poll__node__name"]
        )
        name = _display_person(
            regular["person__username"],
            regular["person__first_name"],
        )
        lines.append(
            f"🪑 *The Regular:* {name} went to {node_name} "
            f"{regular['c']} times"
        )

    if best_poll:
        node = best_poll.node
        nname = f"{node.emoji} {node.name}" if node.emoji else node.name
        date_str = best_poll.created.strftime("%b %d")
        lines.append(
            f"🏟️ *Attendance record:* {nname} with "
            f"{best_poll.real_yes} attendees (week of {date_str})"
        )

    message = "\n".join(lines)

    print(
        f"📤 Sending yearly summary to global group "
        f"{global_group.telegram_id}"
    )
    send(global_group.telegram_id, message)
    print("✅ Yearly summary sent")
    return True
