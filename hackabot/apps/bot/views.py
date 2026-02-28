import html
import json
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import arrow

from django.conf import settings
from django.db.models import F, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone as django_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import (
    ActivityDay,
    Group,
    GroupPerson,
    MeetupPhoto,
    Node,
    Person,
    Poll,
    PollAnswer,
)
from .telegram import (
    HACKA_NETWORK_GLOBAL_CHAT_ID,
    answer_callback_query,
    download_file,
    send,
    send_chat_action,
    verify_webhook_secret,
)

BIO_MAX_LENGTH = 140

if settings.IS_PRODUCTION:
    PHOTO_UPLOAD_CHAT_ID = -1002257954378
else:
    PHOTO_UPLOAD_CHAT_ID = -5117513714


def _sanitize_for_html(text):
    if not text:
        return text
    return html.escape(text, quote=True)


def _get_or_create_person(user_data):
    if not user_data:
        return None
    person, created = Person.objects.update_or_create(
        telegram_id=user_data["id"],
        defaults=dict(
            is_bot=user_data.get("is_bot", False),
            first_name=user_data.get("first_name", ""),
            username=user_data.get("username", ""),
        ),
    )
    action = "created" if created else "updated"
    print(f"👤 Person {action}: {person.first_name} (@{person.username})")
    return person


def _onboard_new_member(person, group):
    if person.is_bot:
        return

    if not group.node_set.exists():
        print(
            f"⏭️ Skipping onboard for {person.first_name} - "
            f"{group.display_name} has no Node"
        )
        return

    print(
        f"🎉 Onboarding new member: {person.first_name} in {group.display_name}"
    )

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
    print(f"✅ Onboarding complete for {person.first_name}")


def _get_or_create_group(chat_data):
    if not chat_data:
        return None
    chat_type = chat_data.get("type", "")
    if chat_type not in ("group", "supergroup"):
        return None
    group, created = Group.objects.update_or_create(
        telegram_id=chat_data["id"],
        defaults=dict(
            display_name=chat_data.get("title", ""),
        ),
    )
    action = "created" if created else "updated"
    print(f"👥 Group {action}: {group.display_name}")
    return group


def _handle_message(message_data):
    print("💬 Handling group message...")
    chat_data = message_data.get("chat")
    group = _get_or_create_group(chat_data)
    if not group:
        print("⚠️ No valid group found, skipping message")
        return

    # Handle join events
    new_members = message_data.get("new_chat_members", [])
    if new_members:
        print(f"➡️ Processing {len(new_members)} new member(s) joining")
    for member_data in new_members:
        person = _get_or_create_person(member_data)
        if person:
            GroupPerson.objects.update_or_create(
                group=group,
                person=person,
                defaults=dict(left=False),
            )
            print(f"✅ {person.first_name} joined {group.display_name}")

    # Handle leave events
    left_member = message_data.get("left_chat_member")
    if left_member:
        print("⬅️ Processing member leaving")
        person = _get_or_create_person(left_member)
        if person:
            GroupPerson.objects.update_or_create(
                group=group,
                person=person,
                defaults=dict(left=True),
            )
            print(f"👋 {person.first_name} left {group.display_name}")

    # Handle regular messages
    if message_data.get("text"):
        print(f"📝 Processing text message in {group.display_name}")
        user_data = message_data.get("from")
        person = _get_or_create_person(user_data)
        unix_ts = message_data.get("date", 0)
        message_dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)

        if person:
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
        print("📊 Found poll in message, processing...")
        _handle_poll_data(poll_data, group=group)

    # Handle photo uploads in designated group
    chat_id = message_data.get("chat", {}).get("id")
    if chat_id == PHOTO_UPLOAD_CHAT_ID:
        photos = message_data.get("photo", [])
        caption = message_data.get("caption", "")
        if photos and caption:
            node = _find_node_from_hashtags(caption)
            if node:
                _handle_photo_upload(message_data, node, photos, chat_id)

        text = message_data.get("text", "")
        if text and text.strip().lower() == "delete":
            _handle_delete_reply(message_data)

        # Handle hashtag reply to photo (for adding hashtag after the fact)
        if text:
            _handle_hashtag_reply(message_data, chat_id)


