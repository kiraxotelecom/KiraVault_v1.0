"""SQLite data layer for the work tracker. No UI code in here."""
import os
import sqlite3
from datetime import date, datetime, timedelta

STATUSES = ["Pending", "Started", "In Progress", "Review", "Completed", "Cancelled"]
SOURCES = ["Freelance", "Job", "Personal"]
PRIORITIES = ["High", "Medium", "Low"]
DONE = ("Completed", "Cancelled")
# Each status button moves progress to a milestone. Cancelled is deliberately absent:
# it keeps whatever progress the project had, so you can see how far it got.
STATUS_PROGRESS = {"Pending": 0, "Started": 25, "In Progress": 50, "Review": 75, "Completed": 100}
DEFAULT_TYPES = ["Video Editing", "Video Shooting", "Photography", "Motion Graphics",
                 "Graphic Design", "Web Design", "Digital Marketing"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS work_types(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  client_id INTEGER REFERENCES clients(id),
  work_type TEXT,
  source TEXT DEFAULT 'Freelance',
  status TEXT DEFAULT 'Pending',
  start_date TEXT,
  deadline TEXT,
  amount REAL DEFAULT 0,
  progress INTEGER DEFAULT 0,
  priority TEXT DEFAULT 'Medium',
  est_days REAL,
  notes TEXT,
  done_date TEXT,
  created TEXT);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  amount REAL NOT NULL,
  paid_on TEXT NOT NULL,
  note TEXT);
"""

PROJ_SQL = """
SELECT p.*, COALESCE(c.name, '') AS client,
       COALESCE((SELECT SUM(amount) FROM payments WHERE project_id = p.id), 0) AS paid
