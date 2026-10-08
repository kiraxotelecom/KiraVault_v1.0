"""Deadline + bill reminders. Pure logic: no Kivy imports, so it can be tested anywhere."""
import json
from datetime import date, timedelta

from db import short
from workdb import DONE, balance

LEVELS = {"overdue": 0, "today": 1, "soon": 2}


def _when(d, today):
    n = (date.fromisoformat(d) - today).days
    if n < 0:
        return "overdue", f"{-n} day{'s' if n != -1 else ''} overdue"
    if n == 0:
        return "today", "due today"
    if n == 1:
        return "soon", "due tomorrow"
    return "soon", f"due in {n} days"


def collect(work, money, today=None, lead=1):
    """Items needing attention: unfinished projects and unpaid bills that are overdue,
    due today, or due within `lead` days. Most urgent first."""
    today = today or date.today()
    last = (today + timedelta(days=lead)).isoformat()
    out = []
    for p in work.projects():
        d = p["deadline"]
        if p["status"] in DONE or not d or d > last:
            continue
        level, txt = _when(d, today)
        out.append({"key": f"p{p['id']}:{level}", "level": level, "kind": "Project",
                    "text": f"{p['title']} {txt}"})
    for c in money.commitments():
        d = c["due_date"]
        if c["status"] == "paid" or not d or d > last:
            continue
        level, txt = _when(d, today)
        left = c["amount"] - (c["paid_amount"] or 0)
        out.append({"key": f"c{c['id']}:{level}", "level": level, "kind": "Bill",
                    "text": f"{c['title']} (UGX {short(left)}) {txt}"})
    out.sort(key=lambda i: (LEVELS[i["level"]], i["text"]))
    return out


def fresh(db, items, today=None):
    """Items not yet notified today. Sent keys reset each day, so overdue items repeat daily."""
    iso = (today or date.today()).isoformat()
    try:
        sent = json.loads(db.get_setting("rem_sent", "{}"))
    except ValueError:
        sent = {}
    if sent.get("date") != iso:
        sent = {"date": iso, "keys": []}
    return [i for i in items if i["key"] not in sent["keys"]], sent


def mark_sent(db, sent, items):
    sent["keys"] = sent["keys"] + [i["key"] for i in items]
    db.set_setting("rem_sent", json.dumps(sent))


def summary(items):
    n = len(items)
    title = "KiraVault: " + (items[0]["text"] if n == 1 else f"{n} reminders")
    body = "\n".join(i["text"] for i in items[:4]) + (f"\n+{n - 4} more" if n > 4 else "")
    return title, body


def notify(title, message):
    """Android/desktop notification via plyer. Returns True if it was handed to the OS."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name="KiraVault", timeout=10)
        return True
    except Exception:
        return False