def _handle_poll_data(poll_data, group=None):
    print(
        f"📊 Handling poll data: {poll_data.get('question', 'unknown')[:50]}..."
    )

    options = poll_data.get("options", [])
    yes_count = 0
    no_count = 0
    if len(options) >= 2:
        yes_count = options[0].get("voter_count", 0)
        no_count = options[1].get("voter_count", 0)

    defaults = dict(
        question=poll_data.get("question", ""),
        yes_count=yes_count,
        no_count=no_count,
    )

    if group:
        node = group.node_set.first()
        defaults["node"] = node

    poll, created = Poll.objects.update_or_create(
        telegram_id=poll_data["id"],
        defaults=defaults,
    )
    action = "created" if created else "updated"
    print(f"📊 Poll {action}: yes={yes_count}, no={no_count}")


FUZZY_MATCH_THRESHOLD = 0.85


def _find_node_from_hashtags(text):
    if not text:
        return None
    hashtags = re.findall(r"#(\w+)", text.lower())
    if not hashtags:
        return None

    nodes = list(Node.objects.filter(disabled=False))

    for node in nodes:
        if node.name_slug in hashtags:
            return node

    best_match = None
    best_ratio = 0
    for node in nodes:
        node_name_lower = node.name_slug
        for hashtag in hashtags:
            ratio = SequenceMatcher(None, node_name_lower, hashtag).ratio()
            if ratio >= FUZZY_MATCH_THRESHOLD and ratio > best_ratio:
                best_ratio = ratio
                best_match = node

    return best_match


def _escape_markdown(text):
    for char in [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]:
        text = text.replace(char, f"\\{char}")
    return text


def _get_event_date(upload_dt, event_day, tz="UTC"):
    local_dt = arrow.get(upload_dt).to(tz)
    days_back = (local_dt.weekday() - event_day) % 7
    return local_dt.shift(days=-days_back).datetime


def _handle_photo_upload(message_data, node, photos, chat_id):
    from .images import process_image

    largest_photo = photos[-1]
    file_id = largest_photo.get("file_id")
    if not file_id:
        send(chat_id, "Hmm, something went wrong with that photo. Try again?")
        return

    if MeetupPhoto.objects.filter(telegram_file_id=file_id).exists():
        print(f"⏭️ Photo already exists: {file_id[:20]}...")
        return

    send_chat_action(chat_id, "typing")

    user_data = message_data.get("from")
    uploader = _get_or_create_person(user_data) if user_data else None

    image_bytes = download_file(file_id)
    if not image_bytes:
        send(chat_id, "Couldn't download that photo. Try again?")
        return

    processed = process_image(image_bytes)
    if not processed:
        send(chat_id, "Couldn't process that image. Is it a valid photo?")
        return

    event_date = _get_event_date(
        django_timezone.now(), node.event_day, node.timezone
    )
    MeetupPhoto.objects.create(
        node=node,
        telegram_file_id=file_id,
        image_data=processed,
        uploaded_by=uploader,
        created=event_date,
    )

    node_name = _escape_markdown(node.name)
    emoji = node.emoji or ""
    send(
        chat_id,
        f"Thanks! Added your {emoji} {node_name} photo to hacka.network",
    )
    print(f"✅ Saved meetup photo for {node.name} ({len(processed)} bytes)")


def _handle_delete_reply(message_data):
    chat_id = message_data.get("chat", {}).get("id")
    reply_to = message_data.get("reply_to_message")

    if not reply_to:
        return

    photos = reply_to.get("photo", [])
    if not photos:
        return

    file_id = photos[-1].get("file_id")
    photo = MeetupPhoto.objects.filter(telegram_file_id=file_id).first()

    if photo:
        node_name = _escape_markdown(photo.node.name)
        emoji = photo.node.emoji or ""
        photo.delete()
        send(chat_id, f"Removed {emoji} {node_name} photo from hacka.network")
    else:
        send(chat_id, "That photo isn't on the website")


