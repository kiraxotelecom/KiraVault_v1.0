"""SQLite data layer + financial model for KiraPlan.

Model:
    Safe to Spend = Available money (Bank + Mobile Money + Cash)
                    - Outstanding commitments (unpaid, due by month-end or overdue)
    Expected income is tracked separately and never counted until received.
"""
import calendar
import csv
import io
import json
import sqlite3
from datetime import date, timedelta

ACCOUNTS = ["Bank", "Mobile Money", "Cash"]
KINDS = ["Rent", "Internet", "Electricity", "Debt", "Subscription",
         "Planned Purchase", "Savings", "Work Expense", "Other"]
EXPENSE_CATEGORIES = ["Food", "Rent", "Bills", "Internet", "Transport",
                      "Shopping", "Entertainment", "Work", "Others"]
INCOME_SOURCES = ["Salary", "Freelance", "Business", "Gift", "Other"]
KIND_TO_CATEGORY = {"Rent": "Rent", "Internet": "Internet", "Electricity": "Bills",
                    "Debt": "Debt", "Subscription": "Entertainment",
                    "Planned Purchase": "Shopping", "Savings": "Savings",
                    "Work Expense": "Work", "Other": "Others"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, balance REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS goals(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, target REAL NOT NULL,
    saved REAL NOT NULL DEFAULT 0, monthly_target REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS commitments(
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, amount REAL NOT NULL,
    paid_amount REAL NOT NULL DEFAULT 0, due_date TEXT, account_id INTEGER,
    recurring TEXT NOT NULL DEFAULT 'None', status TEXT NOT NULL DEFAULT 'pending',
    paid_date TEXT, goal_id INTEGER);
CREATE TABLE IF NOT EXISTS transactions(
    id INTEGER PRIMARY KEY, type TEXT NOT NULL, amount REAL NOT NULL,
    category TEXT DEFAULT '', source TEXT DEFAULT '', description TEXT DEFAULT '',
    account_id INTEGER NOT NULL, transfer_to INTEGER, date TEXT NOT NULL,
    commitment_id INTEGER, goal_id INTEGER);
CREATE TABLE IF NOT EXISTS expected_income(
    id INTEGER PRIMARY KEY, client TEXT DEFAULT '', project TEXT DEFAULT '',
    source TEXT DEFAULT 'Freelance', amount REAL NOT NULL, expected_date TEXT,
    status TEXT NOT NULL DEFAULT 'awaiting', account_id INTEGER);
CREATE TABLE IF NOT EXISTS budgets(category TEXT PRIMARY KEY, limit_amount REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_type ON transactions(type);
"""


# ---------------------------------------------------------------- formatting
def fmt(n):
    return f"{n:,.0f}"


def short(n):
    s = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e6:
        return f"{s}{a / 1e6:.2f}".rstrip("0").rstrip(".") + "M"
    if a >= 1e3:
        return f"{s}{a / 1e3:.1f}".rstrip("0").rstrip(".") + "K"
    return f"{s}{a:.0f}"


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def month_end(d=None):
    d = d or date.today()
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def group_of(kind):
    if kind == "Debt":
        return "Debt"
    if kind == "Savings":
        return "Savings"
    return "Bills"


def _d(s):
    try:
        return date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def commit_state(c, today=None):
    today = today or date.today()
    if c["status"] == "paid":
        return "paid"
    d = _d(c["due_date"])
    if not d:
        return "upcoming"
    delta = (d - today).days
    return "overdue" if delta < 0 else "due_soon" if delta <= 3 else "upcoming"


def due_text(c, today=None):
    today = today or date.today()
    if c["status"] == "paid":
        return "Paid"
    d = _d(c["due_date"])
    if not d:
        return "No due date"
    n = (d - today).days
    if n < 0:
        return f"{-n} day{'s' if n != -1 else ''} overdue"
    if n == 0:
        return "Due today"
    if n == 1:
        return "Due tomorrow"
    return f"Due in {n} days"


def pretty_date(s):
    d = _d(s)
    return f"{d.day} {calendar.month_abbr[d.month]}" if d else "-"


def tx_title(t):
    """'Others' + description shows the description, e.g. 'Phone repair' instead of 'Others'."""
    if t["type"] == "transfer":
        return "Transfer"
    if t["description"]:
        return t["description"]
    if t["type"] == "income":
        return t["source"] or "Income"
    if t["type"] == "saving":
        return t["description"] or "Savings"
    return t["category"] or t["type"].title()


# ------------------------------------------------------------------------ DB
class DB:
    def __init__(self, path):
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        if not self.q("SELECT 1 FROM accounts LIMIT 1"):
            for n in ACCOUNTS:
                self.x("INSERT INTO accounts(name,balance) VALUES(?,0)", (n,))

    def q(self, sql, args=()):
        return self.con.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        cur = self.con.execute(sql, args)
        self.con.commit()
        return cur.lastrowid

    # ---------- settings ----------
    def get_setting(self, key, default=""):
        r = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return r[0]["value"] if r else default

    def set_setting(self, key, value):
        self.x(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ---------- accounts ----------
    def accounts(self):
        return self.q("SELECT * FROM accounts ORDER BY id")

    def account_id(self, name):
        return self.q("SELECT id FROM accounts WHERE name=?", (name,))[0]["id"]

    def account(self, aid):
        return self.q("SELECT * FROM accounts WHERE id=?", (aid,))[0]

    def total_available(self):
        return sum(a["balance"] for a in self.accounts())

    def set_balance(self, aid, balance):
        self.x("UPDATE accounts SET balance=? WHERE id=?", (balance, aid))

    def _need(self, aid, amount):
        a = self.account(aid)
        if a["balance"] < amount:
            raise ValueError(f"Not enough in {a['name']} (UGX {fmt(a['balance'])})")

    # ---------- transactions ----------
    def add_tx(self, type_, amount, account_id, category="", description="", source="",
               date_=None, commitment_id=None, goal_id=None, transfer_to=None):
        tid = self.x(
            "INSERT INTO transactions"
            "(type,amount,category,source,description,account_id,transfer_to,date,commitment_id,goal_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (type_, amount, category, source, description, account_id, transfer_to,
             date_ or date.today().isoformat(), commitment_id, goal_id),
        )
        sign = 1 if type_ == "income" else -1
        self.x("UPDATE accounts SET balance=balance+? WHERE id=?", (sign * amount, account_id))
        if type_ == "transfer" and transfer_to:
            self.x("UPDATE accounts SET balance=balance+? WHERE id=?", (amount, transfer_to))
        return tid

    def delete_tx(self, tid):
        t = self.q("SELECT * FROM transactions WHERE id=?", (tid,))
        if not t:
            return
        t = t[0]
        sign = -1 if t["type"] == "income" else 1
        self.x("UPDATE accounts SET balance=balance+? WHERE id=?",
               (sign * t["amount"], t["account_id"]))
        if t["type"] == "transfer" and t["transfer_to"]:
            self.x("UPDATE accounts SET balance=balance-? WHERE id=?",
                   (t["amount"], t["transfer_to"]))
        if t["goal_id"]:
            self.x("UPDATE goals SET saved=MAX(saved-?,0) WHERE id=?",
                   (t["amount"], t["goal_id"]))
        if t["commitment_id"]:
            self.x(
                "UPDATE commitments SET paid_amount=MAX(paid_amount-?,0), "
                "status='pending', paid_date=NULL WHERE id=?",
                (t["amount"], t["commitment_id"]),
            )
        self.x("DELETE FROM transactions WHERE id=?", (tid,))

    def transactions(self, limit=300):
        return self.q(
            "SELECT t.*, a.name AS account, b.name AS to_account FROM transactions t "
            "JOIN accounts a ON a.id=t.account_id "
            "LEFT JOIN accounts b ON b.id=t.transfer_to "
            "ORDER BY t.date DESC, t.id DESC LIMIT ?",
            (limit,),
        )

    def search_transactions(self, query, limit=300):
        """Filter transactions by description, category, source, or amount."""
        q = (query or "").strip().lower()
        if not q:
            return self.transactions(limit)
        like = f"%{q}%"
        return self.q(
            "SELECT t.*, a.name AS account, b.name AS to_account FROM transactions t "
            "JOIN accounts a ON a.id=t.account_id "
            "LEFT JOIN accounts b ON b.id=t.transfer_to "
            "WHERE LOWER(t.description) LIKE ? "
            "   OR LOWER(t.category) LIKE ? "
            "   OR LOWER(t.source) LIKE ? "
            "   OR CAST(t.amount AS TEXT) LIKE ? "
            "ORDER BY t.date DESC, t.id DESC LIMIT ?",
            (like, like, like, like, limit),
        )

    def transfer(self, from_id, to_id, amount):
        if from_id == to_id:
            raise ValueError("Choose two different accounts")
        self._need(from_id, amount)
        self.add_tx("transfer", amount, from_id, transfer_to=to_id)

    # ---------- commitments ----------
    def add_commitment(self, title, kind, amount, due_date, account_id,
                       recurring="None", goal_id=None):
        return self.x(
            "INSERT INTO commitments"
            "(title,kind,amount,due_date,account_id,recurring,goal_id)"
            " VALUES(?,?,?,?,?,?,?)",
            (title or kind, kind, amount, due_date, account_id, recurring, goal_id),
        )

    def commitments(self):
        return self.q(
            "SELECT c.*, a.name AS account FROM commitments c "
            "LEFT JOIN accounts a ON a.id=c.account_id "
            "ORDER BY (c.status='paid'), c.due_date"
        )

    def update_commitment(self, cid, title, kind, amount, due_date, account_id,
                          recurring="None", goal_id=None):
        c = self.q("SELECT * FROM commitments WHERE id=?", (cid,))[0]
        if c["status"] == "paid":
            raise ValueError("Paid commitments can't be edited")
        paid = c["paid_amount"] or 0
        if amount < paid - 0.5:
            raise ValueError(f"UGX {fmt(paid)} is already paid, so the amount can't be lower")
        was_sav, is_sav = c["kind"] == "Savings", kind == "Savings"
        if paid > 0 and (was_sav != is_sav or (is_sav and goal_id != c["goal_id"])):
            raise ValueError("Payments were already made, so the type or goal can't change")
        if not is_sav:
            goal_id = None
        self.x("UPDATE commitments SET title=?, kind=?, amount=?, due_date=?, account_id=?, "
               "recurring=?, goal_id=? WHERE id=?",
               (title or kind, kind, amount, due_date, account_id, recurring, goal_id, cid))
        if paid > 0 and amount <= paid + 0.5:      # lowered to what's already paid: settled
            self.x("UPDATE commitments SET paid_amount=?, status='paid', paid_date=? WHERE id=?",
                   (amount, date.today().isoformat(), cid))
            self._spawn_next(self.q("SELECT * FROM commitments WHERE id=?", (cid,))[0])

    def delete_commitment(self, cid):
        self.x("DELETE FROM commitments WHERE id=?", (cid,))

    def pay_commitment(self, cid, account_id, amount):
        c = self.q("SELECT * FROM commitments WHERE id=?", (cid,))[0]
        remaining = c["amount"] - c["paid_amount"]
        if amount > remaining + 0.5:
            raise ValueError(f"Only UGX {fmt(remaining)} is still owed")
        self._need(account_id, amount)
        today = date.today().isoformat()
        if c["kind"] == "Savings":
            self.add_tx("saving", amount, account_id, "Savings", c["title"],
                        date_=today, commitment_id=cid, goal_id=c["goal_id"])
            if c["goal_id"]:
                self.x("UPDATE goals SET saved=saved+? WHERE id=?", (amount, c["goal_id"]))
        else:
            self.add_tx("expense", amount, account_id,
                        KIND_TO_CATEGORY.get(c["kind"], "Others"),
                        c["title"], date_=today, commitment_id=cid)
        paid = c["paid_amount"] + amount
        if paid >= c["amount"] - 0.5:
            self.x(
                "UPDATE commitments SET paid_amount=?, status='paid', paid_date=? WHERE id=?",
                (c["amount"], today, cid),
            )
            self._spawn_next(c)
        else:
            self.x("UPDATE commitments SET paid_amount=? WHERE id=?", (paid, cid))

    def _spawn_next(self, c):
        if c["recurring"] not in ("Monthly", "Weekly"):
            return
        base = _d(c["due_date"]) or date.today()
        nxt = add_months(base, 1) if c["recurring"] == "Monthly" else base + timedelta(days=7)
        self.add_commitment(c["title"], c["kind"], c["amount"], nxt.isoformat(),
                            c["account_id"], c["recurring"], c["goal_id"])

    # ---------- expected income ----------
    def add_expected(self, client, project, amount, expected_date, source, account_id):
        return self.x(
            "INSERT INTO expected_income"
            "(client,project,amount,expected_date,source,account_id)"
            " VALUES(?,?,?,?,?,?)",
            (client, project, amount, expected_date, source, account_id),
        )

    def expected(self):
        return self.q(
            "SELECT * FROM expected_income WHERE status='awaiting' ORDER BY expected_date"
        )

    def receive_expected(self, eid, account_id, date_):
        e = self.q("SELECT * FROM expected_income WHERE id=?", (eid,))[0]
        self.add_tx("income", e["amount"], account_id, source=e["source"],
                    description=e["project"] or e["client"], date_=date_)
        self.x("UPDATE expected_income SET status='received' WHERE id=?", (eid,))

    def delete_expected(self, eid):
        self.x("DELETE FROM expected_income WHERE id=?", (eid,))

    # ---------- goals ----------
    def add_goal(self, name, target, saved=0, monthly=0):
        gid = self.x(
            "INSERT INTO goals(name,target,saved,monthly_target) VALUES(?,?,?,?)",
            (name, target, saved, monthly),
        )
        if monthly > 0:
            self.add_commitment(
                f"{name} savings", "Savings", monthly, month_end().isoformat(),
                self.account_id("Bank"), "Monthly", gid,
            )
        return gid

    def goals(self):
        return self.q("SELECT * FROM goals ORDER BY id")

    def contribute(self, goal_id, account_id, amount):
        self._need(account_id, amount)
        g = self.q("SELECT * FROM goals WHERE id=?", (goal_id,))[0]
        self.add_tx("saving", amount, account_id, "Savings", g["name"], goal_id=goal_id)
        self.x("UPDATE goals SET saved=saved+? WHERE id=?", (amount, goal_id))

    def delete_goal(self, gid):
        self.x("UPDATE commitments SET goal_id=NULL WHERE goal_id=?", (gid,))
        self.x("DELETE FROM goals WHERE id=?", (gid,))

    # ---------- budgets / insights ----------
    def set_budget(self, category, limit):
        if limit <= 0:
            self.x("DELETE FROM budgets WHERE category=?", (category,))
        else:
            self.x(
                "INSERT INTO budgets(category,limit_amount) VALUES(?,?) "
                "ON CONFLICT(category) DO UPDATE SET limit_amount=excluded.limit_amount",
                (category, limit),
            )

    def budgets(self):
        return self.q("SELECT * FROM budgets ORDER BY category")

    def spending_by_category(self, ym):
        rows = self.q(
            "SELECT category, SUM(amount) AS s FROM transactions "
            "WHERE type='expense' AND substr(date,1,7)=? "
            "GROUP BY category ORDER BY s DESC",
            (ym,),
        )
        return [(r["category"] or "Others", r["s"]) for r in rows]

    def month_totals(self, ym):
        out = {"income": 0.0, "expenses": 0.0, "savings": 0.0}
        for r in self.q(
            "SELECT type, SUM(amount) AS s FROM transactions "
            "WHERE substr(date,1,7)=? GROUP BY type",
            (ym,),
        ):
            key = {"income": "income", "expense": "expenses", "saving": "savings"}.get(r["type"])
            if key:
                out[key] = r["s"]
        return out

    # ---------- summary / forecast ----------
    def summary(self):
        accts = self.accounts()
        available = sum(a["balance"] for a in accts)
        end = month_end().isoformat()
        groups = {"Bills": 0.0, "Debt": 0.0, "Savings": 0.0}
        pending_bills, pending_bills_n, overdue_n, overdue_sum = 0.0, 0, 0, 0.0
        for c in self.q("SELECT * FROM commitments WHERE status='pending'"):
            rem = c["amount"] - c["paid_amount"]
            g = group_of(c["kind"])
            if not c["due_date"] or c["due_date"] <= end:
                groups[g] += rem
            if g == "Bills":
                pending_bills += rem
                pending_bills_n += 1
            if commit_state(c) == "overdue":
                overdue_n += 1
                overdue_sum += rem
        committed = sum(groups.values())
        return {
            "accounts": accts, "available": available, "groups": groups,
            "committed": committed, "safe": max(available - committed, 0.0),
            "shortfall": max(committed - available, 0.0),
            "expected": sum(e["amount"] for e in self.expected()),
            "pending_bills": pending_bills, "pending_bills_n": pending_bills_n,
            "overdue_n": overdue_n, "overdue_sum": overdue_sum,
            "savings": sum(g["saved"] for g in self.goals()),
        }

    def forecast(self, days=30):
        today = date.today()
        end = today + timedelta(days=days)
        bal = self.total_available()
        events = []
        for c in self.q("SELECT * FROM commitments WHERE status='pending'"):
            d = max(_d(c["due_date"]) or today, today)
            if d <= end:
                events.append((d, c["title"], -(c["amount"] - c["paid_amount"])))
        for e in self.expected():
            d = max(_d(e["expected_date"]) or today, today)
            if d <= end:
                events.append((d, e["project"] or e["client"] or e["source"], e["amount"]))
        events.sort(key=lambda x: (x[0], x[2]))
        out, running, lowest = [], bal, bal
        for d, label, delta in events:
            running += delta
            lowest = min(lowest, running)
            out.append((d, label, delta, running))
        return {"start": bal, "events": out, "lowest": lowest}

    # ---------- export / backup ----------
    def export_csv(self):
        """Return CSV text of all transactions."""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "date", "type", "amount", "category", "source",
                    "description", "account", "to_account", "commitment_id", "goal_id"])
        for t in self.transactions(limit=100000):
            w.writerow([t["id"], t["date"], t["type"], t["amount"], t["category"],
                        t["source"], t["description"], t["account"], t["to_account"] or "",
                        t["commitment_id"] or "", t["goal_id"] or ""])
        return buf.getvalue()

    def export_json(self):
        """Full backup as a JSON-serializable dict."""
        tables = ["accounts", "goals", "commitments", "transactions",
                  "expected_income", "budgets", "settings",
                  "clients", "work_types", "projects", "tasks", "payments"]
        out = {}
        for t in tables:
            out[t] = [dict(r) for r in self.q(f"SELECT * FROM {t}")]
        return out

    def import_json(self, data):
        """Restore from a dict produced by export_json. Replaces all data."""
        self.con.execute("BEGIN")
        try:
            for t in ("payments", "tasks", "projects", "clients", "work_types"):
                if t in data:
                    self.con.execute(f"DELETE FROM {t}")
            for t in ("transactions", "commitments", "expected_income",
                      "goals", "budgets", "settings", "accounts"):
                self.con.execute(f"DELETE FROM {t}")
            for t, rows in data.items():
                for row in rows:
                    cols = ",".join(row.keys())
                    qs = ",".join("?" for _ in row)
                    self.con.execute(f"INSERT INTO {t}({cols}) VALUES({qs})",
                                     tuple(row.values()))
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise