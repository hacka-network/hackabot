---
name: investigate
description: Open-ended investigation of bugs and problems in the hackabot production environment. Inspect Django models via the Heroku prod shell, query Heroku Postgres (read-only), check Heroku addons/config/releases/dynos, read Heroku logs, and cross-reference with optional specialized skills (check-sentry, check-cloudflare) if available. Pass a description of the issue to investigate, or run with no argument for a quick prod health snapshot. Strictly read-only — never makes changes, only reports findings and suggests next steps for the user to approve.
user-invocable: true
disable-model-invocation: true
argument-hint: [description of the issue]
---

# /investigate

Diagnose production issues in `hackabot` — model state, DB data, Heroku infrastructure, addons, config, logs, releases, whatever the user is seeing. If one of the optional specialized skills (`/check-sentry`, `/check-cloudflare`) is available and fits the question better, recommend it rather than duplicating its work. See "Optional specialized skills" below for how to handle the case where they aren't installed.

**CRITICAL: THIS SKILL IS STRICTLY READ-ONLY. DO NOT MAKE ANY CHANGES.**

Only investigate, report findings, and suggest next steps. Do NOT run any command that mutates state. When in doubt, don't run it — stop and ask the user.

Forbidden while operating under this skill:

- **Django shell**: no `.save()`, `.create()`, `.update()`, `.delete()`, `bulk_create`, `bulk_update`, `get_or_create`, `update_or_create`, `raw()` with anything other than `SELECT`, no `.connection.cursor().execute()` with DML/DDL, no calling service functions or worker tasks that mutate, no `transaction.atomic()` blocks doing writes.
- **Heroku CLI**: no `heroku ps:restart`, `ps:scale`, `ps:stop`, `ps:kill`, `ps:resize`, `config:set`, `config:unset`, `releases:rollback`, `maintenance:on/off`, `addons:create/destroy/upgrade`, `pg:reset`, `pg:psql` with anything other than `SELECT`, `redis:cli`, `run` / `run:detached` for anything other than the read-only shell pattern below.
- **Files**: no edits to any file in the project. No code changes, commits, or PRs.
- **HTTP**: no `POST/PUT/PATCH/DELETE` against any production system.

The user's argument is: `$ARGUMENTS`

## Configuration

- **Heroku CLI** — must be logged in locally (`heroku auth:token` returns non-empty). If not, tell the user to run `heroku login` and stop.
- **Heroku app**: `hackabot`
- **Local shell**: `uv run python manage.py shell -c "<code>"` (for reproducing issues locally — same DB schema, local data)

## Running Django shell commands on production

Use this pattern — and only this pattern — to run Python on the prod Django shell:

```bash
heroku run --app hackabot --no-tty -- python manage.py shell <<'PYEOF'
# python code here
PYEOF
```

