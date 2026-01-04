import html
import json
import re
from datetime import datetime, timezone

from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

BIO_MAX_LENGTH = 500

from .models import (
    ActivityDay,
    Group,
    GroupPerson,
    Message,
    Node,
    Person,
    Poll,
    PollAnswer,
)
from .telegram import send, verify_webhook_secret


def _get_or_create_person(user_data):
    if not user_data:
        return None
    person, _ = Person.objects.update_or_create(
        telegram_id=user_data["id"],
        defaults=dict(
            is_bot=user_data.get("is_bot", False),
            first_name=user_data.get("first_name", ""),
            username=user_data.get("username", ""),
        ),
    )
    return person


def _onboard_new_member(person, group):
    if person.is_bot or person.onboarded:
        return

    name = person.first_name or "there"
    mention = f"[{name}](tg://user?id={person.telegram_id})"

    message = (
        f"👋 Welcome {mention}! "
        "Introduce yourself — what are you building? "
        "(DM me to set up your profile)"
    )

    send(group.telegram_id, message)

    person.onboarded = True
    person.save()


def _get_or_create_group(chat_data):
    if not chat_data:
        return None
    chat_type = chat_data.get("type", "")
    if chat_type not in ("group", "supergroup"):
        return None
    group, _ = Group.objects.update_or_create(
        telegram_id=chat_data["id"],
        defaults=dict(
            display_name=chat_data.get("title", ""),
        ),
    )
    return group


def _handle_message(message_data):
    chat_data = message_data.get("chat")
    group = _get_or_create_group(chat_data)
    if not group:
        return

    # Handle join events
    new_members = message_data.get("new_chat_members", [])
    for member_data in new_members:
        person = _get_or_create_person(member_data)
        if person:
            GroupPerson.objects.update_or_create(
                group=group,
                person=person,
                defaults=dict(left=False),
            )
            _onboard_new_member(person, group)

    # Handle leave events
    left_member = message_data.get("left_chat_member")
    if left_member:
        person = _get_or_create_person(left_member)
        if person:
            GroupPerson.objects.update_or_create(
                group=group,
                person=person,
                defaults=dict(left=True),
            )

    # Handle regular messages
    if message_data.get("text"):
        user_data = message_data.get("from")
        person = _get_or_create_person(user_data)
        unix_ts = message_data.get("date", 0)
        message_dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)

        _, is_new_message = Message.objects.update_or_create(
            telegram_id=message_data["message_id"],
            group=group,
            defaults=dict(
                person=person,
                date=message_dt,
                text=message_data.get("text", ""),
            ),
        )

        if person and is_new_message:
            # Update group membership and last message time
            GroupPerson.objects.update_or_create(
                group=group,
                person=person,
                defaults=dict(left=False, last_message_at=message_dt),
            )

            # Update activity bucket for today
            message_date = message_dt.date()
            activity, created = ActivityDay.objects.get_or_create(
                person=person,
                group=group,
                date=message_date,
                defaults=dict(message_count=1),
            )
            if not created:
                ActivityDay.objects.filter(pk=activity.pk).update(
                    message_count=F("message_count") + 1
                )

    # Handle poll in message (when bot sends a poll)
    poll_data = message_data.get("poll")
    if poll_data:
        _handle_poll_data(poll_data, group=group)


def _handle_poll_data(poll_data, group=None):
    node = None
    if group:
        node = group.node_set.first()

    options = poll_data.get("options", [])
    yes_count = 0
    no_count = 0
    if len(options) >= 2:
        yes_count = options[0].get("voter_count", 0)
        no_count = options[1].get("voter_count", 0)

    Poll.objects.update_or_create(
        telegram_id=poll_data["id"],
        defaults=dict(
            node=node,
            question=poll_data.get("question", ""),
            yes_count=yes_count,
            no_count=no_count,
        ),
    )


def _handle_poll_answer(poll_answer_data):
    poll_id = poll_answer_data.get("poll_id")
    user_data = poll_answer_data.get("user")
    option_ids = poll_answer_data.get("option_ids", [])

    if not poll_id or not user_data:
        return

    try:
        poll = Poll.objects.get(telegram_id=poll_id)
    except Poll.DoesNotExist:
        return

    person = _get_or_create_person(user_data)
    if not person:
        return

    # option_ids[0] == 0 means "Yes", option_ids[0] == 1 means "No"
    # Empty option_ids means vote was retracted
    if not option_ids:
        PollAnswer.objects.filter(poll=poll, person=person).delete()
    else:
        yes = option_ids[0] == 0
        PollAnswer.objects.update_or_create(
            poll=poll,
            person=person,
            defaults=dict(yes=yes),
        )


def _handle_chat_member(chat_member_data):
    chat_data = chat_member_data.get("chat")
    group = _get_or_create_group(chat_data)
    if not group:
        return

    new_member = chat_member_data.get("new_chat_member", {})
    user_data = new_member.get("user")
    status = new_member.get("status")

    if not user_data:
        return

    person = _get_or_create_person(user_data)
    if not person:
        return

    left = status in ("left", "kicked")
    GroupPerson.objects.update_or_create(
        group=group,
        person=person,
        defaults=dict(left=left),
    )

    if not left:
        _onboard_new_member(person, group)


def _handle_my_chat_member(my_chat_member_data):
    chat_data = my_chat_member_data.get("chat")
    _get_or_create_group(chat_data)


