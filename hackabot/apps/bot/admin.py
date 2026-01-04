from datetime import timedelta

from django.contrib import admin
from django.db.models import Sum
from django.utils import timezone

from .models import (
    ActivityDay,
    Event,
    Group,
    GroupPerson,
    Message,
    Node,
    Person,
    Poll,
    PollAnswer,
)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "telegram_id",
        "display_name",
        "messages_7d",
        "messages_30d",
        "created",
    ]
    search_fields = ["telegram_id", "display_name"]
    ordering = ["-created"]

    def messages_7d(self, obj):
        cutoff = timezone.now().date() - timedelta(days=7)
        result = ActivityDay.objects.filter(
            group=obj, date__gte=cutoff
        ).aggregate(total=Sum("message_count"))
        return result["total"] or 0

    messages_7d.short_description = "Msgs (7d)"

    def messages_30d(self, obj):
        cutoff = timezone.now().date() - timedelta(days=30)
        result = ActivityDay.objects.filter(
            group=obj, date__gte=cutoff
        ).aggregate(total=Sum("message_count"))
        return result["total"] or 0

    messages_30d.short_description = "Msgs (30d)"


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["id", "telegram_id", "first_name", "username", "is_bot"]
    list_filter = ["is_bot"]
    search_fields = ["telegram_id", "first_name", "username"]
    ordering = ["-id"]


@admin.register(GroupPerson)
class GroupPersonAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "group",
        "person",
        "last_message_at",
        "messages_7d",
        "messages_30d",
        "left",
        "created",
    ]
    list_filter = ["left", "group"]
    search_fields = [
        "person__first_name",
        "person__username",
        "group__display_name",
    ]
    ordering = ["-last_message_at"]
    raw_id_fields = ["group", "person"]

    def messages_7d(self, obj):
        cutoff = timezone.now().date() - timedelta(days=7)
        result = ActivityDay.objects.filter(
            group=obj.group, person=obj.person, date__gte=cutoff
        ).aggregate(total=Sum("message_count"))
        return result["total"] or 0

    messages_7d.short_description = "Msgs (7d)"

    def messages_30d(self, obj):
        cutoff = timezone.now().date() - timedelta(days=30)
        result = ActivityDay.objects.filter(
            group=obj.group, person=obj.person, date__gte=cutoff
        ).aggregate(total=Sum("message_count"))
        return result["total"] or 0

    messages_30d.short_description = "Msgs (30d)"


class EventInline(admin.TabularInline):
    model = Event
    extra = 1
    fields = ["type", "time", "where"]


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "emoji",
        "location",
        "timezone",
        "messages_7d",
        "messages_30d",
        "established",
        "group",
        "created",
    ]
    search_fields = ["name", "location"]
    ordering = ["name"]
    raw_id_fields = ["group"]
    inlines = [EventInline]

    def messages_7d(self, obj):
        if not obj.group:
            return "-"
        cutoff = timezone.now().date() - timedelta(days=7)
        result = ActivityDay.objects.filter(
            group=obj.group, date__gte=cutoff
        ).aggregate(total=Sum("message_count"))
        return result["total"] or 0

    messages_7d.short_description = "Msgs (7d)"

    def messages_30d(self, obj):
        if not obj.group:
            return "-"
        cutoff = timezone.now().date() - timedelta(days=30)
        result = ActivityDay.objects.filter(
            group=obj.group, date__gte=cutoff
        ).aggregate(total=Sum("message_count"))
        return result["total"] or 0

    messages_30d.short_description = "Msgs (30d)"


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["id", "node", "type", "time", "where"]
    list_filter = ["type", "node"]
    ordering = ["node", "type"]
    raw_id_fields = ["node"]


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "telegram_id",
        "node",
        "question",
        "yes_count",
        "no_count",
        "created",
    ]
    list_filter = ["node"]
    search_fields = ["telegram_id", "question"]
    ordering = ["-created"]
    raw_id_fields = ["node"]


@admin.register(PollAnswer)
class PollAnswerAdmin(admin.ModelAdmin):
    list_display = ["id", "poll", "person", "yes"]
    list_filter = ["yes"]
    ordering = ["-id"]
    raw_id_fields = ["poll", "person"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "telegram_id",
        "group",
        "person",
        "date",
        "text_preview",
    ]
    list_filter = ["group"]
    search_fields = ["telegram_id", "text"]
    ordering = ["-date"]
    raw_id_fields = ["group", "person"]

    def text_preview(self, obj):
        if obj.text:
            return obj.text[:50] + "..." if len(obj.text) > 50 else obj.text
        return ""

    text_preview.short_description = "Text"


@admin.register(ActivityDay)
class ActivityDayAdmin(admin.ModelAdmin):
    list_display = ["id", "date", "group", "person", "message_count"]
    list_filter = ["group", "date"]
    search_fields = ["person__first_name", "person__username"]
    ordering = ["-date", "-message_count"]
    raw_id_fields = ["group", "person"]
    date_hierarchy = "date"