**IMPORTANT**: `heroku run` does NOT support `-c` flags. You MUST use the heredoc pattern above. Set timeout to 120000ms for these commands — `heroku run` spins up a one-off dyno and can take 10–30s before the code actually runs. (The Heroku dyno uses the buildpack's `python`, not `uv`, which is local-only.)

Prefer a single heredoc with several prints over many separate `heroku run` invocations — each invocation pays the dyno spin-up cost.

## Querying Heroku Postgres (read-only)

For DB-level investigation (locks, slow queries, index health, cache hit, bloat) use Heroku's pg plugin. All of these are read-only and safe:

```bash
heroku pg:info --app hackabot           # size, version, connections, followers
heroku pg:ps --app hackabot             # currently running queries
heroku pg:locks --app hackabot          # locks being held
heroku pg:blocking --app hackabot       # queries blocking other queries
heroku pg:long-running-queries --app hackabot
heroku pg:outliers --app hackabot       # slowest queries by total time (pg_stat_statements)
heroku pg:cache-hit --app hackabot      # heap + index cache hit rates
heroku pg:index-usage --app hackabot    # index usage per table
heroku pg:bloat --app hackabot          # table/index bloat estimates
heroku pg:diagnose --app hackabot       # aggregated health check
heroku pg:credentials --app hackabot    # connection info only (no password)
```

For ad-hoc SELECTs — ONLY when the user has explicitly asked, and ONLY with `SELECT`:

```bash
heroku pg:psql --app hackabot -c "SELECT count(*) FROM bot_group;"
```

Never run `pg:psql` with `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/GRANT/REVOKE/VACUUM FULL` or anything that takes locks. If an investigation needs a destructive query, stop and describe what you'd run so the user can do it themselves.

## Querying Heroku Redis (read-only)

```bash
heroku redis:info --app hackabot         # memory, clients, hits/misses, connected
heroku redis:timeout --app hackabot      # idle connection timeout
heroku redis:wait --app hackabot         # status
```

NEVER use `heroku redis:cli` under this skill — it's interactive and can mutate. If the user needs to poke at Redis keys, describe what you'd look for and have them open the CLI themselves.

## General Heroku inspection (read-only)

```bash
heroku ps --app hackabot                 # dyno state, uptime since last restart
heroku releases --app hackabot           # deploy history (look for a recent release before the issue started)
heroku releases:info <VERSION> --app hackabot
heroku addons --app hackabot             # addons + plans
heroku config --app hackabot             # env vars (⚠ contains secrets — do not paste raw output back to the user; summarise)
heroku config:get <KEY> --app hackabot   # single value
heroku apps:info --app hackabot          # stack, region, buildpacks, collaborators
heroku maintenance --app hackabot        # check, do NOT toggle
heroku domains --app hackabot
heroku buildpacks --app hackabot
heroku features --app hackabot
```

When printing `heroku config` output, redact secrets — show key names and indicate whether each is set, rather than dumping raw values.

## Heroku logs

```bash
heroku logs --app hackabot --num 1500                             # most you can pull (snapshot)
heroku logs --app hackabot --tail                                 # stream live (bound it with a timeout)
heroku logs --app hackabot --source app --dyno web.1 --num 1500
heroku logs --app hackabot --source app --dyno worker.1 --num 1500
heroku logs --app hackabot --ps router --num 1500
```

**Caveats**: Heroku's logplex buffer is capped at ~1500 lines — often only 5–10 minutes on a busy app. For anything older, cross-reference Sentry tracebacks (`/check-sentry`, if available) or dig directly into the code path. `heroku logs` is only the right tool when you need the *most recent* few minutes or want to `--tail` live.

## Running Django shell commands locally

For schema references, reproducing issues with local data, or iterating on queries before running them against prod:

```bash
uv run python manage.py shell -c "from hackabot.apps.bot.models import Group; print(Group.objects.count())"
```

For multi-line scripts, use a heredoc:

```bash
uv run python manage.py shell <<'PYEOF'
from hackabot.apps.bot.models import Group, Person
print("groups:", Group.objects.count())
print("people:", Person.objects.count())
PYEOF
```

Use this freely — local data is safe to query. It's often faster than bouncing a one-off dyno on prod when you just need to remind yourself of a model's fields.

## Optional specialized skills

`/check-sentry` and `/check-cloudflare` are user-level skills living in `~/.claude/skills/`. They're not guaranteed to be installed on every machine running this skill. Before recommending either, check it actually exists:

```bash
ls ~/.claude/skills/check-sentry/SKILL.md 2>/dev/null && echo "check-sentry available"
ls ~/.claude/skills/check-cloudflare/SKILL.md 2>/dev/null && echo "check-cloudflare available"
```

| If the user is asking about… | If available, prefer | If not available, fall back to |
|---|---|---|
| An exception or error traceback in prod | `/check-sentry` | Sentry web UI; or `heroku logs --num 1500 \| grep -iE "error\|exception\|traceback"` |
| Traffic patterns, rate-limit decisions, WAF events | `/check-cloudflare` | Cloudflare dashboard; or `heroku logs --ps router --num 1500` for Heroku-side view |

If the issue sits across categories, it's fine to run this skill *and* mention the relevant specialized one (gated on its availability). Don't re-implement their logic here.

## Discovering apps, models, and fields at runtime

Don't rely on a static list in this skill — apps and models change. Discover what's actually there when you need to:

Apps live under `hackabot/apps/` — `ls` the directory, or use Django's app registry:

```bash
uv run python manage.py shell -c "
from django.apps import apps
for a in sorted(apps.get_app_configs(), key=lambda a: a.label):
    if a.name.startswith('hackabot.'):
        print(f'{a.label:<20} {a.name}')
"
```

Models in an app:

```bash
uv run python manage.py shell -c "
from django.apps import apps
for m in apps.get_app_config('bot').get_models():
    print(m.__name__)
"
```

Fields on a model (preferred over guessing or scrolling through the file):

```bash
uv run python manage.py shell -c "
from hackabot.apps.bot.models import Group
for f in Group._meta.get_fields():
    print(f.name, type(f).__name__)
"
```

Prefer the local shell for introspection (no dyno spin-up, same schema). Only hit the prod shell when you actually need prod data. When you need to cite precise field semantics (defaults, choices, custom save logic), open the model file — don't guess.

## Investigation workflow

### Step 1: Parse the issue

Read `$ARGUMENTS` carefully and identify:

1. **The subject** — who/what is affected? (Telegram group id, person, model + pk, "the whole bot", etc.)
2. **The symptom** — what's broken or surprising? (error message, missing data, slow response, wrong value, missing summary, etc.)
3. **The time** — when did it start? (just now, "since yesterday", "after the last deploy", etc.)
4. **Expected vs actual** — what should be true vs what is?

If the description is too vague to act on (e.g. "prod is broken"), ask clarifying questions before running anything. Good questions to ask: what endpoint / command, what group / person, what exact error message, when did it start, is it reproducible.

If no argument was provided, treat it as a **quick health snapshot**: run `heroku ps`, `heroku releases --num 5`, `heroku pg:info`, and a brief `heroku logs --num 200 | grep -iE "error|exception|traceback"` and summarise. Suggest `/check-sentry` or `/check-cloudflare` if installed and the snapshot hints at them.

### Step 2: Locate the entity

If the issue names a specific entity (group, person, etc.), find it on prod before anything else:

```bash
heroku run --app hackabot --no-tty -- python manage.py shell <<'PYEOF'
from hackabot.apps.bot.models import Group
g = Group.objects.filter(telegram_id=123456789).first()
print(g, getattr(g, "id", None), getattr(g, "created", None))
PYEOF
```

Try multiple lookup strategies if the first fails (telegram_id, display_name, id, etc.). If the entity genuinely doesn't exist, that itself is often the answer — report it and stop.

### Step 3: Gather the full picture

Pull enough state to understand what's going on, preferably in **one heredoc** to avoid multiple dyno spin-ups. Typical things worth grabbing together:

- The primary entity's full state (use `obj.__dict__` or print specific fields)
- Its related objects (group members, recent messages, etc.)
- Timestamps of relevant actions (`created`, `modified`, `last_*`)
- Counts (how many related rows exist? how many are in each state?)
- Any flag fields relevant to the symptom (`onboarded`, `is_bot`, `privacy`, etc.)

### Step 4: Cross-reference

Depending on the symptom, pull in other angles:

- **Does this correlate with a recent deploy?** → `heroku releases --num 20 --app hackabot`
- **Is the DB unhealthy?** → `heroku pg:info`, `heroku pg:ps`, `heroku pg:blocking`, `heroku pg:outliers`
- **Is an addon in a bad state?** → `heroku addons`, `heroku redis:info`, relevant addon's own CLI
- **Are there errors in logs for this group/endpoint?** → `heroku logs --num 1500 | grep …` (only covers the last few minutes; for older, Sentry)
- **Are there Sentry errors for this window?** → `/check-sentry` if installed; else Sentry web UI
- **Traffic / WAF / rate-limit questions?** → `/check-cloudflare` if installed; else Cloudflare dashboard
- **Dyno/memory/latency spike?** → `heroku ps`, `heroku logs --ps router --num 1500 | grep -iE "H\d\d|R\d\d"`
- **Is behaviour reproducible locally?** → `uv run python manage.py shell -c "..."` with the same query

Only pull the angles that the symptom actually calls for. Don't drag in every cross-reference for every investigation.

### Step 5: Present findings

Structure the report so the user can act on it quickly:

**1. Summary** — one or two sentences: what's wrong, what caused it, confidence level.

**2. Evidence** — the concrete data points you gathered, each with its source (shell query, `heroku pg:ps` output, a log line, etc.). Quote actual values — don't paraphrase.

**3. Root cause** — name the specific code path, task, or upstream event responsible. Point to the file and line(s) if the cause is in the codebase. If uncertain, say so and list the top hypotheses.

**4. Suggested actions** — concrete things the user could do to fix it. Be specific enough that the user can approve and you can execute them in a *follow-up* turn (not inside this skill). Examples:

- "Update `Group.last_weekly_summary_sent_at` on pk=123 to `None`" — describe, don't run
- "Re-run the weekly summary for telegram_id=..."
- "Open a patch to handle the `None` case at `hackabot/apps/bot/views.py:42`"
- "Rotate the `TELEGRAM_BOT_TOKEN` config var"

**IMPORTANT: List suggested actions; do NOT execute them.** If the user approves a change, that's a separate turn outside this skill.

## Safety reminder

If at any point during the investigation the user asks for something that would mutate state (restart a dyno, update a row, roll back a release, clear a Redis key, etc.), stop and tell them:

> That would mutate state and is outside the scope of this read-only skill. Want me to exit the skill and carry it out as a normal coding turn?

And wait for confirmation before doing it.
