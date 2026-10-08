"""Undo for deletes: snapshot before, restore after. Works for every kind of delete (including
side effects like account balances) because it restores the whole database exactly as it was.
Undo is only offered while nothing else has changed since the delete."""


def _core(data):
    return {k: v for k, v in data.items() if k != "settings"}   # settings change in the background


class Undo:
    def __init__(self, db):
        self.db, self.state = db, None

    def run(self, fn):
        before = self.db.export_json()
        fn()
        self.state = (before, _core(self.db.export_json()))

    def can_undo(self):
        return bool(self.state) and _core(self.db.export_json()) == self.state[1]

    def undo(self):
        if not self.can_undo():
            return False
        data = dict(self.state[0])
        data["settings"] = self.db.export_json()["settings"]    # keep current settings
        self.db.import_json(data)
        self.state = None
        return True

    def clear(self):
        self.state = None
