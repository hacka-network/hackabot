import json
from datetime import datetime, timezone

from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import (
    ActivityDay,
    Group,
    GroupPerson,
    Message,
    Person,
    Poll,
    PollAnswer,
)


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


def _handle_my_chat_member(my_chat_member_data):
    chat_data = my_chat_member_data.get("chat")
    _get_or_create_group(chat_data)


@csrf_exempt
@require_POST
def telegram_webhook(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    print(f"[webhook] received update: {json.dumps(data, indent=2)}")

    # Handle message (includes text messages, join/leave service messages, polls)
    if "message" in data:
        _handle_message(data["message"])

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
