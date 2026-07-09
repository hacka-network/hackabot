from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0022_groupperson_welcomed"),
    ]

    operations = [
        migrations.AddField(
            model_name="joinrequest",
            name="product_name",
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
