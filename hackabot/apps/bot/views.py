import html
import json
import re
import zoneinfo
from datetime import datetime, timedelta, timezone

from django.db.models import F, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

BIO_MAX_LENGTH = 500


def _sanitize_for_html(text):
    if not text:
        return text
    return html.escape(text, quote=True)


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
            _onboard_new_member(person, group)

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
        print("📊 Found poll in message, processing...")
        _handle_poll_data(poll_data, group=group)


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

    message = f"✅ Your X/Twitter username has been set to @{username}"

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

    print("📥✅ Webhook processed successfully")
    return JsonResponse(dict(ok=True))


def _calculate_activity_level(node):
    if not node:
        return 0

    try:
        tz = zoneinfo.ZoneInfo(node.timezone)
    except zoneinfo.ZoneInfoNotFoundError:
        tz = zoneinfo.ZoneInfo("UTC")

    now = datetime.now(tz)

    # Find the most recent Thursday at midnight
    days_since_thursday = (now.weekday() - 3) % 7
    thursday_midnight = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=days_since_thursday)

    # Week ends at Thursday 23:59
    thursday_end = thursday_midnight.replace(hour=23, minute=59)

    # If we haven't reached the end of this week yet, go back a week
    if now <= thursday_end:
        last_complete_week_end = thursday_end - timedelta(weeks=1)
    else:
        last_complete_week_end = thursday_end

    # Look back 4 weeks from the end of the last complete week
    four_weeks_before = last_complete_week_end - timedelta(weeks=4)

    # Query polls for this node within this window
    polls = Poll.objects.filter(
        node=node,
        created__gt=four_weeks_before,
        created__lte=last_complete_week_end,
    )

    poll_count = polls.count()
    if poll_count == 0:
        return 0

    total_yes = sum(poll.yes_count for poll in polls)
    average_attendees = total_yes / poll_count

    # Scale: 8+ attendees = 10, 0 = 0, linear in between
    activity_level = min(10, round(average_attendees * 10 / 8))
    return activity_level


def _get_this_weeks_attending_person_ids(node):
    if not node.group:
        return set()

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    attending_ids = PollAnswer.objects.filter(
        poll__node=node,
        poll__created__gte=seven_days_ago,
        yes=True,
    ).values_list("person_id", flat=True)

    return set(attending_ids)


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
            id=str(node.slug),
            name=node.name,
            emoji=node.emoji,
            url=node.signup_url,
            established=node.established,
            location=node.location,
            timezone=node.timezone,
            activity_level=_calculate_activity_level(node),
        )
        nodes_data.append(node_data)
        node_attending_map[node.id] = _get_this_weeks_attending_person_ids(
            node
        )

    people = (
        Person.objects.filter(
            groupperson__left=False,
            groupperson__group__node__isnull=False,
            privacy=False,
        )
        .filter(Q(first_name__gt="") | Q(username_x__gt=""))
        .prefetch_related("groupperson_set__group__node_set")
        .distinct()
    )

    people_data = []
    for person in people:
        person_nodes = []
        is_attending_any = False

        for gp in person.groupperson_set.all():
            if gp.left:
                continue
            for node in gp.group.node_set.all():
                attending = person.id in node_attending_map.get(node.id, set())
                if attending:
                    is_attending_any = True
                person_nodes.append(
                    dict(
                        id=str(node.slug),
                        attending=attending,
                    )
                )

        if not person_nodes:
            continue

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

    response = JsonResponse(dict(nodes=nodes_data, people=people_list))
    return _cors_response(response)
