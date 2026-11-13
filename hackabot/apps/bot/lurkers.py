import arrow
from django.conf import settings
from django.utils import timezone
from requests import HTTPError

from .models import Group, GroupPerson
from .telegram import get_chat_member_status, remove_chat_member, send

INACTIVE_MONTHS = 6
GRACE_DAYS = 7


def _last_active_at(membership):
    times = [
        membership.created,
        membership.joined_at,
        membership.last_message_at,
    ]
    return max(t for t in times if t)


def _mention(person):
    if person.username:
        return f"@{person.username}", ""
    name = person.first_name or "there"
    return f"[{name}](tg://user?id={person.telegram_id})", "Markdown"


def _plain_name(person):
    if person.username:
        return f"@{person.username}"
    return person.first_name or str(person.telegram_id)


def announce_saved_from_purge(group, person, message_id):
    name = _plain_name(person)
    message = f"🛟 Thank you {name} - you survived the purge"
    try:
        send(
            group.telegram_id,
            message,
            parse_mode="",
            reply_to_message_id=message_id,
        )
    except HTTPError:
        print(f"⚠️ Could not announce {person} was saved from the purge")
        return
    print(f"🛟 {person} saved from the lurker purge")


def _is_removable(membership):
    status = get_chat_member_status(
        membership.group.telegram_id, membership.person.telegram_id
    )
    if status in ("left", "kicked"):
        print(f"👻 {membership.person} already gone, marking as left")
        membership.left = True
        membership.inactivity_warned_at = None
        membership.save(update_fields=["left", "inactivity_warned_at"])
        return False
    return status not in ("administrator", "creator")


def _warn(membership, now):
    cutoff = arrow.get(now).shift(months=-INACTIVE_MONTHS).datetime
    if _last_active_at(membership) >= cutoff:
        return False
    if not _is_removable(membership):
        return False

    mention, parse_mode = _mention(membership.person)
    message = (
        f"👋 Hey {mention}, you haven't said anything in here for a"
        f" while, still alive? Sorry but lurkers get auto-removed after"
        f" {INACTIVE_MONTHS} months of inactivity, so say something in"
        f" the next {GRACE_DAYS} days to stay in."
    )
    try:
        send(membership.group.telegram_id, message, parse_mode=parse_mode)
    except HTTPError:
        print(f"⚠️ Could not warn {membership.person} about inactivity")
        return False

    membership.inactivity_warned_at = now
    membership.save(update_fields=["inactivity_warned_at"])
    print(f"⚠️ Warned {membership.person} about inactivity")
    return True


def _remove(membership, now):
    warned_on = membership.inactivity_warned_at.date()
    if (now.date() - warned_on).days < GRACE_DAYS:
        return False
    if not _is_removable(membership):
        return False

    try:
        remove_chat_member(
            membership.group.telegram_id, membership.person.telegram_id
        )
    except HTTPError:
        print(f"⚠️ Could not remove lurker {membership.person}")
        return False

    membership.left = True
    membership.inactivity_warned_at = None
    membership.save(update_fields=["left", "inactivity_warned_at"])
    print(f"🧹 Removed lurker {membership.person}")

    if settings.MRR_ADMIN_CHAT_ID:
        name = _plain_name(membership.person)
        try:
            send(
                settings.MRR_ADMIN_CHAT_ID,
                f"🧹 Removed {name} from the $10k MRR group for"
                f" {INACTIVE_MONTHS} months inactivity",
                parse_mode="",
            )
        except HTTPError:
            print("⚠️ Could not tell admins about lurker removal")
    return True


def process_lurkers():
    if not settings.MRR_10K_CHAT_ID:
        return dict(warned=0, removed=0)
    group = Group.objects.filter(telegram_id=settings.MRR_10K_CHAT_ID).first()
    if not group:
        return dict(warned=0, removed=0)

    now = timezone.now()
    memberships = GroupPerson.objects.filter(
        group=group, left=False, person__is_bot=False
    ).select_related("group", "person")

    warned = 0
    removed = 0
    for membership in memberships:
        if membership.inactivity_warned_at:
            if _remove(membership, now):
                removed += 1
        elif _warn(membership, now):
            warned += 1
    return dict(warned=warned, removed=removed)