def _handle_hashtag_reply(message_data, chat_id):
    text = message_data.get("text", "")
    reply_to = message_data.get("reply_to_message")

    if not reply_to:
        return

    photos = reply_to.get("photo", [])
    if not photos:
        return

    largest_photo = photos[-1]
    file_id = largest_photo.get("file_id")
    if not file_id:
        return

    if MeetupPhoto.objects.filter(telegram_file_id=file_id).exists():
        print(f"⏭️ Photo already uploaded, ignoring hashtag reply")
        return

    node = _find_node_from_hashtags(text)
    if not node:
        return

    _handle_photo_upload(reply_to, node, photos, chat_id)


def _handle_poll_answer(poll_answer_data):
    print("🗳️ Handling poll answer...")
    poll_id = poll_answer_data.get("poll_id")
    user_data = poll_answer_data.get("user")
    option_ids = poll_answer_data.get("option_ids", [])

    if not poll_id or not user_data:
        print("⚠️ Missing poll_id or user_data, skipping")
        return

    try:
        poll = Poll.objects.get(telegram_id=poll_id)
    except Poll.DoesNotExist:
        print(f"⚠️ Poll {poll_id} not found in database")
        return

    person = _get_or_create_person(user_data)
    if not person:
        print("⚠️ Could not get/create person, skipping")
        return

    # option_ids[0] == 0 means "Yes", option_ids[0] == 1 means "No"
    # Empty option_ids means vote was retracted
    if not option_ids:
        PollAnswer.objects.filter(poll=poll, person=person).delete()
        print(f"🗳️ {person.first_name} retracted their vote")
    else:
        yes = option_ids[0] == 0
        PollAnswer.objects.update_or_create(
            poll=poll,
            person=person,
            defaults=dict(yes=yes),
        )
        vote = "Yes ✅" if yes else "No 👎"
        print(f"🗳️ {person.first_name} voted: {vote}")


def _handle_chat_member(chat_member_data):
    print("👥 Handling chat_member update...")
    chat_data = chat_member_data.get("chat")
    group = _get_or_create_group(chat_data)
    if not group:
        print("⚠️ No valid group found, skipping chat_member")
        return

    new_member = chat_member_data.get("new_chat_member", {})
    user_data = new_member.get("user")
    status = new_member.get("status")
    print(f"👥 Member status change: {status}")

    if not user_data:
        print("⚠️ No user_data in chat_member, skipping")
        return

    person = _get_or_create_person(user_data)
    if not person:
        print("⚠️ Could not get/create person, skipping")
        return

    left = status in ("left", "kicked")
    GroupPerson.objects.update_or_create(
        group=group,
        person=person,
        defaults=dict(left=left),
    )

    if left:
        print(f"👋 {person.first_name} left/kicked from {group.display_name}")
    else:
        print(f"➡️ {person.first_name} joined {group.display_name}")
        _onboard_new_member(person, group)


def _handle_my_chat_member(my_chat_member_data):
    print("🤖 Handling my_chat_member update (bot membership change)...")
    chat_data = my_chat_member_data.get("chat")
    new_status = my_chat_member_data.get("new_chat_member", {}).get("status")
    print(f"🤖 Bot status changed to: {new_status}")
    _get_or_create_group(chat_data)


