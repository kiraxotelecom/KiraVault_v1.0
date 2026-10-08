"""Automatic backups: rotating JSON snapshots in the app folder, plus a best-effort copy in
shared storage so something can survive an uninstall. No Kivy imports (testable anywhere)."""
import json
import os
import shutil
from datetime import datetime, timedelta

KEEP_AUTO, KEEP_BEFORE = 7, 3
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _dir(user_dir):
    p = os.path.join(user_dir, "auto_backups")
    os.makedirs(p, exist_ok=True)
    return p


def _write(path, data):
    tmp = path + ".tmp"          # write fully, then swap in: never leaves a half-written backup
    with open(tmp, "w") as f:
        json.dump(data, f, default=str)
    os.replace(tmp, path)


def list_backups(user_dir):
    out = []
    for n in os.listdir(_dir(user_dir)):
        if n.endswith(".json"):
            p = os.path.join(_dir(user_dir), n)
            st = os.stat(p)
            out.append({"path": p, "name": n, "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime),
                        "kind": "before" if "before_restore" in n else "auto"})
    return sorted(out, key=lambda b: b["mtime"], reverse=True)


def prune(user_dir):
    items = list_backups(user_dir)
    for kind, keep in (("auto", KEEP_AUTO), ("before", KEEP_BEFORE)):
        for b in [x for x in items if x["kind"] == kind][keep:]:
            os.remove(b["path"])


def make(user_dir, data, kind="auto", now=None):
    """One file per day (overwritten within the day), newest 7 kept."""
    now = now or datetime.now()
    name = (f"kiravault_auto_{now.date().isoformat()}.json" if kind == "auto"
            else f"kiravault_before_restore_{now:%Y%m%d_%H%M%S}.json")
    path = os.path.join(_dir(user_dir), name)
    _write(path, data)
    prune(user_dir)
    return path


# ---------------------------------------------------------------- outside the app
def _android_copy(path, name):
    from jnius import autoclass
    data = open(path, "rb").read()
    if autoclass("android.os.Build$VERSION").SDK_INT >= 29:
        # MediaStore needs no storage permission; the file lands in Downloads/KiraVault
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        resolver = activity.getContentResolver()
        base = autoclass("android.provider.MediaStore$Downloads").EXTERNAL_CONTENT_URI
        rel = "Download/KiraVault/"
        uri, cur = None, resolver.query(base, ["_id"], "_display_name=? AND relative_path=?",
                                        [name, rel], None)
        try:
            if cur is not None and cur.moveToFirst():
                uri = autoclass("android.content.ContentUris").withAppendedId(base, cur.getLong(0))
        finally:
            if cur is not None:
                cur.close()
        if uri is None:
            v = autoclass("android.content.ContentValues")()
            v.put("_display_name", name)
            v.put("mime_type", "application/json")
            v.put("relative_path", "Download/KiraVault")
            uri = resolver.insert(base, v)
        out = resolver.openOutputStream(uri, "wt")
        try:
            out.write([b - 256 if b > 127 else b for b in data])
            out.flush()
        finally:
            out.close()
        return "Downloads/KiraVault/" + name
    env = autoclass("android.os.Environment")
    folder = os.path.join(env.getExternalStoragePublicDirectory(env.DIRECTORY_DOWNLOADS).getAbsolutePath(),
                          "KiraVault")
    os.makedirs(folder, exist_ok=True)
    shutil.copyfile(path, os.path.join(folder, name))
    return "Downloads/KiraVault/" + name


def public_copy(path, now=None):
    """Copy outside the app's own folder. Seven rotating files (one per weekday).
    Returns (ok, where_or_reason). Never raises."""
    now = now or datetime.now()
    name = f"kiravault_backup_{WEEKDAYS[now.weekday()]}.json"
    try:
        if os.environ.get("ANDROID_ARGUMENT"):
            return True, _android_copy(path, name)
        folder = os.path.join(os.path.expanduser("~"), "KiraVault_backups")
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(path, os.path.join(folder, name))
        return True, os.path.join(folder, name)
    except Exception as e:
        return False, str(e)[:100] or e.__class__.__name__


# ---------------------------------------------------------------- policy
def due(last_iso, now, changed, min_gap=timedelta(minutes=15)):
    """Back up if none yet today, or data changed and the last one is at least 15 min old."""
    try:
        last = datetime.fromisoformat(last_iso)
    except (TypeError, ValueError):
        return True
    if last.date() != now.date():
        return True
    return bool(changed) and now - last >= min_gap


def backup_now(db, user_dir, now=None):
    now = now or datetime.now()
    path = make(user_dir, db.export_json(), now=now)
    ok, where = public_copy(path, now)
    db.set_setting("last_backup", now.isoformat(timespec="seconds"))
    db.set_setting("backup_where", ("Extra copy: " + where) if ok else
                   "No copy outside the app yet (" + where + ")")
    return ok, where


def describe(last_iso, now):
    try:
        last = datetime.fromisoformat(last_iso)
    except (TypeError, ValueError):
        return "No backup yet"
    days = (now.date() - last.date()).days
    day = "today" if days == 0 else "yesterday" if days == 1 else f"{last:%d %b %Y}"
    return f"Last backup: {day} at {last:%H:%M}"
