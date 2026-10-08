"""Parsing helpers: amounts ('25k', '1.5m'), dates, and natural-language quick entry."""
import calendar
import re
from datetime import date, timedelta

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def parse_amount(s, allow_zero=False):
    """'25k' -> 25000, '1.5m' -> 1500000, '150,000' -> 150000. None if invalid."""
    if s is None:
        return None
    t = str(s).lower().replace(",", "").replace("ugx", "").replace("shs", "").strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|m|thousand|million)?", t)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2)
    if unit in ("k", "thousand"):
        n *= 1_000
    elif unit in ("m", "million"):
        n *= 1_000_000
    if n == 0 and not allow_zero:
        return None
    return n


def parse_days(s):
    """Effort estimate -> days. '3' -> 3.0, '2.5d' -> 2.5, '6h' -> 0.75 (8-hour day).
    Blank or zero -> None (no estimate). Raises ValueError on junk."""
    t = (s or "").strip().lower()
    for a, b in (("days", "d"), ("day", "d"), ("hours", "h"), ("hrs", "h"), ("hr", "h")):
        t = t.replace(a, b)
    if not t:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(d|h)?", t)
    if not m:
        raise ValueError("Estimate must look like 3, 2.5 or 6h")
    n = float(m.group(1)) / (8 if m.group(2) == "h" else 1)
    if n > 365:
        raise ValueError("That estimate is over a year of work")
    return n or None


def parse_date(s, today=None):
    """Accepts '', 'today', 'tomorrow', 'yesterday', 2026-10-15, '15 Oct'. Returns ISO str or None."""
    today = today or date.today()
    t = (s or "").strip().lower()
    if t in ("", "today"):
        return today.isoformat()
    if t == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if t == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(t).isoformat()
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})\s*([a-z]{3})[a-z]*(?:\s+(\d{4}))?", t)
    if m and m.group(2) in MONTHS:
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            return date(year, MONTHS[m.group(2)], int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- quick entry
EXPENSE_VERBS = {"spent", "spend", "paid", "pay", "bought", "buy", "purchased", "gave"}
INCOME_VERBS = {"received", "receive", "got", "earned", "earn", "deposit", "deposited"}
STOP = {"on", "for", "from", "via", "using", "with", "by", "to", "the", "a", "an", "my",
        "in", "at", "through", "into", "of", "me", "i", "ugx", "shs", "shillings"}

METHODS = [
    (r"\bmobile\s+money\b|\bmomo\b|\bmtn\b|\bairtel(?:\s+money)?\b|\bmobile\b|\bmm\b", "Mobile Money"),
    (r"\bbank\b", "Bank"),
    (r"\bcash\b", "Cash"),
]

SOURCES = {
    "salary": "Salary", "freelance": "Freelance", "client": "Freelance", "gig": "Freelance",
    "business": "Business", "gift": "Gift",
}

CATEGORY_WORDS = {
    "Food": "lunch dinner breakfast supper food snack snacks rolex groceries restaurant coffee tea juice meal",
    "Transport": "boda taxi uber bolt safeboda fuel transport matatu fare parking petrol",
    "Rent": "rent",
    "Internet": "internet wifi data airtime",
    "Bills": "electricity umeme water nwsc yaka bill bills tv",
    "Shopping": "shopping clothes shoes shirt dress",
    "Entertainment": "movie movies netflix party drinks beer game concert cinema",
    "Work": "work equipment software tools",
}
WORD_TO_CAT = {w: c for c, ws in CATEGORY_WORDS.items() for w in ws.split()}


def parse_quick(text, today=None):
    """Turn 'spent 25k on lunch cash' into a transaction dict. ok=False if no amount found."""
    today = today or date.today()
    low = " " + (text or "").lower().strip() + " "
    out = {"ok": False, "type": "expense", "amount": None, "category": "Others",
           "source": "Other", "description": "", "method": None, "date": today.isoformat()}

    m = re.search(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(k|m)?(?![a-z0-9])", low)
    if not m:
        return out
    amt = parse_amount(m.group(1) + (m.group(2) or ""))
    if not amt:
        return out
    out["amount"] = amt
    low = low[:m.start()] + " " + low[m.end():]

    for pat, name in METHODS:
        if re.search(pat, low):
            out["method"] = name
            low = re.sub(pat, " ", low)
            break

    for word, delta in (("yesterday", -1), ("today", 0), ("tomorrow", 1)):
        if re.search(r"\b%s\b" % word, low):
            out["date"] = (today + timedelta(days=delta)).isoformat()
            low = re.sub(r"\b%s\b" % word, " ", low)

    tokens = re.findall(r"[a-z']+", low)
    has_exp = any(t in EXPENSE_VERBS for t in tokens)
    has_inc = any(t in INCOME_VERBS for t in tokens)
    source = next((SOURCES[t] for t in tokens if t in SOURCES), None)
    tokens = [t for t in tokens if t not in EXPENSE_VERBS | INCOME_VERBS | STOP]

    if has_inc or (source and not has_exp):
        out["type"] = "income"
        out["source"] = source or "Other"
        tokens = [t for t in tokens if t not in SOURCES]
        out["description"] = " ".join(tokens)
    else:
        out["type"] = "expense"
        tokens = [t for t in tokens if t not in ("other", "others")]
        for t in tokens:
            if t in WORD_TO_CAT:
                out["category"] = WORD_TO_CAT[t]
                break
        out["description"] = " ".join(tokens)

    d = out["description"]
    out["description"] = d[:1].upper() + d[1:]
    out["ok"] = True
    return out