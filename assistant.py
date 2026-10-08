"""KiraVault Insights: turns your money and work data into plain-language observations and
chart data. Pure logic, no Kivy imports, so it can be tested anywhere.

It is a rule-based assistant: everything is computed on the phone from your own records, so
it works offline and nothing leaves the device.

Each insight is a dict:
    level  "warn" (needs attention) | "tip" (suggestion) | "good" (well done) | "info"
    title  short headline
    text   one or two sentences
    score  sort weight inside its level (bigger = more important)
"""
import calendar
import math
from datetime import date, timedelta

from db import add_months, short
from workdb import DONE, balance, level as load_level

ORDER = {"warn": 0, "tip": 1, "good": 2, "info": 3}

TIPS = {
    "Transport": "Consider using your bike more over short distances.",
    "Food": "Cooking a few more meals at home could bring this down.",
    "Shopping": "Try waiting a few days before non-essential purchases.",
    "Entertainment": "Pick your one or two favourite outings and skip the rest this month.",
    "Bills": "Check whether any bill or subscription can be trimmed.",
    "Internet": "Compare data bundles; a cheaper plan may cover what you actually use.",
    "Work": "Make sure these costs are charged to a client or tracked as business expenses.",
    "Others": "Look at what's in 'Others' and give repeating items their own category.",
}

MIN_CHANGE = 0.20          # ignore swings smaller than 20%
MIN_AMOUNT = 5_000         # ...or smaller than UGX 5K


