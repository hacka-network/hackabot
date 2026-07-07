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

1. Copy `.env.example` to `.env` and fill in your values:
   ```
   cp .env.example .env
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

## $10k MRR gated group

A private Telegram group whose members are verified to have $10k+ MRR.
The group has "Approve New Members" enabled, so joining via its invite
link creates a join request instead of admitting directly:

1. User taps the invite link → Telegram sends the bot a
   `chat_join_request` update
2. The bot DMs the user asking for a link to their public Stripe MRR
   chart (`https://profile.stripe.com/<company>/<token>`, created via
   Share on a Stripe dashboard chart)
3. If the chart checks out (live-mode MRR chart, recent data, latest
   value ≥ $10k USD-equivalent), the bot approves the join request
   automatically
4. Otherwise (no Stripe, below threshold, or the check fails) the
   request is forwarded to the admin group with Approve/Decline
   buttons for manual review. If the user replies with a screenshot
   or video instead of a link, that media is forwarded to the admins
   too

The group is also promoted to the global group right after the weekly
network stats. The bot fetches the group's invite link itself (via
`getChat`), so no extra config is needed beyond `MRR_10K_CHAT_ID`.

Verification uses an unofficial Stripe endpoint
(`api.stripe.com/v2/xauth_/shareable_metrics/...`); if it ever breaks,
all requests simply fall back to manual review.

Setup:

- The bot must be an **admin** of the gated group with the
  "Invite Users via Link" right — without it, Telegram silently
  doesn't deliver `chat_join_request` updates
- Set `MRR_10K_CHAT_ID` (the gated group) and `MRR_ADMIN_CHAT_ID`
  (the admin review group) in the environment; while unset the
  feature is disabled

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
    MRR_10K_CHAT_ID=your_gated_group_chat_id \
    MRR_ADMIN_CHAT_ID=your_admin_group_chat_id \
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
