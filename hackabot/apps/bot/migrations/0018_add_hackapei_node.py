from django.db import migrations

NODE_DATA = dict(
    name="Hackapei",
    emoji="🇹🇼",
    location="Taipei, Taiwan",
    timezone="Asia/Taipei",
    established=2026,
    signup_url="https://hackapei.com",
)


def add_node(apps, schema_editor):
    Node = apps.get_model("bot", "Node")
    Node.objects.get_or_create(
        name=NODE_DATA["name"],
        defaults=dict(
            emoji=NODE_DATA["emoji"],
            location=NODE_DATA["location"],
            timezone=NODE_DATA["timezone"],
            established=NODE_DATA["established"],
            signup_url=NODE_DATA["signup_url"],
        ),
    )


def remove_node(apps, schema_editor):
    Node = apps.get_model("bot", "Node")
    Node.objects.filter(name=NODE_DATA["name"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("bot", "0017_poll_is_attendance"),
    ]

    operations = [
        migrations.RunPython(add_node, remove_node),
    ]
