# hackabot

@the_hackabot Telegram bot for the Hacka* network.

## Public API

Base URL: `https://bot.hacka.network`

- `GET /api/nodes/` — all nodes, public people, and global stats
- `GET /api/nodes/<node_slug>/` — single node, its people, node stats, and recent photos
- `GET /api/photos/` — most recent photos from all nodes (max 12)
- `GET /api/photos/<id>/image` — raw JPEG image for a photo

The `node_slug` is the node name lowercased with spaces removed (e.g.
"Hackawatu" → `hackawatu`), matching the hashtag format used in Telegram.

## Prerequisites

- Python 3.14+
- uv

## Setup

1. Copy `.env` and fill in your values:
   ```
   HACKABOT_ENV=dev
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_WEBHOOK_URL=https://your-domain.com/webhook/telegram/
   TELEGRAM_WEBHOOK_SECRET=your_random_secret
   DJANGO_SECRET_KEY=your_secret_key
   SENTRY_DSN=your_sentry_dsn
   ```

   `TELEGRAM_WEBHOOK_SECRET` is used to verify that webhook requests are
   actually from Telegram. Generate a random string (1-256 chars, A-Za-z0-9_-).

   In production, set `HACKABOT_ENV=production` and set `DATABASE_URL`.

2. Install dependencies:
   ```
   uv sync
   ```

3. Run migrations:
   ```
   uv run python manage.py migrate
   ```

4. Create a superuser (to access admin):
   ```
   uv run python manage.py createsuperuser
   ```

## Running locally

Run the web server:
```
uv run python manage.py runserver 8089
```

Run the worker (scheduler):
```
uv run python manage.py hackabot_worker
```

## How it works

- **Webhook**: Receives Telegram updates (messages, polls, join/leave events)
- **Worker**: Runs on a schedule, sending polls (Mondays) and event reminders (Thursdays)
- All scheduling is driven by the Node's timezone and Event times in the database

## Testing

Run the test suite:

```bash
uv run pytest
```

The test suite uses `responses` to mock all Telegram API calls, so no external
services are required to run tests.

## Deploying to Heroku

```sh
heroku login
heroku git:remote -a hackabot
git push heroku main
```

## Heroku Setup

```sh
heroku create --buildpack heroku/python --region us hackabot
heroku certs:auto:enable
heroku buildpacks:add heroku/pgbouncer
heroku addons:create heroku-postgresql:essential-0

# Backups
heroku pg:backups:schedule DATABASE_URL --at '02:00 America/Los_Angeles'

# Config
heroku config:set HACKABOT_ENV=production \
    DJANGO_SECRET_KEY=your_secret_key \
    TELEGRAM_BOT_TOKEN=your_bot_token \
    TELEGRAM_WEBHOOK_URL=https://bot.hacka.network/webhook/telegram/ \
    TELEGRAM_WEBHOOK_SECRET=your_random_secret \
    SENTRY_DSN=your_sentry_dsn \
    PGBOUNCER_DEFAULT_POOL_SIZE=10 PGBOUNCER_RESERVE_POOL_SIZE=5 \
    PGBOUNCER_MAX_CLIENT_CONN=500 \
    PGBOUNCER_LOG_CONNECTIONS=0 PGBOUNCER_LOG_DISCONNECTIONS=0

# Custom domain
heroku domains:add bot.hacka.network
# Then add a CNAME record in your DNS pointing bot.hacka.network
# to the DNS target shown by: heroku domains

# Initial deploy
git push heroku main

# Create a superuser
heroku run python manage.py createsuperuser

# Make sure it doesn't sleep.
heroku dyno:type worker=basic web=basic
heroku ps:scale worker=1 web=1

# Check that it works
open https://bot.hacka.network
```

To see the logs:

```sh
heroku logs --tail
```
