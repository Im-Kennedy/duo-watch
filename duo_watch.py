"""Emails you when an iPhone Duo becomes available for Apple Store pickup near a ZIP.

Data source: Duo Stock (https://duo-checker.on-forge.com), a read-only public API.
It refreshes about every 20 minutes (6:30 AM-9:30 PM EDT), so polling faster is pointless.
Availability means Apple offered online pickup, NOT a stock count. Confirm on apple.com.

Config via environment variables:
  ZIP            ZIP to search from (default 97301, Salem OR)
  RADIUS_MILES   search radius (default 75)
  STORAGE_GB     optional: 256 / 512 / 1024 / 2048
  COLOR          optional: "Night Sky" or "Star White"
  SMTP_USER      your Gmail address
  SMTP_PASS      Gmail APP PASSWORD (not your normal password)
  EMAIL_TO       where to send alerts (default: SMTP_USER)
  STATE_FILE     where to remember what you've been alerted about (default state.json)
"""
import json
import os
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

BASE = "https://duo-checker.on-forge.com/api/v1/nearby"
ZIP = os.environ.get("ZIP", "97301")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")


def fetch():
    params = {
        "zip": ZIP,
        "available_only": "1",
        "radius_miles": os.environ.get("RADIUS_MILES", "75"),
        "limit": "50",
    }
    if os.environ.get("STORAGE_GB"):
        params["storage_gb"] = os.environ["STORAGE_GB"]
    if os.environ.get("COLOR"):
        params["color"] = os.environ["COLOR"]
    req = urllib.request.Request(
        BASE + "?" + urllib.parse.urlencode(params),
        headers={"Accept": "application/json", "User-Agent": "duo-watch/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # 429 = rate limited, 503 = no check published yet, 422 = bad ZIP.
        print(f"API returned HTTP {e.code}; skipping this run", file=sys.stderr)
        return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_state(keys):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(keys), f)


def send_email(subject, body):
    user = os.environ["SMTP_USER"]
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = os.environ.get("EMAIL_TO", user)
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, os.environ["SMTP_PASS"])
        s.send_message(msg)


def send_push(title, body):
    """Free push notification to your phone via ntfy.sh (install the ntfy app, subscribe to your topic)."""
    topic = os.environ["NTFY_TOPIC"]
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "iphone"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30).close()


def notify(subject, body, short_subject, short_body):
    """Full details go to the ntfy push; a short version goes to email/text."""
    sent = False
    if os.environ.get("NTFY_TOPIC"):
        send_push(subject, body)
        sent = True
    if os.environ.get("SMTP_USER"):
        send_email(short_subject, short_body)  # EMAIL_TO can be a carrier text gateway address
        sent = True
    if not sent:
        print("No NTFY_TOPIC or SMTP_USER set; nothing to notify with", file=sys.stderr)


def main():
    data = fetch()
    if data is None:
        return
    if data.get("is_stale"):
        print("Warning: latest check is stale", file=sys.stderr)

    current = {}
    for store in data.get("stores", []):
        for p in store.get("products", []):
            if p.get("pickup_status") == "available":
                key = f"{store['store_id']}|{p['part_number']}"
                current[key] = (store, p)

    already_sent = load_state()
    new_keys = [k for k in current if k not in already_sent]

    if new_keys:
        lines = []
        for k in new_keys:
            store, p = current[k]
            lines.append(
                f"- {p['color']} {p['storage_label']} at {store['name']} "
                f"({store['city']}, {store['state_code']}), "
                f"{store['distance_miles']} mi: {p.get('pickup_message', 'available')}\n"
                f"  {store['url']}"
            )
        body = (
            "iPhone Duo pickup is showing as available:\n\n"
            + "\n".join(lines)
            + f"\n\nData collected: {data.get('run_finished_at')} UTC."
            "\nThis means Apple offered online pickup, not a guaranteed stock count. "
            "Confirm on apple.com before you go."
        )
        short_items = [
            f"{current[k][1]['color']} {current[k][1]['storage_label']} "
            f"at {current[k][0]['name']} ({current[k][0]['distance_miles']} mi)"
            for k in new_keys
        ]
        short_body = "Duo available: " + "; ".join(short_items) + ". Check apple.com now."
        notify(f"iPhone Duo available near {ZIP}", body, "Duo available", short_body)
        print(f"Emailed about {len(new_keys)} new item(s)")
    else:
        print("Nothing new")

    # Remember only what is currently available, so you get re-alerted
    # if something sells out and later comes back.
    save_state(set(current))


if __name__ == "__main__":
    main()