def _handle_dm(message_data):
    print("📩 Handling DM...")
    chat_data = message_data.get("chat")
    if not chat_data or chat_data.get("type") != "private":
        print("⚠️ Not a private chat, skipping DM handler")
        return

    user_data = message_data.get("from")
    if not user_data:
        print("⚠️ No user_data in DM, skipping")
        return

    person = _get_or_create_person(user_data)
    if not person:
        print("⚠️ Could not get/create person, skipping DM")
        return

    text = message_data.get("text", "").strip()
    chat_id = chat_data["id"]
    print(f"📩 DM from {person.first_name}: {text[:50]}...")

    is_member_of_any_node = Node.objects.filter(
        group__groupperson__person=person,
        group__groupperson__left=False,
    ).exists()

    if not is_member_of_any_node:
        print("⚠️ User is not in any node, sending join prompt")
        send(
            chat_id,
            "👋 Hey! I'm the bot for the Hacka* network.\n\n"
            "To use me, you need to be a member of at least one Hacka* node.\n\n"
            "Head to https://hacka.network to find and apply to your local one!",
        )
        return

    if text.startswith("/help") or text.startswith("/start"):
        print("📩 Processing /help or /start command")
        _handle_help_command(chat_id, person)
    elif text.startswith("/x ") or text == "/x":
        print("📩 Processing /x command")
        _handle_x_command(chat_id, person, text)
    elif text.startswith("/privacy"):
        print("📩 Processing /privacy command")
        _handle_privacy_command(chat_id, person, text)
    elif text.startswith("/bio"):
        print("📩 Processing /bio command")
        _handle_bio_command(chat_id, person, text)
    elif text.startswith("/people"):
        print("📩 Processing /people command")
        _handle_people_command(chat_id, person)
    # elif text.startswith("/nodes"):
    #     print("📩 Processing /nodes command")
    #     _handle_nodes_command(chat_id, person)
    else:
        print("📩 Unknown command, sending help prompt")
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
    if person.username:
        escaped_username = person.username.replace("_", "\\_")
        lines.append(f"  • Telegram: @{escaped_username}")
    if person.username_x:
        escaped_x = person.username_x.replace("_", "\\_")
        lines.append(f"  • X/Twitter: @{escaped_x}")
    if person.bio:
        escaped_bio = person.bio.replace("_", "\\_")
        lines.append(f"  • Bio: _{escaped_bio}_")

    lines.append("")
    privacy_status = "ON 🔒" if person.privacy else "OFF 🔓"
    lines.append(f"🛡️ *Privacy mode:* {privacy_status}")
    if person.privacy:
        lines.append("  You are hidden from hacka.network")
    else:
        lines.append("  You are listed on hacka.network for your nodes")

    lines.append("")
    lines.append("*Commands:*")
    lines.append("  /bio your text — set your bio")
    lines.append("  /bio unset — clear your bio")
    lines.append("  /x @username — set your X/Twitter username")
    lines.append("  /privacy on — turn privacy mode ON")
    lines.append("  /privacy off — turn privacy mode OFF")
    lines.append("  /people — list people in your nodes")
    # lines.append("  /nodes — browse all nodes and get invite links")

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

    if "<" in username or ">" in username:
        send(
            chat_id,
            "❌ Username cannot contain HTML characters.",
        )
        return

    if not re.match(r"^[a-zA-Z0-9_]+$", username):
        send(
            chat_id,
            "❌ Please provide a valid username.\n\n"
            "Example: `/x @yourname`",
        )
        return

    person.username_x = username
    person.save()

    escaped_username = username.replace("_", "\\_")
    message = f"✅ Your X/Twitter username has been set to @{escaped_username}"

    if person.privacy:
        message += (
            "\n\n💡 Your privacy mode is ON, so you won't appear on "
            "hacka.network. Use `/privacy off` to be listed!"
        )

    send(chat_id, message)


def _handle_privacy_command(chat_id, person, text):
    parts = text.lower().split()
    if len(parts) < 2 or parts[1] not in ("on", "off"):
        current = "ON 🔒" if person.privacy else "OFF 🔓"
        if person.privacy:
            explanation = "You are hidden from hacka.network"
        else:
            explanation = "You are listed on hacka.network for your nodes"
        send(
            chat_id,
            f"🛡️ Your privacy mode is currently *{current}*\n"
            f"{explanation}\n\n"
            "Use `/privacy on` or `/privacy off` to change it.",
        )
        return

    new_value = parts[1] == "on"
    person.privacy = new_value
    person.save()

    if new_value:
        explanation = "You are now hidden from hacka.network"
    else:
        explanation = "You are now listed on hacka.network for your nodes"
    status = "ON 🔒" if new_value else "OFF 🔓"
    send(chat_id, f"✅ Privacy mode is now *{status}*\n{explanation}")


