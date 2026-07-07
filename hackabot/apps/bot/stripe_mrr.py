import json
import re
from datetime import date, timedelta

import requests

REQUEST_TIMEOUT = 30

STRIPE_PROFILE_RE = re.compile(r"profile\.stripe\.com/([\w-]+)/([\w-]+)")
SHAREABLE_METRICS_URL = (
    "https://api.stripe.com/v2/xauth_/shareable_metrics/{slug}/{token}"
)
MRR_CHART_IDENTIFIER = "bento_mrr_volume"
MIN_MRR_USD = 10000
MAX_DATA_AGE_DAYS = 60

# ponytail: hardcoded approximate rates, only gate auto-approval —
# humans review anything below threshold. Decimal currencies only.
USD_RATES = dict(
    usd=1.0,
    eur=1.17,
    gbp=1.35,
    cad=0.73,
    aud=0.66,
    nzd=0.60,
    chf=1.25,
    sek=0.10,
    nok=0.10,
    dkk=0.16,
)


def extract_stripe_link(text):
    match = STRIPE_PROFILE_RE.search(text or "")
    if not match:
        return None
    return match.group(1), match.group(2)


def verify_mrr(slug, token):
    url = SHAREABLE_METRICS_URL.format(slug=slug, token=token)
    print(f"📈 Fetching Stripe shareable metrics: {url}")
    try:
        resp = requests.get(
            url,
            headers={"stripe-version": "unsafe-development"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        chart_identifier = data["chart_identifier"]
        if chart_identifier != MRR_CHART_IDENTIFIER:
            return False, f"chart is {chart_identifier}, not MRR"

        if not data["livemode"]:
            return False, "chart is in test mode"

        config = json.loads(data["chart_configuration"])
        currency = config["currency"].lower()
        rate = USD_RATES.get(currency)
        if rate is None:
            return False, f"unknown currency {currency}"

        points = json.loads(data["metric_data"])
        latest = points[-1]
        latest_date = date.fromisoformat(latest["start_time"])
        age_limit = date.today() - timedelta(days=MAX_DATA_AGE_DAYS)
        if latest_date < age_limit:
            return False, f"latest data point is stale ({latest_date})"

        mrr_usd = latest["total"] / 100 * rate
        if mrr_usd < MIN_MRR_USD:
            return False, f"MRR is ${mrr_usd:,.0f}, below ${MIN_MRR_USD:,}"

        return True, f"MRR verified at ${mrr_usd:,.0f}"
    except (requests.RequestException, KeyError, ValueError, IndexError):
        return False, "could not fetch or parse Stripe metrics"
