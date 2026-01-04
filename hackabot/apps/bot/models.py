import uuid

from django.db import models


class Group(models.Model):
    telegram_id = models.BigIntegerField(unique=True)
    display_name = models.CharField(max_length=255, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.display_name or str(self.telegram_id)

    def to_dict(self):
        return dict(
            id=self.id,
            telegram_id=self.telegram_id,
            display_name=self.display_name,
            created=self.created.isoformat(),
        )


class Person(models.Model):
    telegram_id = models.BigIntegerField(unique=True)
    is_bot = models.BooleanField(default=False)
    first_name = models.CharField(max_length=255, blank=True)
    username = models.CharField(max_length=255, blank=True)
    privacy = models.BooleanField(default=True)
    username_x = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True)
    onboarded = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "People"

    def __str__(self):
        if self.username:
            return f"@{self.username}"
        return self.first_name or str(self.telegram_id)

    def to_dict(self):
        return dict(
            id=self.id,
            telegram_id=self.telegram_id,
            is_bot=self.is_bot,
            first_name=self.first_name,
            username=self.username,
            privacy=self.privacy,
            username_x=self.username_x,
            bio=self.bio,
            onboarded=self.onboarded,
        )


class GroupPerson(models.Model):
    group = models.ForeignKey("Group", on_delete=models.CASCADE)
    person = models.ForeignKey("Person", on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    left = models.BooleanField(default=False)
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["group", "person"]
        verbose_name = "Group membership"
        verbose_name_plural = "Group memberships"

    def __str__(self):
        status = " (left)" if self.left else ""
        return f"{self.person} in {self.group}{status}"

    def to_dict(self):
        return dict(
            id=self.id,
            group_id=self.group_id,
            person_id=self.person_id,
            created=self.created.isoformat(),
            left=self.left,
            last_message_at=(
                self.last_message_at.isoformat()
                if self.last_message_at
                else None
            ),
        )


class Node(models.Model):
    slug = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    group = models.ForeignKey(
        "Group", on_delete=models.CASCADE, null=True, blank=True
    )
    created = models.DateTimeField(auto_now_add=True)
    established = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Year this node was established (e.g. 2019)",
    )
    name = models.CharField(max_length=100)
    emoji = models.CharField(max_length=10, blank=True)
    signup_url = models.URLField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    timezone = models.CharField(
        max_length=50, default="UTC", help_text="e.g. Europe/Paris"
    )

    def __str__(self):
        return f"{self.emoji} {self.name}" if self.emoji else self.name

    def to_dict(self):
        return dict(
            id=self.id,
            slug=str(self.slug),
            group_id=self.group_id,
            created=self.created.isoformat(),
            established=self.established,
            name=self.name,
            emoji=self.emoji,
            signup_url=self.signup_url,
            location=self.location,
            timezone=self.timezone,
        )


class Event(models.Model):
    TYPE_INTROS = "intros"
    TYPE_LUNCH = "lunch"
    TYPE_DEMOS = "demos"
    TYPE_DRINKS = "drinks"

    TYPE_CHOICES = [
        (TYPE_INTROS, "Intros"),
        (TYPE_LUNCH, "Lunch"),
        (TYPE_DEMOS, "Demos"),
        (TYPE_DRINKS, "Drinks"),
    ]

    node = models.ForeignKey("Node", on_delete=models.CASCADE)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    time = models.TimeField(help_text="Time of day for this event")
    where = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ["node", "type"]

    def __str__(self):
        return f"{self.node.name} {self.get_type_display()} @ {self.time}"

    def to_dict(self):
        return dict(
            id=self.id,
            node_id=self.node_id,
            type=self.type,
            time=self.time.isoformat(),
            where=self.where,
        )


class Poll(models.Model):
    telegram_id = models.CharField(max_length=100, unique=True)
    created = models.DateTimeField(auto_now_add=True)
    node = models.ForeignKey(
        "Node", on_delete=models.CASCADE, null=True, blank=True
    )
    question = models.CharField(max_length=500)
    yes_count = models.PositiveIntegerField(default=0)
    no_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.question[:50]}..."

    def to_dict(self):
        return dict(
            id=self.id,
            telegram_id=self.telegram_id,
            created=self.created.isoformat(),
            node_id=self.node_id,
            question=self.question,
            yes_count=self.yes_count,
            no_count=self.no_count,
        )


class PollAnswer(models.Model):
    poll = models.ForeignKey("Poll", on_delete=models.CASCADE)
    person = models.ForeignKey("Person", on_delete=models.CASCADE)
    yes = models.BooleanField()

    class Meta:
        unique_together = ["poll", "person"]

    def __str__(self):
        answer = "Yes" if self.yes else "No"
        return f"{self.person} answered {answer}"

    def to_dict(self):
        return dict(
            id=self.id,
            poll_id=self.poll_id,
            person_id=self.person_id,
            yes=self.yes,
        )


class Message(models.Model):
    telegram_id = models.BigIntegerField()
    group = models.ForeignKey("Group", on_delete=models.CASCADE)
    person = models.ForeignKey(
        "Person", on_delete=models.CASCADE, null=True, blank=True
    )
    date = models.DateTimeField()
    text = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["telegram_id", "group"]

    def __str__(self):
        return f"Message {self.telegram_id} in {self.group}"

    def to_dict(self):
        return dict(
            id=self.id,
            telegram_id=self.telegram_id,
            group_id=self.group_id,
            person_id=self.person_id,
            date=self.date.isoformat(),
            text=self.text,
            created=self.created.isoformat(),
        )


class ActivityDay(models.Model):
    person = models.ForeignKey("Person", on_delete=models.CASCADE)
    group = models.ForeignKey("Group", on_delete=models.CASCADE)
    date = models.DateField()
    message_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ["person", "group", "date"]
        verbose_name = "Activity day"
        verbose_name_plural = "Activity days"

    def __str__(self):
        return f"{self.person} in {self.group} on {self.date}: {self.message_count}"

    def to_dict(self):
        return dict(
            id=self.id,
            person_id=self.person_id,
            group_id=self.group_id,
            date=self.date.isoformat(),
            message_count=self.message_count,
        )
