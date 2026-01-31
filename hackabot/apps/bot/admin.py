from datetime import timedelta

import base64

from django.contrib import admin
from django.db.models import Sum
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    ActivityDay,
    Event,
    Group,
    GroupPerson,
    MeetupPhoto,
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
        "has_node",
        "messages_7d",
        "messages_30d",
        "created",
    ]

    @admin.display(boolean=True, description="Node")
    def has_node(self, obj):
        return obj.node_set.exists()

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
    list_display = [
        "id",
        "telegram_id",
        "first_name",
        "username",
        "username_x",
        "is_bot",
        "privacy",
        "onboarded",
    ]
    list_filter = ["is_bot", "privacy", "onboarded"]
    search_fields = ["telegram_id", "first_name", "username", "username_x"]
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
        "disabled",
        "emoji",
        "slug",
        "location",
        "timezone",
        "has_group",
        "messages_7d",
        "messages_30d",
        "established",
        "signup_url",
        "group",
        "created",
    ]
    list_filter = ["disabled"]
    list_editable = ["disabled"]

    @admin.display(boolean=True, description="Group")
    def has_group(self, obj):
        return obj.group is not None

    search_fields = ["name", "location", "slug"]
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


@admin.register(ActivityDay)
class ActivityDayAdmin(admin.ModelAdmin):
    list_display = ["id", "date", "group", "person", "message_count"]
    list_filter = ["group", "date"]
    search_fields = ["person__first_name", "person__username"]
    ordering = ["-date", "-message_count"]
    raw_id_fields = ["group", "person"]
    date_hierarchy = "date"


@admin.register(MeetupPhoto)
class MeetupPhotoAdmin(admin.ModelAdmin):
    list_display = ["id", "node", "preview_thumb", "uploaded_by", "size_kb", "created"]
    list_filter = ["node"]
    search_fields = ["node__name", "uploaded_by__first_name", "uploaded_by__username"]
    ordering = ["-created"]
    raw_id_fields = ["node", "uploaded_by"]
    readonly_fields = ["telegram_file_id", "preview_large", "size_kb", "created"]

    @admin.display(description="Preview")
    def preview_thumb(self, obj):
        if not obj.image_data:
            return "-"
        b64 = base64.b64encode(obj.image_data).decode("utf-8")
        return format_html(
            '<img src="data:image/jpeg;base64,{}" style="max-height: 50px;">',
            b64,
        )

    @admin.display(description="Preview")
    def preview_large(self, obj):
        if not obj.image_data:
            return "-"
        b64 = base64.b64encode(obj.image_data).decode("utf-8")
        return format_html(
            '<img src="data:image/jpeg;base64,{}" style="max-width: 400px;">',
            b64,
        )

    @admin.display(description="Size")
    def size_kb(self, obj):
        if not obj.image_data:
            return "-"
        return f"{len(obj.image_data) // 1024} KB"