def _handle_people_command(chat_id, person):
    nodes = Node.objects.filter(
        group__groupperson__person=person,
        group__groupperson__left=False,
    ).distinct()

    if not nodes.exists():
        send(chat_id, "📍 You're not in any Hacka\\* nodes yet!")
        return

    lines = ["👥 *People in your nodes:*", ""]

    for node in nodes:
        node_name = f"{node.emoji} {node.name}" if node.emoji else node.name
        lines.append(f"*{node_name}*")

        if not node.group:
            lines.append("  _No group linked_")
            lines.append("")
            continue

        people = (
            Person.objects.filter(
                groupperson__group=node.group,
                groupperson__left=False,
                privacy=False,
            )
            .filter(Q(first_name__gt="") | Q(username_x__gt=""))
            .distinct()
            .order_by("first_name")
        )

        if not people.exists():
            lines.append("  _No public profiles yet_")
            lines.append("")
            continue

        for p in people:
            name = p.first_name or "Unknown"
            parts = [f"  • {name}"]
            if p.username_x:
                parts.append(
                    f"[@{p.username_x}](https://x.com/{p.username_x})"
                )
            lines.append(" ".join(parts))
            if p.bio:
                lines.append(f"    _{p.bio}_")

        lines.append("")

    lines.append("_Only showing people with privacy mode OFF_")
    send(chat_id, "\n".join(lines))


def _handle_bio_command(chat_id, person, text):
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        current = f"_{person.bio}_" if person.bio else "not set"
        send(
            chat_id,
            f"📝 Your bio is currently: {current}\n\n"
            "Use `/bio your text` to set it, or `/bio unset` to clear it.",
        )
        return

    bio_text = parts[1].strip()

    if bio_text.lower() == "unset":
        person.bio = ""
        person.save()
        send(chat_id, "✅ Your bio has been cleared.")
        return

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

    message = f"✅ Your bio has been set to:\n\n_{bio_text}_"

    if person.privacy:
        message += (
            "\n\n💡 Your privacy mode is ON, so you won't appear on "
            "hacka.network. Use `/privacy off` to be listed!"
        )

    send(chat_id, message)


# def _handle_nodes_command(chat_id, person):
#     nodes = Node.objects.exclude(group__isnull=True).order_by("name")
#
#     if not nodes.exists():
#         send(chat_id, "📍 No Hacka\\* nodes are currently available to join.")
#         return
#
#     keyboard = []
#     for node in nodes:
#         node_name = f"{node.emoji} {node.name}" if node.emoji else node.name
#         if node.location:
#             node_name = f"{node_name} — {node.location}"
#         keyboard.append(
#             [dict(text=node_name, callback_data=f"node_invite:{node.slug}")]
#         )
#
#     send_with_keyboard(
#         chat_id,
#         "🌍 Tap a node to get its invite link:",
#         keyboard,
#     )


# def _handle_node_invite_callback(callback_query_id, chat_id, node_slug):
#     try:
#         node = Node.objects.get(slug=node_slug)
#     except Node.DoesNotExist:
#         answer_callback_query(callback_query_id, "Node not found")
#         return
#
#     if not node.group:
#         answer_callback_query(callback_query_id, "No group linked")
#         return
#
#     answer_callback_query(callback_query_id)
#
#     invite_link = export_chat_invite_link(node.group.telegram_id)
#     node_name = f"{node.emoji} {node.name}" if node.emoji else node.name
#     message = f"🔗 *{node_name}* invite link:\n\n{invite_link}"
#     send(chat_id, message)


def _handle_callback_query(callback_query_data):
    print("🔘 Handling callback query...")
    callback_query_id = callback_query_data.get("id")
    callback_data = callback_query_data.get("data", "")
    chat_id = callback_query_data.get("message", {}).get("chat", {}).get("id")

    if not callback_query_id or not chat_id:
        print("⚠️ Missing callback_query_id or chat_id, skipping")
        return

    # if callback_data.startswith("node_invite:"):
    #     node_slug = callback_data.replace("node_invite:", "")
    #     print(f"🔘 Processing node invite callback for {node_slug}")
    #     _handle_node_invite_callback(callback_query_id, chat_id, node_slug)
    # else:
    if True:
        print(f"⚠️ Unknown callback_data: {callback_data}")
        answer_callback_query(callback_query_id)


