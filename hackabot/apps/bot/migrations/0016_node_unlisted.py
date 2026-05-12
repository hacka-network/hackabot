from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bot", "0015_group_last_yearly_summary_sent_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="unlisted",
            field=models.BooleanField(
                default=False,
                help_text="Hide from the public nodes API listing",
            ),
        ),
    ]
