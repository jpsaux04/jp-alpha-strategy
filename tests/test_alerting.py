#!/usr/bin/env python3
"""ALERT-1 — the alert channel itself.

An untested alarm is an assumption. These exercise provider detection, payload
shape and the disclosure rule WITHOUT touching the network: a channel that
silently posts the wrong shape, or posts the portfolio to a public topic, is
worse than no channel, and neither failure is visible from the outside.
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("APCA_API_KEY_ID", "x")
os.environ.setdefault("APCA_API_SECRET_KEY", "x")

import monitor as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if not cond else ""))


def clear_env():
    for k in ("ALERT_WEBHOOK_URL", "ALERT_WEBHOOK_CHAT_ID", "ALERT_NTFY_FULL",
              "ALERT_WEBHOOK_KIND"):
        os.environ.pop(k, None)


clear_env()

# ── provider detection ──────────────────────────────────────────────────────
for url, want in [
    ("https://hooks.slack.com/services/T/B/x", "slack"),
    ("https://discord.com/api/webhooks/1/abc", "discord"),
    ("https://api.telegram.org/bot123:ABC/sendMessage", "telegram"),
    ("https://ntfy.sh/jp-alpha-secret", "ntfy"),
    ("https://ntfy.example.com/topic", "generic"),
    ("https://alerts.ntfy.sh/topic", "ntfy"),
    ("https://example.com/hook", "generic"),
]:
    check(f"{url.split('/')[2]} detected as {want}", M._provider(url) == want,
          f"got {M._provider(url)}")

#  NEGATIVE: a path that merely mentions ntfy.sh is not ntfy. Host, not substring.
check("a URL whose PATH contains ntfy.sh is not treated as ntfy",
      M._provider("https://example.com/proxy/ntfy.sh/topic") == "generic",
      M._provider("https://example.com/proxy/ntfy.sh/topic"))

# ── ntfy publishes to the ROOT, with the topic in the body ──────────────────
u, kw, kind = M._webhook_request("https://ntfy.sh/jp-alpha-xyz", "hello", "ntfy")
check("ntfy posts to the server root, not the topic URL",
      u == "https://ntfy.sh/", u)
check("ntfy carries the topic in the body",
      kw["json"]["topic"] == "jp-alpha-xyz", kw["json"].get("topic"))
check("ntfy carries the message as `message`",
      kw["json"]["message"] == "hello")

#  A topic URL with no topic cannot work. Fail loudly at build, not silently
#  at delivery time.
for bad in ("https://ntfy.sh/", "https://ntfy.sh", "https://ntfy.sh/a/b"):
    try:
        M._webhook_request(bad, "x", "ntfy")
        ok = False
    except ValueError:
        ok = True
    check(f"a malformed ntfy URL is rejected ({bad})", ok)

# ── the disclosure rule: a public topic never gets the digest ───────────────
DIGEST = ("JP ALPHA V5 — DAILY DIGEST\nEquity: $113,750.14\n"
          "Open positions: AMZN, AAPL\nCash: $41,870.60")


class A:
    def __init__(self, sev, code):
        self.severity, self.code = sev, code
        self.message, self.detail = "msg", None


sent = {}


def fake_post(url, timeout=None, **kw):
    sent.clear()
    sent.update({"url": url, **kw})

    class R:
        status_code = 200
        text = "ok"
    return R()


M.requests.post = fake_post

clear_env()
os.environ["ALERT_WEBHOOK_URL"] = "https://ntfy.sh/jp-alpha-xyz"
res = M.push_webhook(DIGEST, [A("HIGH", "ORPHAN_POSITIONS")])
body = sent["json"]["message"]
check("delivery reports ok", res["state"] == "ok" and res.get("provider") == "ntfy", res)
check("the public topic does NOT receive the equity figure",
      "113,750" not in body and "$" not in body, body)
check("the public topic does NOT receive open symbols",
      "AMZN" not in body and "AAPL" not in body, body)
check("the public topic DOES receive severity and code",
      "HIGH" in body and "ORPHAN_POSITIONS" in body, body)
check("an alert is sent at high priority",
      sent["json"]["priority"] == 4, sent["json"].get("priority"))

#  Opt-in restores the full digest for anyone who prefers detail on-device.
os.environ["ALERT_NTFY_FULL"] = "1"
M.push_webhook(DIGEST, [A("HIGH", "ORPHAN_POSITIONS")])
check("ALERT_NTFY_FULL=1 sends the full digest",
      "113,750" in sent["json"]["message"])
os.environ.pop("ALERT_NTFY_FULL")

#  NEGATIVE: the redaction is ntfy-only. A private Slack channel is not a
#  public topic and must keep the detail it exists to deliver.
os.environ["ALERT_WEBHOOK_URL"] = "https://hooks.slack.com/services/T/B/x"
M.push_webhook(DIGEST, [A("HIGH", "ORPHAN_POSITIONS")])
check("Slack still receives the full digest",
      "113,750" in sent["json"]["text"] and "AMZN" in sent["json"]["text"])

# ── delivery accounting ─────────────────────────────────────────────────────
clear_env()
check("no URL configured is reported, not silently skipped",
      M.push_webhook(DIGEST, [A("HIGH", "X")])["state"] == "not_configured")

os.environ["ALERT_WEBHOOK_URL"] = "https://ntfy.sh/jp-alpha-xyz"
check("no alerts means nothing is sent",
      M.push_webhook(DIGEST, [])["sent"] is False)
check("--test-alert sends even with no alerts",
      M.push_webhook("test", [], force=True)["state"] == "ok")
check("a test is NOT sent at alert priority",
      sent["json"]["priority"] == 3, sent["json"].get("priority"))

#  A revoked topic/hook returns 4xx. requests does not raise on that, and
#  counting it as delivered is how an alarm goes quiet without anyone knowing.
def post_404(url, timeout=None, **kw):
    class R:
        status_code = 404
        text = "not found"
    return R()


M.requests.post = post_404
r = M.push_webhook(DIGEST, [A("HIGH", "X")])
check("a 4xx is reported as rejected, never as delivered",
      r["state"] == "rejected" and "404" in r["detail"], r)


def post_boom(url, timeout=None, **kw):
    raise OSError("network down")


M.requests.post = post_boom
r = M.push_webhook(DIGEST, [A("HIGH", "X")])
check("an unreachable channel is reported, and never raises",
      r["state"] == "unreachable", r)

#  Telegram still needs its chat id, and says so rather than posting nowhere.
os.environ["ALERT_WEBHOOK_URL"] = "https://api.telegram.org/bot1:A/sendMessage"
r = M.push_webhook(DIGEST, [A("HIGH", "X")])
check("telegram without a chat id is misconfigured, not sent",
      r["state"] == "misconfigured", r)

print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"    FAILED: {f}")
    sys.exit(1)
print("  ALERT CHANNEL TESTS PASSED")