@csrf_exempt
@require_POST
def telegram_webhook(request):
    print("📥 Incoming webhook request...")
    if not verify_webhook_secret(request):
        print("🔐❌ Webhook secret verification FAILED")
        return HttpResponse(status=403)
    print("🔐✅ Webhook secret verified")

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        print("❌ Failed to parse JSON body")
        return HttpResponse(status=400)

    print(f"📥 Webhook received: {json.dumps(data, indent=2)}")

    # Handle message (includes text messages, join/leave service messages, polls)
    if "message" in data:
        print("📥 Update type: message")
        message_data = data["message"]
        chat_type = message_data.get("chat", {}).get("type", "")
        if chat_type == "private":
            _handle_dm(message_data)
        else:
            _handle_message(message_data)

    # Handle poll state updates
    if "poll" in data:
        print("📥 Update type: poll")
        _handle_poll_data(data["poll"])

    # Handle individual poll answers
    if "poll_answer" in data:
        print("📥 Update type: poll_answer")
        _handle_poll_answer(data["poll_answer"])

    # Handle chat member updates (join/leave via admin view)
    if "chat_member" in data:
        print("📥 Update type: chat_member")
        _handle_chat_member(data["chat_member"])

    # Handle bot's own membership updates (e.g. bot added to a group)
    if "my_chat_member" in data:
        print("📥 Update type: my_chat_member")
        _handle_my_chat_member(data["my_chat_member"])

    # Handle callback queries (inline keyboard button presses)
    if "callback_query" in data:
        print("📥 Update type: callback_query")
        _handle_callback_query(data["callback_query"])

    print("📥✅ Webhook processed successfully")
    return JsonResponse(dict(ok=True))


def _get_attending_window():
    now = datetime.now(timezone.utc)

    # Calculate this week's Monday 7:00 UTC (when poll is sent)
    days_since_monday = now.weekday()
    monday_7am = now.replace(
        hour=7, minute=0, second=0, microsecond=0
    ) - timedelta(days=days_since_monday)

    # If we haven't reached Monday 7:00 UTC yet this week, use last week's
    if now < monday_7am:
        monday_7am = monday_7am - timedelta(weeks=1)

    # Calculate the corresponding Friday 7:00 UTC (when summary is sent)
    friday_7am = monday_7am + timedelta(days=4)

    # If we're past Friday 7:00 UTC, we're outside the attending window
    if now >= friday_7am:
        return None

    return monday_7am


def _get_this_weeks_attending_person_ids(node):
    if not node.group:
        return set()

    window_start = _get_attending_window()
    if window_start is None:
        return set()

    attending_ids = PollAnswer.objects.filter(
        poll__node=node,
        poll__created__gte=window_start,
        yes=True,
    ).values_list("person_id", flat=True)

    return set(attending_ids)


def _get_this_weeks_attending_count(node):
    if not node.group:
        return 0

    window_start = _get_attending_window()
    if window_start is None:
        return 0

    return PollAnswer.objects.filter(
        poll__node=node,
        poll__created__gte=window_start,
        yes=True,
    ).count()