# ------------------------------------------------------------------ helpers
def _ord(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _dm(d):
    return f"{d.day} {calendar.month_abbr[d.month]}"


def _ins(level, title, text, score=0):
    return {"level": level, "title": title, "text": text, "score": score}


def _sorted(items):
    return sorted(items, key=lambda i: (ORDER[i["level"]], -i["score"]))


def _first_of(d):
    return d.replace(day=1)


def _months_back(today, n):
    """Oldest first: [(first_day_of_month, 'YYYY-MM', 'Oct'), ...] ending with this month."""
    out = []
    for i in range(n - 1, -1, -1):
        d = add_months(_first_of(today), -i)
        out.append((d, d.strftime("%Y-%m"), calendar.month_abbr[d.month]))
    return out


def _merge_slices(pairs, keep=4):
    """[(name, amount)] -> top `keep` plus an 'Other' slice, each with a percentage."""
    pairs = [(n, a) for n, a in pairs if a > 0]
    total = sum(a for _, a in pairs)
    if not total:
        return []
    pairs.sort(key=lambda p: -p[1])
    if len(pairs) > keep + 1:
        rest = sum(a for _, a in pairs[keep:])
        pairs = pairs[:keep] + [("Other", rest)]
    return [{"name": n, "amount": a, "pct": a / total * 100} for n, a in pairs]


def _cat_to_day(db, ym, day):
    rows = db.q("SELECT category, SUM(amount) AS s FROM transactions WHERE type='expense' "
                "AND substr(date,1,7)=? AND CAST(substr(date,9,2) AS INTEGER)<=? GROUP BY category",
                (ym, day))
    return {(r["category"] or "Others"): r["s"] for r in rows}


# ------------------------------------------------------------------ financial
def financial(db, today=None):
    today = today or date.today()
    ym = today.strftime("%Y-%m")
    dim = calendar.monthrange(today.year, today.month)[1]
    prev_first = _first_of(today) - timedelta(days=1)
    pym, pdim = prev_first.strftime("%Y-%m"), prev_first.day

    cur = db.spending_by_category(ym)
    cur_d = dict(cur)
    total = sum(cur_d.values())
    prev_full = dict(db.spending_by_category(pym))
    prev_total = sum(prev_full.values())
    prev_same = _cat_to_day(db, pym, min(today.day, pdim))
    prev_same_total = sum(prev_same.values())
    ins = []

    # 1. category vs the same point last month (fair mid-month comparison)
    if today.day >= 4:
        for cat, amt in cur:
            p = prev_same.get(cat, 0)
            if p < MIN_AMOUNT or abs(amt - p) < MIN_AMOUNT:
                continue
            ch = (amt - p) / p
            tip = TIPS.get(cat, "")
            if ch >= MIN_CHANGE:
                ins.append(_ins("warn", f"{cat} spending is up",
                                f"Your {cat.lower()} spending is {ch * 100:.0f}% higher than at this point "
                                f"last month (UGX {short(amt)} vs {short(p)}). {tip}".strip(),
                                amt - p))
            elif ch <= -MIN_CHANGE:
                ins.append(_ins("good", f"{cat} spending is down",
                                f"You're spending {-ch * 100:.0f}% less on {cat.lower()} than at this point "
                                f"last month (UGX {short(amt)} vs {short(p)}). Nice work.", p - amt))

    # 2. overall pace
    if today.day >= 7 and total > 0 and prev_total > 0:
        proj = total / today.day * dim
        ch = (proj - prev_total) / prev_total
        if ch >= 0.15:
            ins.append(_ins("warn", "Spending pace is high",
                            f"At this pace you'll spend about UGX {short(proj)} this month, "
                            f"{ch * 100:.0f}% more than last month (UGX {short(prev_total)}).", proj - prev_total))
        elif ch <= -0.15:
            ins.append(_ins("good", "Spending pace is low",
                            f"At this pace you'll spend about UGX {short(proj)} this month, "
                            f"{-ch * 100:.0f}% less than last month (UGX {short(prev_total)}).", prev_total - proj))

    # 3. budgets
    for bd in db.budgets():
        lim = bd["limit_amount"]
        used = cur_d.get(bd["category"], 0)
        if not lim:
            continue
        if used >= lim:
            ins.append(_ins("warn", f"{bd['category']} budget exceeded",
                            f"You've spent UGX {short(used)} of your UGX {short(lim)} {bd['category'].lower()} "
                            f"budget, UGX {short(used - lim)} over.", 1e9 + used - lim))
            continue
        hit = math.ceil(lim / (used / today.day)) if used > 0 and today.day >= 5 else None
        if hit and hit <= dim:
            ins.append(_ins("warn", f"{bd['category']} budget at risk",
                            f"At this pace you'll use up your {bd['category'].lower()} budget around the "
                            f"{_ord(hit)}, before the month ends.", 1e8 + used))
        elif used / lim >= 0.8:
            ins.append(_ins("tip", f"{bd['category']} budget nearly used",
                            f"{used / lim * 100:.0f}% of your {bd['category'].lower()} budget is gone.", used))

    # 4. income vs spending
    mt = db.month_totals(ym)
    if mt["income"] > 0:
        ratio = mt["expenses"] / mt["income"]
        if ratio >= 0.9:
            ins.append(_ins("warn", "Almost all income spent",
                            f"You've spent {ratio * 100:.0f}% of this month's income "
                            f"(UGX {short(mt['expenses'])} of {short(mt['income'])}).", ratio * 1e6))
        elif ratio <= 0.7 and today.day >= 10:
            ins.append(_ins("good", "You're keeping money",
                            f"So far you've kept {100 - ratio * 100:.0f}% of this month's income "
                            f"(UGX {short(mt['income'] - mt['expenses'])}).", 1 - ratio))

    # 5. one category dominating
    if len(cur) >= 2 and total >= 50_000 and cur[0][1] / total >= 0.4:
        ins.append(_ins("info", f"{cur[0][0]} leads your spending",
                        f"{cur[0][0]} is {cur[0][1] / total * 100:.0f}% of everything you've spent this month.",
                        cur[0][1] / total))

    # 6. cash position
    s = db.summary()
    if s["shortfall"] > 0:
        ins.append(_ins("warn", "Commitments exceed your money",
                        f"What you've promised is UGX {short(s['shortfall'])} more than what you have.", 1e10))
    f = db.forecast()
    if f["lowest"] < 0:
        dip = next((e for e in f["events"] if e[3] < 0), None)
        when = f" around {_dm(dip[0])}" if dip else ""
        ins.append(_ins("warn", "Cash may run short",
                        f"Your balance is projected to dip to UGX {short(f['lowest'])}{when} once upcoming "
                        f"bills are paid.", 1e9))
    if s["overdue_n"]:
        ins.append(_ins("warn", "Overdue payments",
                        f"{s['overdue_n']} commitment{'s' if s['overdue_n'] != 1 else ''} overdue, "
                        f"UGX {short(s['overdue_sum'])} in total.", 1e10 - 1))

    # 7. money that should have arrived
    for e in db.expected():
        d = e["expected_date"]
        if d and d < today.isoformat():
            late = (today - date.fromisoformat(d)).days
            who = e["client"] or e["project"] or e["source"]
            ins.append(_ins("warn", "Payment is late",
                            f"UGX {short(e['amount'])} from {who} was expected {late} day"
                            f"{'s' if late != 1 else ''} ago. Time to follow up.", 1e7 + late))

    # 8. savings goals
    goals = [g for g in db.goals() if g["target"] and g["saved"] < g["target"]]
    if goals:
        g = max(goals, key=lambda g: g["saved"] / g["target"])
        if g["saved"] > 0:
            ins.append(_ins("info", f"Goal: {g['name']}",
                            f"You're {g['saved'] / g['target'] * 100:.0f}% of the way there "
                            f"(UGX {short(g['saved'])} of {short(g['target'])}).", 0))

    # fallbacks
    if not cur and not any(i["level"] == "warn" for i in ins):
        if prev_total > 0:
            top = max(prev_full.items(), key=lambda kv: kv[1])
            ins.append(_ins("info", "A fresh month",
                            f"Last month you spent UGX {short(prev_total)}, mostly on {top[0].lower()}. "
                            f"Add expenses as you go and I'll compare as the month moves.", 0))
        else:
            ins.append(_ins("info", "Getting started",
                            "Record a few expenses and I'll start spotting patterns and sending tips.", 0))
    elif not ins:
        ins.append(_ins("good", "All looks steady",
                        "Nothing unusual in your spending compared with last month.", 0))

    # chart data
    months = []
    for first, key, label in _months_back(today, 6):
        t = db.month_totals(key)
        months.append((label, t["income"], t["expenses"]))
    daily = []
    since = (today - timedelta(days=13)).isoformat()
    by_day = {r["date"]: r["s"] for r in db.q(
        "SELECT date, SUM(amount) AS s FROM transactions WHERE type='expense' AND date>=? GROUP BY date",
        (since,))}
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        daily.append((str(d.day), by_day.get(d.isoformat(), 0.0)))

    return {"insights": _sorted(ins), "breakdown": _merge_slices(cur), "total": total,
            "prev_same_total": prev_same_total, "months": months, "daily": daily,
            "avg_daily": sum(a for _, a in daily) / 14, "month_name": calendar.month_name[today.month]}


# ------------------------------------------------------------------ work
def _paid_to_day(w, ym, day):
    r = w.q("SELECT COALESCE(SUM(amount),0) AS s FROM payments WHERE substr(paid_on,1,7)=? "
            "AND CAST(substr(paid_on,9,2) AS INTEGER)<=?", (ym, day))
    return r[0]["s"]


BOOKING_TYPES = ("video shooting", "photography")


def _is_booking(p):
    """A job that happens on a set day (a shoot, an event), so 0% progress beforehand is normal.
    True for shoot/photo work types, or when the start and deadline are the same day."""
    same_day = bool(p["start_date"]) and p["start_date"] == p["deadline"]
    return same_day or (p["work_type"] or "").strip().lower() in BOOKING_TYPES


def work(w, today=None):
    today = today or date.today()
    ym = today.strftime("%Y-%m")
    prev_first = _first_of(today) - timedelta(days=1)
    pym = prev_first.strftime("%Y-%m")
    projs = [p for p in w.projects() if p["status"] != "Cancelled"]
    active = [p for p in projs if p["status"] not in DONE]
    ins = []

    # overdue
    late = [p for p in active if p["deadline"] and p["deadline"] < today.isoformat()]
    if late:
        names = ", ".join(p["title"] for p in late[:2]) + (f" +{len(late) - 2} more" if len(late) > 2 else "")
        ins.append(_ins("warn", "Projects past their deadline",
                        f"{len(late)} project{'s are' if len(late) != 1 else ' is'} overdue: {names}. "
                        f"Finish them or agree a new date with the client.", 1e9 + len(late)))

    # at risk (only for work with a "finish by" date; a booked shoot/event on a set day is not "behind")
    for p in active:
        d = p["deadline"]
        if not d or d < today.isoformat():
            continue
        n = (date.fromisoformat(d) - today).days
        if _is_booking(p):
            if n <= 3:
                ins.append(_ins("info", "Coming up",
                                f"{p['title']} is {'today' if n == 0 else 'tomorrow' if n == 1 else f'in {n} days'}"
                                f" ({calendar.day_abbr[date.fromisoformat(d).weekday()]} {_dm(date.fromisoformat(d))}).",
                                5e7 - n))
            continue
        if n <= 3 and (p["progress"] or 0) < 50 and p["status"] != "Review":
            ins.append(_ins("warn", "Deadline at risk",
                            f"{p['title']} is due {'today' if n == 0 else 'tomorrow' if n == 1 else f'in {n} days'} "
                            f"but is only {p['progress'] or 0}% done.", 1e8 - n))

    # workload next 14 days
    days = [today + timedelta(days=i) for i in range(14)]
    ld = w.load(days)
    scores = [ld[d][0] for d in days]
    heavy = [d for d, s in zip(days, scores) if s >= 1.0]
    light = [d for d, s in zip(days, scores) if s < 0.5]
    if heavy:
        ins.append(_ins("warn", "You're overbooked",
                        f"{len(heavy)} of the next 14 days need more than a full day of work "
                        f"(first: {calendar.day_abbr[heavy[0].weekday()]} {_dm(heavy[0])}). "
                        f"Think twice before taking another job, or move a deadline.", 1e7 + len(heavy)))
    elif active and len(light) >= 7:
        ins.append(_ins("good", "You have room for more",
                        f"{len(light)} of the next 14 days are light. Use 'Can I take another job?' in Work "
                        f"to see if a new project fits.", len(light)))

    # completed this month
    st = w.month_stats(today.year, today.month)
    if st["completed"]:
        n = len(st["completed"])
        ins.append(_ins("good", "Projects delivered",
                        f"You've completed {n} project{'s' if n != 1 else ''} this month.", n))

    if not projs:
        ins.append(_ins("info", "Getting started",
                        "Add a project in Work and I'll watch deadlines and workload for you.", 0))
    elif not ins:
        ins.append(_ins("good", "Work is on track",
                        "No late projects or deadlines at risk right now.", 0))

    # chart data
    load = [(str(d.day), s, load_level(s)[2]) for d, s in zip(days, scores)]
    stats = {"active": len(active),
             "due7": sum(1 for p in active if p["deadline"] and p["deadline"] <= (today + timedelta(days=7)).isoformat()),
             "overdue": len(late), "done_month": len(st["completed"])}
    return {"insights": _sorted(ins), "stats": stats, "load": load}