def _handle_dm(message_data):
    chat_data = message_data.get("chat")
    if not chat_data or chat_data.get("type") != "private":
        return

    user_data = message_data.get("from")
    if not user_data:
        return

    person = _get_or_create_person(user_data)
    if not person:
        return

    text = message_data.get("text", "").strip()
    chat_id = chat_data["id"]

    if text.startswith("/help") or text.startswith("/start"):
        _handle_help_command(chat_id, person)
    elif text.startswith("/x ") or text == "/x":
        _handle_x_command(chat_id, person, text)
    elif text.startswith("/privacy"):
        _handle_privacy_command(chat_id, person, text)
    elif text.startswith("/bio"):
        _handle_bio_command(chat_id, person, text)
    else:
        send(
            chat_id,
            "🤔 I don't recognise that command.\n\nType /help to see what I can do!",
        )


def _handle_help_command(chat_id, person):
    nodes = Node.objects.filter(
        group__groupperson__person=person,
        group__groupperson__left=False,
    ).distinct()

    lines = [
        "👋 *Welcome to Hackabot!*",
        "",
        "I'm the friendly bot for the Hacka\\* network — a global community "
        "of hackers, makers, and builders.",
        "",
        "🔒 *Privacy:* I never store any of your group messages.",
        "",
        "🌐 For more info, visit https://hacka.network",
        "",
    ]

    if nodes.exists():
        lines.append("📍 *Your nodes:*")
        for node in nodes:
            node_name = (
                f"{node.emoji} {node.name}" if node.emoji else node.name
            )
            lines.append(f"  • {node_name}")
    else:
        lines.append("📍 You're not in any Hacka\\* nodes yet!")

    lines.append("")
    lines.append(f"👤 *Your profile:*")
    lines.append(
        f"  • Telegram: @{person.username}" if person.username else ""
    )
    if person.username_x:
        lines.append(f"  • X/Twitter: @{person.username_x}")
    if person.bio:
        lines.append(f"  • Bio: _{person.bio}_")

    lines.append("")
    privacy_status = "ON 🔒" if person.privacy else "OFF 🔓"
    lines.append(f"🛡️ *Privacy mode:* {privacy_status}")

    lines.append("")
    lines.append("*Commands:*")
    lines.append("  /bio your text — set your bio")
    lines.append("  /bio — clear your bio")
    lines.append("  /x @username — set your X/Twitter username")
    lines.append("  /privacy on — turn privacy mode ON")
    lines.append("  /privacy off — turn privacy mode OFF")

    message = "\n".join(line for line in lines if line is not None)
    send(chat_id, message)


def _handle_x_command(chat_id, person, text):
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        send(
            chat_id,
            "❌ Please provide your X/Twitter username.\n\n"
            "Example: `/x @yourname`",
        )
        return

    username = parts[1].strip()
    if username.startswith("@"):
        username = username[1:]

    if not username:
        send(
            chat_id,
            "❌ Please provide a valid username.\n\n"
            "Example: `/x @yourname`",
        )
        return

    person.username_x = username
    person.save()

    send(chat_id, f"✅ Your X/Twitter username has been set to @{username}")


def _handle_privacy_command(chat_id, person, text):
    parts = text.lower().split()
    if len(parts) < 2 or parts[1] not in ("on", "off"):
        current = "ON 🔒" if person.privacy else "OFF 🔓"
        send(
            chat_id,
            f"🛡️ Your privacy mode is currently *{current}*\n\n"
            "Use `/privacy on` or `/privacy off` to change it.",
        )
        return

    new_value = parts[1] == "on"
    person.privacy = new_value
    person.save()

    status = "ON 🔒" if new_value else "OFF 🔓"
    send(chat_id, f"✅ Privacy mode is now *{status}*")


def _handle_bio_command(chat_id, person, text):
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        person.bio = ""
        person.save()
        send(chat_id, "✅ Your bio has been cleared.")
        return

    bio_text = parts[1].strip()

    if len(bio_text) > BIO_MAX_LENGTH:
        send(
            chat_id,
            f"❌ Bio is too long ({len(bio_text)} characters).\n\n"
            f"Maximum length is {BIO_MAX_LENGTH} characters.",
        )
        return

    if "<" in bio_text or ">" in bio_text:
        send(
            chat_id,
            "❌ Bio cannot contain HTML tags.",
        )
        return

    if re.search(r"/\w+", bio_text):
        send(
            chat_id,
            "❌ Bio cannot contain Telegram commands (e.g. /something).",
        )
        return

    bio_text = html.unescape(bio_text)

    person.bio = bio_text
    person.save()

    send(chat_id, f"✅ Your bio has been set to:\n\n_{bio_text}_")


@csrf_exempt
@require_POST
def telegram_webhook(request):
    if not verify_webhook_secret(request):
        return HttpResponse(status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    print(f"[webhook] received update: {json.dumps(data, indent=2)}")

    # Handle message (includes text messages, join/leave service messages, polls)
    if "message" in data:
        message_data = data["message"]
        chat_type = message_data.get("chat", {}).get("type", "")
        if chat_type == "private":
            _handle_dm(message_data)
        else:
            _handle_message(message_data)

    # Handle poll state updates
    if "poll" in data:
        _handle_poll_data(data["poll"])

    # Handle individual poll answers
    if "poll_answer" in data:
        _handle_poll_answer(data["poll_answer"])

    # Handle chat member updates (join/leave via admin view)
    if "chat_member" in data:
        _handle_chat_member(data["chat_member"])

    # Handle bot's own membership updates (e.g. bot added to a group)
    if "my_chat_member" in data:
        _handle_my_chat_member(data["my_chat_member"])

    return JsonResponse(dict(ok=True))