def _cors_response(response):
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def api_nodes(request):
    if request.method == "OPTIONS":
        response = HttpResponse()
        return _cors_response(response)

    if request.method != "GET":
        return HttpResponse(status=405)

    nodes = Node.objects.all().order_by(F("established").asc(nulls_last=True))

    nodes_data = []
    node_attending_map = {}

    for node in nodes:
        node_data = dict(
            id=node.name_slug,
            name=node.name,
            emoji=node.emoji,
            url=node.signup_url,
            established=node.established,
            location=node.location,
            timezone=node.timezone,
            disabled=node.disabled,
            attending_count=_get_this_weeks_attending_count(node),
        )
        nodes_data.append(node_data)
        node_attending_map[node.id] = _get_this_weeks_attending_person_ids(
            node
        )

    # Build map of person -> nodes they've attended (answered yes to any poll)
    person_attended_nodes = {}
    poll_answers = PollAnswer.objects.filter(
        yes=True,
        poll__node__isnull=False,
    ).select_related("poll__node")

    for pa in poll_answers:
        if pa.person_id not in person_attended_nodes:
            person_attended_nodes[pa.person_id] = set()
        person_attended_nodes[pa.person_id].add(pa.poll.node_id)

    # Build map of person -> their most recent chatted node (fallback)
    # First get all groups that have a node
    groups_with_nodes = {
        node.group_id: node.id for node in nodes if node.group_id
    }

    person_last_chatted_node = {}
    group_memberships = GroupPerson.objects.filter(
        left=False,
        last_message_at__isnull=False,
        group_id__in=groups_with_nodes.keys(),
    ).order_by("person_id", "-last_message_at")
    for gp in group_memberships:
        if gp.person_id not in person_last_chatted_node:
            person_last_chatted_node[gp.person_id] = groups_with_nodes[
                gp.group_id
            ]

    # Build node lookup by id
    node_lookup = {node.id: node for node in nodes}

    # Get people with public profile who have attended OR chatted
    candidate_person_ids = set(person_attended_nodes.keys()) | set(
        person_last_chatted_node.keys()
    )
    people = (
        Person.objects.filter(
            id__in=candidate_person_ids,
            privacy=False,
        )
        .filter(Q(first_name__gt="") | Q(username_x__gt=""))
        .distinct()
    )

    people_data = []
    for person in people:
        attended_node_ids = person_attended_nodes.get(person.id, set())
        person_nodes = []
        is_attending_any = False

        if attended_node_ids:
            # Use nodes they've attended via poll
            for node_id in attended_node_ids:
                node = node_lookup.get(node_id)
                if not node:
                    continue
                attending = person.id in node_attending_map.get(node_id, set())
                if attending:
                    is_attending_any = True
                person_nodes.append(
                    dict(
                        id=node.name_slug,
                        attending=attending,
                    )
                )
        else:
            # Fallback to last chatted node
            fallback_node_id = person_last_chatted_node.get(person.id)
            if fallback_node_id:
                node = node_lookup.get(fallback_node_id)
                if node:
                    person_nodes.append(
                        dict(
                            id=node.name_slug,
                            attending=False,
                        )
                    )

        if not person_nodes:
            continue

        person_nodes.sort(key=lambda n: not n["attending"])

        person_data = dict(
            display_name=_sanitize_for_html(person.first_name),
            username_x=(
                _sanitize_for_html(person.username_x)
                if person.username_x
                else None
            ),
            nodes=person_nodes,
        )
        if person.bio:
            person_data["bio"] = _sanitize_for_html(person.bio)

        people_data.append(
            (is_attending_any, person.first_name.lower(), person_data)
        )

    people_data.sort(key=lambda x: (not x[0], x[1]))
    people_list = [p[2] for p in people_data]

    node_group_ids = list(
        Node.objects.filter(group__isnull=False).values_list(
            "group_id", flat=True
        )
    )
    global_group = Group.objects.filter(
        telegram_id=int(HACKA_NETWORK_GLOBAL_CHAT_ID)
    ).first()
    if global_group:
        node_group_ids.append(global_group.id)
    people_count = (
        Person.objects.filter(
            groupperson__group_id__in=node_group_ids,
            groupperson__left=False,
        )
        .distinct()
        .count()
    )
    stats = dict(people_count=people_count)

    response = JsonResponse(
        dict(nodes=nodes_data, people=people_list, stats=stats)
    )
    return _cors_response(response)


def _find_node_by_slug(node_slug):
    for node in Node.objects.filter(disabled=False):
        if node.name_slug == node_slug:
            return node
    return None