FROM projects p LEFT JOIN clients c ON c.id = p.client_id
"""


def today():
    return date.today()


def parse_date(s):
    try:
        return date.fromisoformat(s.strip()) if s and s.strip() else None
    except ValueError:
        return None


def balance(p):
    return max(0, (p["amount"] or 0) - (p["paid"] or 0))


def is_archived(p):
    return p["status"] == "Cancelled" or (p["status"] == "Completed" and balance(p) <= 0)


# Workload is measured in "full days of work per day": 1.0 = a completely full day.
# Projects without an estimate count as a part day, by priority.
DEFAULT_LOAD = {"High": 0.6, "Medium": 0.4, "Low": 0.25}


def level(score):
    """Workload score -> (name, calendar tint, bar colour)."""
    if score <= 0.001:
        return "Free", "#FFFFFF", "#22A559"
    if score < 0.5:
        return "Light", "#DDF5E6", "#22A559"
    if score < 1.0:
        return "Moderate", "#FFF1D0", "#F5A524"
    if score < 1.5:
        return "Heavy", "#FFDADA", "#E5484D"
    return "Very heavy", "#FFB8B8", "#E5484D"


class DB:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA foreign_keys = ON")
        self.c.executescript(SCHEMA)
        if "est_days" not in [r["name"] for r in self.q("PRAGMA table_info(projects)")]:
            self.c.execute("ALTER TABLE projects ADD COLUMN est_days REAL")
            self.c.commit()
        if "tx_id" not in [r["name"] for r in self.q("PRAGMA table_info(payments)")]:
            self.c.execute("ALTER TABLE payments ADD COLUMN tx_id INTEGER")
            self.c.commit()
        if not self.q("SELECT 1 FROM work_types LIMIT 1"):
            self.c.executemany("INSERT INTO work_types(name) VALUES(?)",
                               [(n,) for n in DEFAULT_TYPES])
            self.c.commit()

    # ---- low level -------------------------------------------------
    def q(self, sql, args=()):
        return self.c.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        cur = self.c.execute(sql, args)
        self.c.commit()
        return cur.lastrowid

    def backup(self):
        folder = os.path.join(os.path.dirname(self.path), "backups")
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, "workflow_%s.db" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        out = sqlite3.connect(dest)
        self.c.backup(out)
        out.close()
        return dest

    # ---- lookups ---------------------------------------------------
    def work_types(self):
        return [r["name"] for r in self.q("SELECT name FROM work_types ORDER BY id")]

    def clients(self):
        return self.q("""SELECT c.id, c.name, COUNT(p.id) AS n,
                                COALESCE(SUM(p.amount), 0) AS billed
                         FROM clients c LEFT JOIN projects p ON p.client_id = c.id
                         GROUP BY c.id ORDER BY c.name COLLATE NOCASE""")

    def client(self, cid):
        r = self.q("SELECT id, name FROM clients WHERE id = ?", (cid,))
        return r[0] if r else None

    @staticmethod
    def _totals(projs):
        """Billed / paid / outstanding over a client's projects. Cancelled work is not billed
        and not chased; money already received on it still counts as paid."""
        live = [p for p in projs if p["status"] != "Cancelled"]
        billed = sum(p["amount"] or 0 for p in live)
        paid = sum(p["paid"] or 0 for p in projs)
        owed = sum(balance(p) for p in live)
        return {"n": len(projs), "active": sum(1 for p in live if p["status"] not in DONE),
                "billed": billed, "paid": paid, "outstanding": owed,
                "average": billed / len(live) if live else 0,
                "since": min((p["created"] for p in projs if p["created"]), default=None)}

    def client_projects(self, cid):
        return [p for p in self.projects() if p["client_id"] == cid]

    def client_totals(self, cid):
        return self._totals(self.client_projects(cid))

    def clients_summary(self):
        """Every client with totals; those who owe you the most come first."""
        by = {}
        for p in self.projects():
            by.setdefault(p["client_id"], []).append(p)
        out = []
        for c in self.clients():
            t = self._totals(by.get(c["id"], []))
            t.update(id=c["id"], name=c["name"])
            out.append(t)
        return sorted(out, key=lambda t: (-t["outstanding"], t["name"].lower()))

    def get_client(self, name):
        name = (name or "").strip()
        if not name:
            return None
        r = self.q("SELECT id FROM clients WHERE name = ? COLLATE NOCASE", (name,))
        return r[0]["id"] if r else self.x("INSERT INTO clients(name) VALUES(?)", (name,))

    # ---- projects --------------------------------------------------
    def projects(self):
        return self.q(PROJ_SQL + " ORDER BY p.deadline IS NULL, p.deadline, p.id")

    def project(self, pid):
        r = self.q(PROJ_SQL + " WHERE p.id = ?", (pid,))
        return r[0] if r else None

    def save_project(self, d, pid=None):
        cid = self.get_client(d["client"])
        if d["work_type"]:
            self.x("INSERT OR IGNORE INTO work_types(name) VALUES(?)", (d["work_type"],))
        progress = 100 if d["status"] == "Completed" else int(d["progress"])
        done = today().isoformat() if d["status"] == "Completed" else None
        vals = (d["title"], cid, d["work_type"], d["source"], d["status"],
                d["start_date"] or None, d["deadline"] or None, d["amount"],
                progress, d["priority"], d["notes"], d.get("est_days"))
        if pid is None:
            return self.x("""INSERT INTO projects(title, client_id, work_type, source, status,
                              start_date, deadline, amount, progress, priority, notes, est_days,
                              done_date, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          vals + (done, today().isoformat()))
        old = self.project(pid)
        if old and old["status"] == "Completed" and done:
            done = old["done_date"] or done
        self.x("""UPDATE projects SET title=?, client_id=?, work_type=?, source=?, status=?,
                  start_date=?, deadline=?, amount=?, progress=?, priority=?, notes=?,
                  est_days=?, done_date=? WHERE id=?""", vals + (done, pid))
        return pid

    def set_status(self, pid, status):
        done = today().isoformat() if status == "Completed" else None
        self.x("UPDATE projects SET status=?, done_date=? WHERE id=?", (status, done, pid))
        if status in STATUS_PROGRESS:
            self.x("UPDATE projects SET progress=? WHERE id=?", (STATUS_PROGRESS[status], pid))

    def delete_project(self, pid):
        self.x("DELETE FROM projects WHERE id = ?", (pid,))

    # ---- tasks -----------------------------------------------------
    def tasks(self, pid):
        return self.q("SELECT * FROM tasks WHERE project_id = ? ORDER BY id", (pid,))

    def open_tasks(self):
        return self.q("""SELECT t.*, p.title AS project FROM tasks t
                         JOIN projects p ON p.id = t.project_id
                         WHERE t.done = 0 AND p.status NOT IN ('Completed', 'Cancelled')
                         ORDER BY p.deadline IS NULL, p.deadline, t.id""")

    def add_task(self, pid, title):
        self.x("INSERT INTO tasks(project_id, title) VALUES(?, ?)", (pid, title))
        self._sync_progress(pid)

    def toggle_task(self, tid, done):
        self.x("UPDATE tasks SET done = ? WHERE id = ?", (1 if done else 0, tid))
        self._sync_progress(self.q("SELECT project_id FROM tasks WHERE id = ?", (tid,))[0][0])

    def delete_task(self, tid):
        r = self.q("SELECT project_id FROM tasks WHERE id = ?", (tid,))
        self.x("DELETE FROM tasks WHERE id = ?", (tid,))
        if r:
            self._sync_progress(r[0][0])

    def _sync_progress(self, pid):
        t = self.q("SELECT COUNT(*) AS n, COALESCE(SUM(done), 0) AS d FROM tasks WHERE project_id = ?",
                   (pid,))[0]
        if t["n"]:
            self.x("UPDATE projects SET progress = ? WHERE id = ?",
                   (round(100 * t["d"] / t["n"]), pid))

    # ---- payments --------------------------------------------------
    def payments(self, pid):
        return self.q("SELECT * FROM payments WHERE project_id = ? ORDER BY paid_on DESC, id DESC", (pid,))

    def payment(self, payid):
        r = self.q("SELECT * FROM payments WHERE id = ?", (payid,))
        return r[0] if r else None

    def add_payment(self, pid, amount, note="", paid_on=None, tx_id=None):
        return self.x("INSERT INTO payments(project_id, amount, paid_on, note, tx_id) VALUES(?,?,?,?,?)",
                      (pid, amount, paid_on or today().isoformat(), note, tx_id))

    def update_payment(self, payid, amount, paid_on, tx_id=None):
        self.x("UPDATE payments SET amount=?, paid_on=?, tx_id=? WHERE id=?",
               (amount, paid_on, tx_id, payid))

    def delete_payment(self, payid):
        self.x("DELETE FROM payments WHERE id = ?", (payid,))

    # ---- workload & stats -----------------------------------------
    def load(self, days):
        """{date: (score, [projects])}. Score = full days of work needed that day (1.0 = full).
        With an estimate, effort is spread over the project's window: the whole estimate over
        past days, only the work still remaining (by progress) from today on. Without one, a
        part day by priority. Unfinished projects past their deadline count up to today."""
        t = today()
        spans = []
        for p in self.projects():
            if p["status"] in DONE:
                continue
            a, b = parse_date(p["start_date"] or p["deadline"]), parse_date(p["deadline"] or p["start_date"])
            if not a or not b:
                continue
            a, b = min(a, b), max(a, b, t)
            est = p["est_days"]
            if est:
                remaining = est * (1 - (p["progress"] or 0) / 100)
                total_rate = min(est / ((b - a).days + 1), 1.0)
                rem_rate = min(remaining / ((b - max(a, t)).days + 1), 1.0)
            else:
                total_rate = rem_rate = DEFAULT_LOAD.get(p["priority"], 0.4)
            spans.append((a, b, total_rate, rem_rate, p))
        out = {}
        for d in days:
            hit = [(tr if d < t else rr, p) for a, b, tr, rr, p in spans if a <= d <= b]
            out[d] = (sum(r for r, _ in hit), [p for _, p in hit])
        return out

    def capacity_check(self, est, start, deadline):
        """Can a new job of `est` days fit between start and deadline on top of current work?
        verdict: 'yes' (even spread never exceeds a full day), 'tight' (fits only by using
        lighter days) or 'no'. Also the earliest date enough free time has accumulated."""
        start = max(start, today())
        n = (deadline - start).days + 1
        horizon = max(365, n)
        days = [start + timedelta(i) for i in range(horizon)]
        ld = self.load(days)
        free = [max(0.0, 1.0 - ld[d][0]) for d in days]
        rate = est / n
        window = [ld[d][0] for d in days[:n]]
        over = sum(1 for x in window if x + rate > 1.0 + 1e-9)
        free_in = sum(free[:n])
        earliest, acc = None, 0.0
        for d, f in zip(days, free):
            acc += f
            if acc >= est - 1e-9:
                earliest = d
                break
        verdict = "yes" if over == 0 else "tight" if free_in >= est - 1e-9 else "no"
        return {"verdict": verdict, "n": n, "peak": max(window) + rate, "over": over,
                "free": free_in, "earliest": earliest}

    def overdue(self):
        t = today().isoformat()
        return [p for p in self.projects()
                if p["status"] not in DONE and p["deadline"] and p["deadline"] < t]

    def month_stats(self, y, m):
        prefix = "%04d-%02d" % (y, m)
        projs = [p for p in self.projects() if p["status"] != "Cancelled"]
        scheduled = [p for p in projs if (p["deadline"] or "").startswith(prefix)]
        completed = [p for p in projs if (p["done_date"] or "").startswith(prefix)]
        pays = self.q("""SELECT p.source, pay.amount FROM payments pay
                         JOIN projects p ON p.id = pay.project_id
                         WHERE pay.paid_on LIKE ?""", (prefix + "%",))
        by_source, by_type = {}, {}
        for r in pays:
            by_source[r["source"]] = by_source.get(r["source"], 0) + r["amount"]
        for p in completed:
            by_type[p["work_type"] or "Other"] = by_type.get(p["work_type"] or "Other", 0) + 1
        return {
            "earned": sum(r["amount"] for r in pays),
            "by_source": by_source,
            "by_type": by_type,
            "scheduled": scheduled,
            "completed": completed,
            "expected": sum(p["amount"] or 0 for p in scheduled),
            "paid": sum(p["paid"] for p in scheduled),
        }

    # ---- demo data -------------------------------------------------
    def seed_demo(self):
        t = today()

        def d(n):
            return (t + timedelta(days=n)).isoformat()

        demo = [
            ("CNOOC Documentary", "CNOOC", "Video Editing", "Freelance", "In Progress", -15, 3, 1500000, 500000, 80, "High"),
            ("XYZ Corporate Video", "XYZ Ltd", "Video Shooting", "Freelance", "In Progress", -5, 6, 1200000, 300000, 50, "Medium"),
            ("ABC Event Coverage", "ABC Events", "Photography", "Freelance", "Started", 1, 8, 800000, 200000, 20, "Medium"),
            ("Website Design", "Tech Solutions", "Web Design", "Freelance", "Pending", 4, 14, 750000, 0, 0, "Low"),
            ("Office Promo Reel", "Day job", "Video Editing", "Job", "Review", -10, 1, 0, 0, 90, "High"),
        ]
        for title, client, wt, src, st, a, b, amt, paid, prog, pr in demo:
            pid = self.save_project({"title": title, "client": client, "work_type": wt, "source": src,
                                     "status": st, "start_date": d(a), "deadline": d(b), "amount": amt,
                                     "progress": prog, "priority": pr, "notes": ""})
            if paid:
                self.add_payment(pid, paid, "Deposit")
        first = self.q("SELECT id FROM projects ORDER BY id LIMIT 1")[0]["id"]
        for i, name in enumerate(["Receive footage", "Organize footage", "Select interviews",
                                  "Rough cut", "Client review", "Final edit", "Export", "Delivery",
                                  "Payment"]):
            self.x("INSERT INTO tasks(project_id, title, done) VALUES(?,?,?)", (first, name, 1 if i < 6 else 0))