def api_node_detail(request, node_slug):
    if request.method == "OPTIONS":
        return _cors_response(HttpResponse())
    if request.method != "GET":
        return HttpResponse(status=405)

    node = _find_node_by_slug(node_slug)
    if not node:
        return _cors_response(HttpResponse(status=404))

    node_data = dict(
        id=node.name_slug,
        name=node.name,
        emoji=node.emoji,
        url=node.signup_url,
        established=node.established,
        location=node.location,
        timezone=node.timezone,
        disabled=node.disabled,
        attending_count=_get_this_weeks_attending_count(node),
    )

    node_attending_ids = _get_this_weeks_attending_person_ids(node)

    person_attended_nodes = {}
    poll_answers = PollAnswer.objects.filter(
        yes=True,
        poll__node=node,
    ).select_related("poll__node")
    for pa in poll_answers:
        if pa.person_id not in person_attended_nodes:
            person_attended_nodes[pa.person_id] = set()
        person_attended_nodes[pa.person_id].add(pa.poll.node_id)

    person_last_chatted_node = {}
    if node.group_id:
        group_memberships = GroupPerson.objects.filter(
            left=False,
            last_message_at__isnull=False,
            group_id=node.group_id,
        ).order_by("person_id", "-last_message_at")
        for gp in group_memberships:
            if gp.person_id not in person_last_chatted_node:
                person_last_chatted_node[gp.person_id] = node.id

    candidate_person_ids = set(person_attended_nodes.keys()) | set(
        person_last_chatted_node.keys()
    )
    people = (
        Person.objects.filter(
            id__in=candidate_person_ids,
            privacy=False,
        )
        .filter(Q(first_name__gt="") | Q(username_x__gt=""))
        .distinct()
    )

    people_data = []
    for person in people:
        attended_node_ids = person_attended_nodes.get(person.id, set())
        attending = person.id in node_attending_ids

        if attended_node_ids:
            if node.id not in attended_node_ids:
                if person.id not in person_last_chatted_node:
                    continue
        else:
            if person.id not in person_last_chatted_node:
                continue

        person_data = dict(
            display_name=_sanitize_for_html(person.first_name),
            username_x=(
                _sanitize_for_html(person.username_x)
                if person.username_x
                else None
            ),
            nodes=[dict(id=node.name_slug, attending=attending)],
        )
        if person.bio:
            person_data["bio"] = _sanitize_for_html(person.bio)

        people_data.append((attending, person.first_name.lower(), person_data))

    people_data.sort(key=lambda x: (not x[0], x[1]))
    people_list = [p[2] for p in people_data]

    node_group_ids = [node.group_id] if node.group_id else []
    people_count = (
        Person.objects.filter(
            groupperson__group_id__in=node_group_ids,
            groupperson__left=False,
        )
        .distinct()
        .count()
    )
    stats = dict(people_count=people_count)

    two_weeks_ago = django_timezone.now() - timedelta(weeks=2)
    photos = MeetupPhoto.objects.filter(
        node=node,
        created__gte=two_weeks_ago,
    )[:12]
    photos_data = []
    for photo in photos:
        photos_data.append(
            dict(
                id=photo.id,
                node_name=node.name,
                node_emoji=node.emoji,
                created=photo.created.isoformat(),
            )
        )

    response = JsonResponse(
        dict(
            node=node_data,
            people=people_list,
            stats=stats,
            photos=photos_data,
        )
    )
    return _cors_response(response)


def api_recent_photos(request):
    if request.method == "OPTIONS":
        return _cors_response(HttpResponse())
    if request.method != "GET":
        return HttpResponse(status=405)

    two_weeks_ago = django_timezone.now() - timedelta(weeks=2)
    photos = MeetupPhoto.objects.filter(
        created__gte=two_weeks_ago
    ).select_related("node")[:12]

    photos_data = []
    for photo in photos:
        photos_data.append(
            dict(
                id=photo.id,
                node_name=photo.node.name,
                node_emoji=photo.node.emoji,
                created=photo.created.isoformat(),
            )
        )

    return _cors_response(JsonResponse(dict(photos=photos_data)))


def api_photo_image(request, photo_id):
    if request.method != "GET":
        return HttpResponse(status=405)

    photo = MeetupPhoto.objects.filter(id=photo_id).first()
    if not photo:
        return HttpResponse(status=404)

    response = HttpResponse(photo.image_data, content_type="image/jpeg")
    response["Cache-Control"] = "public, max-age=86400"
    return _cors_response(response)
