"""KiraVault - money + work in one offline app (pure Kivy + SQLite).

Know three numbers at a glance:
    what you HAVE, what you've PROMISED, what is SAFE TO SPEND.
"""
import json
import os
import re
import calendar
from datetime import date, datetime, timedelta

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle, Line
from kivy.graphics.texture import Texture
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

from kivy.app import App

from db import (ACCOUNTS, DB, EXPENSE_CATEGORIES, INCOME_SOURCES, KINDS,
                commit_state, due_text, fmt, month_end, pretty_date, short, tx_title)
from quickparse import parse_amount, parse_date, parse_days, parse_quick
import assistant
import autobackup
import reminders
from charts import Badge, Dot, bar_chart, donut_with_legend, PALETTE
from undo import Undo
from theme import ACCENTS, Theme, category_color
from workdb import (DB as WorkDB, DEFAULT_TYPES, DONE, PRIORITIES, SOURCES, STATUSES,
                    balance as wbal, is_archived, level)

# --------------------------------------------------------------------- theme
def T(key):
    return Theme.c[key]


# ------------------------------------------------------------------ widgets
class Lbl(Label):
    """Auto-height, word-wrapping label."""
    def __init__(self, text="", size=14, color=None, bold=False, halign="left", **kw):
        super().__init__(
            text=text, font_size=sp(size),
            color=color if color is not None else T("text"),
            bold=bold, halign=halign, valign="middle",
            size_hint_y=None, **kw,
        )
        self.bind(width=lambda *_: setattr(self, "text_size", (self.width, None)))
        self.bind(texture_size=lambda *_: setattr(self, "height", self.texture_size[1] + dp(2)))


class Card(BoxLayout):
    """stripe=<rgba> draws a slim coloured bar on the left edge: a quick visual cue for the card's topic."""
    def __init__(self, bg=None, radius=14, stripe=None, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("padding", dp(14))
        if stripe is not None:
            pad = kw["padding"]
            pad = [pad] * 4 if not isinstance(pad, (list, tuple)) else \
                [pad[0], pad[1], pad[0], pad[1]] if len(pad) == 2 else list(pad)
            pad[0] += dp(8)
            kw["padding"] = pad
        kw.setdefault("spacing", dp(6))
        kw.setdefault("size_hint_y", None)
        super().__init__(**kw)
        self.bind(minimum_height=self.setter("height"))
        with self.canvas.before:
            self._col = Color(*(bg if bg is not None else T("card")))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
            self._bar = None
            if stripe is not None:
                Color(*stripe)
                self._bar = RoundedRectangle(pos=self.pos, size=(dp(4), self.height), radius=[dp(2)])
        self.bind(pos=self._upd, size=self._upd)

    def _upd(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size
        if self._bar is not None:
            self._bar.pos = (self.x + dp(9), self.y + dp(12))
            self._bar.size = (dp(4), max(self.height - dp(24), 0))

    def set_bg(self, color):
        self._col.rgba = color


class TapCard(ButtonBehavior, Card):
    pass


class Row(BoxLayout):
    def __init__(self, *children, **kw):
        kw.setdefault("spacing", dp(8))
        super().__init__(orientation="horizontal", size_hint_y=None, **kw)
        self.bind(minimum_height=self.setter("height"))
        for c in children:
            self.add_widget(c)


class Btn(Button):
    def __init__(self, text="", bg=None, fg=None, size=14, h=44, **kw):
        super().__init__(
            text=text, background_normal="", background_down="",
            background_color=(0, 0, 0, 0),
            color=fg if fg is not None else ((1, 1, 1, 1) if bg is None or bg in (T("red"), T("accent")) else T("text")),
            font_size=sp(size), bold=True,
            size_hint_y=None, height=dp(h), **kw,
        )
        self._bg = bg if bg is not None else T("accent")
        with self.canvas.before:
            self._col = Color(*self._bg)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
        self.bind(pos=self._upd, size=self._upd, state=self._press)

    def _upd(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size

    def _press(self, *_):
        r, g, b, a = self._bg
        self._col.rgba = (r, g, b, 0.65 if self.state == "down" else a)

    def set_bg(self, bg):
        self._bg = bg
        self._col.rgba = bg


class Bar(Widget):
    """Progress bar drawn on the canvas."""
    def __init__(self, frac=0.0, color=None, **kw):
        super().__init__(size_hint_y=None, height=dp(8), **kw)
        self.frac = frac
        self.color = color if color is not None else T("green")
        self.bind(pos=self.draw, size=self.draw)

    def draw(self, *_):
        self.canvas.clear()
        with self.canvas:
            Color(*T("track"))
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(4)])
            w = self.width * min(max(self.frac, 0), 1)
            if w > 0:
                Color(*self.color)
                RoundedRectangle(pos=self.pos,
                                 size=(max(w, dp(8)), self.height),
                                 radius=[dp(4)])


class Divider(Widget):
    def __init__(self, **kw):
        super().__init__(size_hint_y=None, height=dp(1), **kw)
        with self.canvas.before:
            self._col = Color(*T("line"))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(1)])
        self.bind(pos=lambda *_: setattr(self._rect, "pos", self.pos),
                  size=lambda *_: setattr(self._rect, "size", self.size))


def kvrow(left, right, size=14, lcolor=None, rcolor=None, bold=False):
    return Row(
        Lbl(left, size, lcolor if lcolor else T("muted"), size_hint_x=0.55),
        Lbl(right, size, rcolor if rcolor else T("text"), bold,
            halign="right", size_hint_x=0.45),
    )


def dd(x):
    """Days with at most one decimal: 3.0 -> '3', 2.5 -> '2.5'."""
    return f"{x:.1f}".rstrip("0").rstrip(".")


def heading(text, color=None):
    """Small caps section label; pass a theme colour to tint it."""
    return Lbl(text.upper(), 11, color if color is not None else T("muted"), True)


# One colour per section, used for its title, tabs, buttons and the bottom-nav label.
SECTION = {"home": "accent", "work": "purple", "project": "purple", "client": "purple",
           "calendar": "teal", "money": "blue", "insights": "pink", "settings": "accent"}
STATUS_COLOR = {"Pending": "muted", "Started": "blue", "In Progress": "purple",
                "Review": "amber", "Completed": "green", "Cancelled": "muted"}


def tint(key, t=0.16):
    """Soft background version of a theme colour."""
    return mix(T(key), T("card"), t)


def title(text, key="accent"):
    return Lbl(text, 22, T(key), True)


def tbtn(text, key, **kw):
    """Tinted button in a theme colour (soft background, coloured text)."""
    return Btn(text, bg=tint(key), fg=T(key), **kw)


def sbtn(text, key, **kw):
    """Solid button in a theme colour."""
    return Btn(text, bg=T(key), fg=(1, 1, 1, 1), **kw)


def tab_row(options, selected, on_pick, color="accent"):
    """Equal-width buttons that fill the screen (no scrolling), for a few main tabs."""
    return Row(*[Btn(o, bg=T(color) if o == selected else tint(color),
                     fg=(1, 1, 1, 1) if o == selected else T(color), size=14, h=40,
                     on_release=lambda _b, o=o: on_pick(o)) for o in options])


def chip_row(options, selected, on_pick, color="accent"):
    sv = ScrollView(size_hint_y=None, height=dp(38), do_scroll_y=False, bar_width=0)
    box = BoxLayout(size_hint_x=None, spacing=dp(6))
    box.bind(minimum_width=box.setter("width"))
    for o in options:
        on = o == selected
        box.add_widget(Btn(o,
                           bg=T(color) if on else tint(color),
                           fg=(1, 1, 1, 1) if on else T(color),
                           size=12, h=34,
                           size_hint_x=None,
                           width=dp(24 + 8 * len(o)),
                           on_release=lambda _b, o=o: on_pick(o)))
    sv.add_widget(box)
    return sv


def make_input(default="", hint=""):
    return TextInput(
        text=default, hint_text=hint, multiline=False,
        size_hint_y=None, height=dp(42),
        background_normal="", background_active="",
        background_color=T("card2"),
        foreground_color=T("text"),
        hint_text_color=T("muted"),
        cursor_color=T("text"),
        padding=[dp(10), dp(11)], font_size=sp(15),
    )


def make_spinner(default, options):
    return Spinner(
        text=default, values=options, size_hint_y=None, height=dp(42),
        background_normal="", background_color=T("card2"),
        color=T("text"), font_size=sp(15),
    )


class DateFieldButton(Button):
    """Looks like an input, opens a picker."""
    def __init__(self, iso, on_change=None, **kw):
        self.iso = iso
        super().__init__(
            text=self._pretty(iso),
            background_normal="", background_down="",
            background_color=(0, 0, 0, 0),
            color=T("text"), font_size=sp(15),
            size_hint_y=None, height=dp(42), **kw,
        )
        self._on_change = on_change or (lambda _iso: None)
        with self.canvas.before:
            self._col = Color(*T("card2"))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
        self.bind(pos=lambda *_: setattr(self._rect, "pos", self.pos),
                  size=lambda *_: setattr(self._rect, "size", self.size))

    @staticmethod
    def _pretty(iso):
        try:
            d = date.fromisoformat(iso)
            return f"{d.day} {calendar.month_name[d.month]} {d.year}"
        except Exception:
            return iso

    def on_release(self):
        DatePickerPopup(initial=self.iso, on_pick=self._picked).open()

    def _picked(self, iso):
        self.iso = iso
        self.text = self._pretty(iso)
        self._on_change(iso)


# ---------------------------------------------------------------- date picker
class DayCell(ButtonBehavior, Label):
    """A tappable cell in the date picker (a day, a month or a year)."""
    def __init__(self, day, on_pick, selected=False, today=False, label=None, h=38):
        super().__init__(
            text=label if label is not None else str(day), font_size=sp(14),
            color=T("accent") if selected else T("text"),
            bold=selected or today, halign="center", valign="middle",
            size_hint_y=None, height=dp(h),
        )
        self.bind(size=lambda *_: setattr(self, "text_size", self.size))
        self._day = day
        self._on_pick = on_pick
        with self.canvas.before:
            self._col = Color(*(T("accent") if selected else T("card2") if today else (0, 0, 0, 0)))
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(8)])
        self.bind(pos=lambda *_: setattr(self._rect, "pos", self.pos),
                  size=lambda *_: setattr(self._rect, "size", self.size))

    def on_release(self):
        self._on_pick(self._day)


class DatePickerPopup(ModalView):
    """Days by default. Tap the title to jump by month, tap again to jump by year."""
    def __init__(self, initial=None, on_pick=None, **kw):
        super().__init__(size_hint=(0.94, None), auto_dismiss=True,
                         background="", background_color=(0, 0, 0, 0),
                         overlay_color=(0, 0, 0, 0.65), **kw)
        self.on_pick = on_pick or (lambda _iso: None)
        self.selected = self._parse(initial) or date.today()
        self.view_year = self.selected.year
        self.view_month = self.selected.month
        self.mode = "day"

        box = self._box = Card(bg=T("card"), padding=dp(14), spacing=dp(8))
        box.bind(height=lambda _i, v: setattr(self, "height", v))
        self.add_widget(box)

        self._title = Btn("", bg=T("card"), fg=T("text"), size=16, h=36, size_hint_x=0.7,
                          on_release=lambda *_: self._cycle())
        box.add_widget(Row(
            Btn("<", bg=T("card2"), size=14, h=36, size_hint_x=0.15,
                on_release=lambda *_: self._shift(-1)),
            self._title,
            Btn(">", bg=T("card2"), size=14, h=36, size_hint_x=0.15,
                on_release=lambda *_: self._shift(1)),
        ))

        self._wd = BoxLayout(size_hint_y=None, height=dp(24))
        for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"):
            self._wd.add_widget(Lbl(name, 11, T("muted"), True, halign="center"))
        box.add_widget(self._wd)

        from kivy.uix.gridlayout import GridLayout
        self._grid = GridLayout(cols=7, size_hint_y=None, spacing=dp(4))
        self._grid.bind(minimum_height=self._grid.setter("height"))
        box.add_widget(self._grid)

        box.add_widget(Row(
            Btn("Cancel", bg=T("card2"), on_release=lambda *_: self.dismiss()),
            Btn("Today", bg=T("card2"), on_release=lambda *_: self._pick(date.today())),
        ))
        self._redraw()

    @staticmethod
    def _parse(s):
        try:
            return date.fromisoformat(s) if s else None
        except (ValueError, TypeError):
            return None

    def _cycle(self):
        self.mode = {"day": "month", "month": "year", "year": "month"}[self.mode]
        self._redraw()

    def _shift(self, delta):
        if self.mode == "day":
            m = self.view_month + delta
            y = self.view_year
            if m < 1:
                m, y = 12, y - 1
            elif m > 12:
                m, y = 1, y + 1
            self.view_month, self.view_year = m, y
        elif self.mode == "month":
            self.view_year += delta
        else:
            self.view_year += 12 * delta
        self.view_year = min(max(self.view_year, 1900), 2200)
        self._redraw()

    def _pick_month(self, m):
        self.view_month, self.mode = m, "day"
        self._redraw()

    def _pick_year(self, y):
        self.view_year, self.mode = y, "month"
        self._redraw()

    def _redraw(self):
        g, today, sel = self._grid, date.today(), self.selected
        g.clear_widgets()
        show_wd = self.mode == "day"
        if show_wd and self._wd not in self._box.children:
            self._box.add_widget(self._wd, index=2)
        elif not show_wd and self._wd in self._box.children:
            self._box.remove_widget(self._wd)

        if self.mode == "day":
            g.cols = 7
            self._title.text = f"{calendar.month_name[self.view_month]} {self.view_year}"
            first_wd, n_days = calendar.monthrange(self.view_year, self.view_month)
            for _ in range(first_wd):
                g.add_widget(Widget(size_hint_y=None, height=dp(38)))
            for d in range(1, n_days + 1):
                this = date(self.view_year, self.view_month, d)
                g.add_widget(DayCell(d, self._pick, selected=(this == sel), today=(this == today)))
        elif self.mode == "month":
            g.cols = 3
            self._title.text = str(self.view_year)
            for i in range(1, 13):
                g.add_widget(DayCell(i, self._pick_month, label=calendar.month_abbr[i], h=46,
                                     selected=(self.view_year == sel.year and i == sel.month),
                                     today=(self.view_year == today.year and i == today.month)))
        else:
            g.cols = 3
            base = self.view_year - self.view_year % 12
            self._title.text = f"{base} - {base + 11}"
            for y in range(base, base + 12):
                g.add_widget(DayCell(y, self._pick_year, label=str(y), h=46,
                                     selected=(y == sel.year), today=(y == today.year)))

    def _pick(self, d):
        if not isinstance(d, date):
            d = date(self.view_year, self.view_month, d)
        self.selected = d
        self.dismiss()
        self.on_pick(self.selected.isoformat())


# ---------------------------------------------------------------- popups
class BasePopup(ModalView):
    def __init__(self, **kw):
        super().__init__(size_hint=(0.94, None), auto_dismiss=True,
                         background="", background_color=(0, 0, 0, 0),
                         overlay_color=(0, 0, 0, 0.65), **kw)
        self.box = Card(bg=T("card"), padding=dp(16), spacing=dp(8))
        # popups taller than the screen scroll instead of running off it
        self.box.bind(height=lambda _i, v: setattr(self, "height", min(v, Window.height * 0.92)))
        sv = ScrollView(do_scroll_x=False, bar_width=0)
        sv.add_widget(self.box)
        self.add_widget(sv)


OTHER_LABELS = ("Other", "Others")


class OtherPopup(BasePopup):
    """Asks 'what do you mean by Other?' and hands the typed text back (or None if cancelled)."""
    def __init__(self, label, on_result):
        super().__init__()
        self.on_result, self._answered = on_result, False
        self.box.add_widget(Lbl(f"What do you mean by '{label}'?", 18, T("text"), True))
        self.box.add_widget(Lbl("Type it here and it will be used instead.", 12, T("muted")))
        self.inp = make_input("", "e.g. Phone repair")
        self.inp.bind(on_text_validate=self._ok)
        self.box.add_widget(self.inp)
        self.err = Lbl("", 13, T("red"))
        self.box.add_widget(self.err)
        self.box.add_widget(Row(Btn("Cancel", bg=T("card2"), on_release=lambda *_: self.dismiss()),
                                Btn("OK", on_release=self._ok)))
        self.bind(on_dismiss=self._closed)
        Clock.schedule_once(lambda *_: setattr(self.inp, "focus", True), 0.2)

    def _ok(self, *_):
        t = self.inp.text.strip()
        if not t or t in OTHER_LABELS:
            self.err.text = "Please type what you mean."
            return
        self._answered = True
        self.on_result(t)
        self.dismiss()

    def _closed(self, *_):
        if not self._answered:
            self._answered = True
            self.on_result(None)


class FormPopup(BasePopup):
    """
    fields: (key, label, kind, default, options).
      kind is one of: 'text', 'choice', 'date'
    on_submit(values) -> error str or None
    """
    def __init__(self, title, fields, on_submit, on_done, submit_text="Save"):
        super().__init__()
        self.on_submit, self.on_done, self.widgets, self.labels = on_submit, on_done, {}, {}
        self.box.add_widget(Lbl(title, 18, T("text"), True))

        for key, label, kind, default, options in fields:
            lab = Lbl(label, 12, T("muted"))
            self.labels[key] = lab
            self.box.add_widget(lab)
            if kind == "date":
                initial = default if default and default != "today" else date.today().isoformat()
                w = DateFieldButton(initial)
            elif kind == "text":
                w = make_input(default)
            else:
                w = make_spinner(default, options)
                if any(o in OTHER_LABELS for o in options):
                    w._prev = default if default not in OTHER_LABELS else options[0]
                    w.bind(text=self._choice_changed)
            self.widgets[key] = w
            self.box.add_widget(w)

        self.err = Lbl("", 13, T("red"))
        self.box.add_widget(self.err)
        self.box.add_widget(Btn(submit_text, on_release=self.submit))

    def _choice_changed(self, spinner, text):
        """Picking 'Other'/'Others' opens a box to type what it really is."""
        if text not in OTHER_LABELS:
            spinner._prev = text
            return

        def got(answer):
            spinner.text = answer if answer else spinner._prev

        OtherPopup(text, got).open()

    def set_visible(self, key, show):
        """Show/hide a field (label + input). Only the last field is supported."""
        lab, w = self.labels[key], self.widgets[key]
        present = w in self.box.children
        if show and not present:
            self.box.add_widget(lab, index=2)
            self.box.add_widget(w, index=2)
        elif not show and present:
            self.box.remove_widget(w)
            self.box.remove_widget(lab)

    def submit(self, *_):
        vals = {}
        for k, w in self.widgets.items():
            if isinstance(w, DateFieldButton):
                vals[k] = w.iso
            else:
                vals[k] = w.text.strip()
        try:
            err = self.on_submit(vals)
        except ValueError as e:
            err = str(e)
        if err:
            self.err.text = err
        else:
            self.dismiss()
            self.on_done()


class ConfirmPopup(BasePopup):
    """Deletes (the default) can be undone for a few seconds; other confirmations just run."""
    def __init__(self, message, on_yes, on_done, yes_text="Delete"):
        super().__init__()
        self.box.add_widget(Lbl(message, 15, T("text")))

        def go(*_):
            self.dismiss()
            if yes_text == "Delete":
                name = re.search(r"'(.+?)'", message)
                App.get_running_app().delete_with_undo(f"Deleted '{name.group(1)}'" if name else "Deleted", on_yes)
            else:
                on_yes()
            on_done()

        self.box.add_widget(Row(
            Btn("Cancel", bg=T("card2"), on_release=lambda *_: self.dismiss()),
            Btn(yes_text, bg=T("red"), on_release=go),
        ))


class RestorePopup(BasePopup):
    def __init__(self, app):
        super().__init__()
        self.box.add_widget(Lbl("Restore a backup", 18, T("text"), True))
        items = autobackup.list_backups(app.user_data_dir)
        if not items:
            self.box.add_widget(Lbl("No automatic backups yet.", 13, T("muted")))
        for bk in items:
            tag = "  (before a restore)" if bk["kind"] == "before" else ""
            self.box.add_widget(Btn(f"{bk['mtime']:%a %d %b %Y, %H:%M}   {max(bk['size'] // 1024, 1)} KB{tag}",
                                    bg=T("card2"), size=12, h=40,
                                    on_release=lambda _b, bk=bk: self._ask(app, bk)))
        self.box.add_widget(Btn("Close", bg=T("card2"), on_release=lambda *_: self.dismiss()))

    def _ask(self, app, bk):
        self.dismiss()
        ConfirmPopup(f"Replace ALL current data with the backup from {bk['mtime']:%d %b %Y, %H:%M}? "
                     "Your current data is saved first, so you can switch back.",
                     lambda: app.restore_backup(bk["path"]), lambda: None, yes_text="Restore").open()


class InfoPopup(BasePopup):
    def __init__(self, message):
        super().__init__()
        self.box.add_widget(Lbl(message, 15, T("text")))
        self.box.add_widget(Btn("OK", on_release=lambda *_: self.dismiss()))


class VerdictPopup(BasePopup):
    """Answer to 'can I take another job?', with a shortcut to add it as a project."""
    def __init__(self, app, r, est, start, deadline):
        super().__init__()
        n, peak = r["n"], r["peak"] * 100
        if r["verdict"] == "yes":
            head, col = "Yes, you can take it.", T("green")
            body = (f"Spread evenly over {n} day{'s' if n != 1 else ''}, your busiest day in that "
                    f"window would be {peak:.0f}% of a full day.")
        elif r["verdict"] == "tight":
            head, col = "Possible, but tight.", T("amber")
            body = (f"It fits only if you use your lighter days. Spread evenly, {r['over']} of {n} days "
                    f"would go over a full day (peak {peak:.0f}%). You have about {dd(r['free'])} free "
                    f"days in that window for {dd(est)} days of work.")
        else:
            head, col = f"Not by {pretty_date(deadline)}.", T("red")
            body = (f"You have about {dd(r['free'])} free days in that window and this needs {dd(est)}.")
            body += (f" At your current load the earliest you could finish is "
                     f"{pretty_date(r['earliest'].isoformat())}." if r["earliest"]
                     else " There's no room for it in the next year at your current load.")
        self.box.add_widget(Lbl(head, 18, col, True))
        self.box.add_widget(Lbl(body, 14, T("text")))
        self.box.add_widget(Lbl("Based on your estimates. Projects without one count as a part day "
                                "by priority.", 11, T("muted")))
        self.box.add_widget(Row(
            Btn("Close", bg=T("card2"), on_release=lambda *_: self.dismiss()),
            Btn("Add as project", on_release=lambda *_: (
                self.dismiss(), app.project_form(None, start=start, deadline=deadline, est=est)))))


class QuickEntryPopup(BasePopup):
    def __init__(self, app):
        super().__init__()
        self.app, self.parsed = app, None
        self.box.add_widget(Lbl("Quick add", 18, T("text"), True))
        self.box.add_widget(Lbl(
            "Type naturally:  spent 25k on lunch cash  /  "
            "received 450k freelance mobile money",
            12, T("muted"),
        ))
        self.inp = make_input(hint="Type naturally...")
        self.box.add_widget(self.inp)
        self.box.add_widget(Btn("Understand", bg=T("card2"), on_release=self.parse))
        self.preview = Lbl("", 14, T("text"))
        self.acct = make_spinner("Cash", app.acct_names())
        self.save = Btn("Save", on_release=self.do_save)
        self.box.add_widget(self.preview)

    def parse(self, *_):
        p = parse_quick(self.inp.text)
        for w in (self.acct, self.save):
            if w.parent:
                self.box.remove_widget(w)
        if not p["ok"]:
            self.parsed = None
            self.preview.text = "I couldn't find an amount. Try e.g. 'spent 25k on lunch cash'."
            self.preview.color = T("red")
            return
        self.parsed = p
        self.acct.text = p["method"] or ("Mobile Money" if p["type"] == "income" else "Cash")
        what = p["source"] if p["type"] == "income" else p["category"]
        lines = [p["type"].title(), f"Amount:  UGX {fmt(p['amount'])}",
                 f"{'Source' if p['type'] == 'income' else 'Category'}:  {what}"]
        if p["description"]:
            lines.append(f"Description:  {p['description']}")
        lines.append(f"Date:  {pretty_date(p['date'])}")
        lines.append("Account (change if needed):")
        self.preview.text, self.preview.color = "\n".join(lines), T("text")
        self.box.add_widget(self.acct)
        self.box.add_widget(self.save)

    def do_save(self, *_):
        p = self.parsed
        if not p:
            return
        aid = self.app.db.account_id(self.acct.text)
        if p["type"] == "income":
            self.app.db.add_tx("income", p["amount"], aid, source=p["source"],
                               description=p["description"], date_=p["date"])
        else:
            self.app.db.add_tx("expense", p["amount"], aid, p["category"],
                               p["description"], date_=p["date"])
        self.dismiss()
        self.app.refresh()


class AddMenuPopup(BasePopup):
    def __init__(self, app):
        super().__init__()
        self.box.add_widget(Lbl("Add", 18, T("text"), True))
        items = [
            ("Quick add (type naturally)", T("accent"),
             lambda: QuickEntryPopup(app).open()),
            ("Income", T("card2"), app.income_form),
            ("Expense", T("card2"), app.expense_form),
            ("Commitment (bill, debt, savings)", T("card2"), app.commitment_form),
            ("Savings goal", T("card2"), app.goal_form),
            ("Work project", T("card2"), app.project_form),
        ]
        for text, bg, fn in items:
            self.box.add_widget(Btn(
                text, bg=bg,
                on_release=lambda _b, fn=fn: (self.dismiss(),
                                              Clock.schedule_once(lambda *_: fn(), 0.05)),
            ))


class BackupPopup(BasePopup):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.box.add_widget(Lbl("Backup & export", 18, T("text"), True))
        self.box.add_widget(Lbl(
            "Save a JSON backup or a CSV of transactions to your home folder.",
            12, T("muted"),
        ))
        self.box.add_widget(Btn("Export CSV", bg=T("card2"),
                                on_release=lambda *_: self._do("csv")))
        self.box.add_widget(Btn("Export JSON backup", bg=T("card2"),
                                on_release=lambda *_: self._do("json")))
        self.box.add_widget(Btn("Import JSON backup", bg=T("card2"),
                                on_release=lambda *_: self._import()))
        self.box.add_widget(Btn("Close", bg=T("card2"),
                                on_release=lambda *_: self.dismiss()))

    def _do(self, kind):
        home = os.path.expanduser("~")
        if kind == "csv":
            path = os.path.join(home, f"kiravault_transactions_{date.today().isoformat()}.csv")
            with open(path, "w") as f:
                f.write(self.app.db.export_csv())
        else:
            path = os.path.join(home, f"kiravault_backup_{date.today().isoformat()}.json")
            with open(path, "w") as f:
                json.dump(self.app.db.export_json(), f, indent=2, default=str)
        self.box.clear_widgets()
        self.box.add_widget(Lbl("Saved", 18, T("green"), True))
        self.box.add_widget(Lbl(path, 12, T("muted")))
        self.box.add_widget(Btn("OK", on_release=lambda *_: self.dismiss()))

    def _import(self):
        home = os.path.expanduser("~")

        def do_load(sel):
            if not sel:
                return
            try:
                with open(sel[0]) as f:
                    data = json.load(f)
                self.app.db.import_json(data)
                chooser_popup.dismiss()
                self.dismiss()
                self.app.refresh()
            except Exception as e:
                self.box.add_widget(Lbl(f"Error: {e}", 12, T("red")))

        chooser = FileChooserListView(path=home, filters=["*.json"])
        chooser.bind(on_submit=lambda _c, sel, *_: do_load(sel))
        chooser_popup = Popup(title="Select backup", content=chooser,
                              size_hint=(0.9, 0.9))
        chooser_popup.open()


# ------------------------------------------------------------------ pages
class Page(Screen):
    def __init__(self, app, **kw):
        super().__init__(**kw)
        self.app = app
        self._saved_y = 1.0

        self.scroll = ScrollView(do_scroll_x=False, bar_width=0)
        self.body = BoxLayout(
            orientation="vertical", size_hint_y=None, spacing=dp(12),
            padding=[dp(14), dp(14), dp(14), dp(100)],
        )
        self.body.bind(minimum_height=self.body.setter("height"))
        self.scroll.add_widget(self.body)
        self.add_widget(self.scroll)

    def refresh(self):
        self.body.clear_widgets()
        self.build(self.body)

    def on_pre_enter(self, *args):
        Clock.schedule_once(
            lambda *_: setattr(self.scroll, "scroll_y", self._saved_y), 0
        )

    def on_leave(self, *args):
        self._saved_y = self.scroll.scroll_y

    def build(self, b):
        raise NotImplementedError


class HomePage(Page):
    def build(self, b):
        app, s = self.app, self.app.db.summary()
        h = datetime.now().hour
        greet = "GOOD MORNING" if h < 12 else "GOOD AFTERNOON" if h < 17 else "GOOD EVENING"
        name = app.db.get_setting("user_name", "Kevin")
        b.add_widget(Row(Lbl(f"{greet}, {name.upper()}", 12, T("muted"), True, size_hint_x=0.75),
                         Btn("Settings", bg=T("card2"), size=11, h=32, size_hint_x=0.25,
                             on_release=lambda *_: app.go("settings"))))

        due = reminders.collect(app.work, app.db, lead=app.lead_days())
        if due:
            c = Card(bg=T("amber_bg"))
            c.add_widget(heading("Needs attention"))
            col = {"overdue": T("red"), "today": T("amber"), "soon": T("text")}
            for i in due[:6]:
                c.add_widget(Lbl(i["text"], 13, col[i["level"]], i["level"] != "soon"))
            if len(due) > 6:
                c.add_widget(Lbl(f"+{len(due) - 6} more", 12, T("muted")))
            b.add_widget(c)

        c = Card(stripe=T("blue"))
        c.add_widget(heading("Total available", T("blue")))
        c.add_widget(Lbl("UGX " + fmt(s["available"]), 28, T("text"), True))
        for a in s["accounts"]:
            c.add_widget(kvrow(a["name"], fmt(a["balance"]),
                               rcolor=T("red") if a["balance"] < 0 else T("text")))
        b.add_widget(c)

        c = Card(stripe=T("amber"))
        c.add_widget(heading("Committed", T("amber")))
        c.add_widget(Lbl("UGX " + fmt(s["committed"]), 22, T("amber"), True))
        c.add_widget(kvrow("Bills", fmt(s["groups"]["Bills"])))
        c.add_widget(kvrow("Debt", fmt(s["groups"]["Debt"])))
        c.add_widget(kvrow("Savings target", fmt(s["groups"]["Savings"])))
        b.add_widget(c)

        neg = s["shortfall"] > 0          # promised more than you have: show 0, in red
        c = Card(bg=T("red_bg") if neg else T("green_bg"), padding=dp(18),
                 stripe=T("red") if neg else T("green"))
        c.add_widget(heading("Safe to spend", T("red") if neg else T("green")))
        c.add_widget(Lbl("UGX " + fmt(s["safe"]), 36,
                         T("red") if neg else T("green"), True))
        c.add_widget(Lbl(
            "Available money minus everything you've already promised this month.",
            12, T("muted"),
        ))
        b.add_widget(c)

        left = Card(bg=T("teal_bg"))
        left.add_widget(heading("Expected income", T("teal")))
        left.add_widget(Lbl(short(s["expected"]), 20, T("teal"), True))
        left.add_widget(Lbl("Not counted until received", 11, T("muted")))
        right = Card(bg=T("purple_bg"))
        right.add_widget(heading("Pending bills", T("purple")))
        right.add_widget(Lbl(short(s["pending_bills"]), 20, T("purple"), True))
        right.add_widget(Lbl(f"{s['pending_bills_n']} unpaid", 11, T("muted")))
        b.add_widget(Row(left, right))

        c = Card(stripe=T("green"))
        c.add_widget(heading("Savings", T("green")))
        c.add_widget(Lbl("UGX " + fmt(s["savings"]), 20, T("green"), True))
        b.add_widget(c)

        wk = app.work
        act = [p for p in wk.projects() if p["status"] not in DONE]
        soon = [p for p in act if p["deadline"] and p["deadline"] <= (date.today() + timedelta(days=7)).isoformat()]
        unpaid = sum(wbal(p) for p in wk.projects() if p["status"] != "Cancelled")
        c = TapCard(on_release=lambda *_: app.go("work"), stripe=T("purple"))
        c.add_widget(heading("Work", T("purple")))
        c.add_widget(Lbl(f"{len(act)} active  |  {len(soon)} due within 7 days", 16, T("text"), True))
        c.add_widget(Lbl(f"UGX {fmt(unpaid)} still unpaid by clients (not counted until received)", 12, T("muted")))
        b.add_widget(c)

        c = TapCard(on_release=lambda *_: app.go("insights"), stripe=T("accent"))
        c.add_widget(heading("Assistant", T("accent")))
        for text, col in app.messages(s):
            c.add_widget(Lbl(text, 14, col))
        c.add_widget(Lbl("See all insights and charts >", 12, T("accent"), True))
        b.add_widget(c)



class MoneyOverview(Page):
    def build(self, b):
        app, db = self.app, self.app.db
        b.add_widget(title("My money", "blue"))

        c = Card()
        for a in db.accounts():
            c.add_widget(Row(
                Lbl(a["name"], 15, T("text"), size_hint_x=0.5),
                Lbl(fmt(a["balance"]), 15,
                    T("red") if a["balance"] < 0 else T("text"),
                    True, "right", size_hint_x=0.5),
            ))
        c.add_widget(kvrow("Total", "UGX " + fmt(db.total_available()),
                           16, T("text"), T("green"), True))
        b.add_widget(c)

        b.add_widget(sbtn("Transfer money", "blue", on_release=lambda *_: app.transfer_form()))

        b.add_widget(heading("Expected income (not yet available)"))
        exp = db.expected()
        if not exp:
            b.add_widget(Lbl(
                "Nothing expected. Add unpaid gigs here so they don't inflate your balance.",
                13, T("muted"),
            ))
        for e in exp:
            c = Card()
            c.add_widget(Row(
                Lbl(e["project"] or e["source"], 15, T("text"), True, size_hint_x=0.6),
                Lbl("UGX " + fmt(e["amount"]), 15, T("amber"), True,
                    "right", size_hint_x=0.4),
            ))
            if e["client"]:
                c.add_widget(Lbl(f"Client: {e['client']}", 12, T("muted")))
            c.add_widget(Lbl(
                f"Expected: {pretty_date(e['expected_date'])}  |  Awaiting payment",
                12, T("muted"),
            ))
            c.add_widget(Row(
                Btn("Mark received", bg=T("green_bg"), fg=T("green"), size=12, h=36,
                    on_release=lambda _b, e=e: app.receive_form(e)),
                Btn("Delete", bg=T("card2"), size=12, h=36,
                    on_release=lambda _b, e=e: ConfirmPopup(
                        "Delete this expected income?",
                        lambda: db.delete_expected(e["id"]),
                        app.refresh).open()),
            ))
            b.add_widget(c)

        b.add_widget(tbtn("+ Expected income", "teal",
                         on_release=lambda *_: app.expected_form()))

        ym = date.today().strftime("%Y-%m")
        rows = [t for t in db.transactions()
                if t["type"] == "income" and t["date"][:7] == ym]
        b.add_widget(heading("Received this month"))
        if not rows:
            b.add_widget(Lbl("No income received yet this month.", 13, T("muted")))
        for t in rows:
            c = Card()
            c.add_widget(Row(
                Lbl(tx_title(t), 14, T("text"), size_hint_x=0.6),
                Lbl("+" + fmt(t["amount"]), 14, T("green"), True,
                    "right", size_hint_x=0.4),
            ))
            c.add_widget(Lbl(
                f"{t['source']}  |  {t['account']}  |  Received",
                11, T("muted"),
            ))
            b.add_widget(c)


class TxPage(Page):
    ftype = "All"
    facc = "All"
    query = ""

    def build(self, b):
        app, db = self.app, self.app.db
        b.add_widget(title("Transactions", "blue"))
        b.add_widget(Row(Btn("+ Income", bg=T("green_bg"), fg=T("green"), size=13, h=40,
                             on_release=lambda *_: app.income_form()),
                         Btn("+ Expense", bg=T("red_bg"), fg=T("red"), size=13, h=40,
                             on_release=lambda *_: app.expense_form())))

        # search input (persists across refresh)
        self.search_inp = make_input(self.query, hint="Search description, category, amount...")
        self.search_inp.bind(text=self._on_search)
        b.add_widget(self.search_inp)

        b.add_widget(chip_row(["All", "Income", "Expenses", "Commitments"],
                              self.ftype, self._set_type))
        b.add_widget(chip_row(["All"] + ACCOUNTS, self.facc, self._set_acc))

        # list container we rebuild in isolation
        self.list_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                  spacing=dp(10))
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        b.add_widget(self.list_box)
        self._render_list()

    def _set_type(self, v):
        self.ftype = v
        self._render_list()

    def _set_acc(self, v):
        self.facc = v
        self._render_list()

    def _on_search(self, _w, text):
        if text == self.query:
            return
        self.query = text
        Clock.unschedule(self._debounced)
        Clock.schedule_once(self._debounced, 0.15)

    def _debounced(self, *_):
        self._render_list()

    def _render_list(self):
        self.list_box.clear_widgets()
        db = self.app.db
        rows = db.transactions()
        if self.ftype == "Income":
            rows = [t for t in rows if t["type"] == "income"]
        elif self.ftype == "Expenses":
            rows = [t for t in rows if t["type"] == "expense"]
        elif self.ftype == "Commitments":
            rows = [t for t in rows if t["commitment_id"]]
        if self.facc != "All":
            rows = [t for t in rows
                    if t["account"] == self.facc or t["to_account"] == self.facc]
        if self.query:
            q = self.query.lower()
            def ok(t):
                hay = " ".join(str(t.get(k) or "") for k in
                               ("description", "category", "source", "account",
                                "to_account"))
                return q in hay.lower() or q in f"{t['amount']:.0f}"
            rows = [t for t in rows if ok(t)]

        if not rows:
            self.list_box.add_widget(Lbl("No transactions match.", 14, T("muted")))
            return

        last = None
        for t in rows:
            if t["date"] != last:
                last = t["date"]
                d = date.fromisoformat(t["date"])
                self.list_box.add_widget(
                    heading(f"{calendar.month_name[d.month]} {d.day}, {d.year}")
                )
            inc = t["type"] == "income"
            sign = "+" if inc else "-" if t["type"] in ("expense", "saving") else ""
            col = T("green") if inc else T("red") if t["type"] in ("expense", "saving") else T("text")
            sub = (t["source"] if inc else t["category"]) or t["type"].title()
            where = (f"{t['account']} -> {t['to_account']}"
                     if t["type"] == "transfer" else t["account"])

            c = TapCard(on_release=lambda _b, t=t: ConfirmPopup(
                f"Delete '{tx_title(t)}' (UGX {fmt(t['amount'])})? "
                f"Your balance will be reversed.",
                lambda: db.delete_tx(t["id"]), self.app.refresh).open())
            c.add_widget(Row(
                Lbl(tx_title(t), 15, T("text"), True, size_hint_x=0.55),
                Lbl(f"{sign}{fmt(t['amount'])}", 15, col, True, "right",
                    size_hint_x=0.45),
            ))
            c.add_widget(Row(
                Lbl(sub, 11, T("muted"), size_hint_x=0.5),
                Lbl(where, 11, T("muted"), halign="right", size_hint_x=0.5),
            ))
            self.list_box.add_widget(c)


COMMIT_TITLES = {"overdue": "Overdue", "due_soon": "Due soon", "upcoming": "Upcoming", "paid": "Paid"}
COMMIT_COLORS = {"overdue": "red", "due_soon": "amber", "upcoming": "muted", "paid": "green"}


def commitment_groups(db, savings):
    """Commitments bucketed by state. savings=True -> only Savings ones; False -> everything else."""
    today = date.today()
    groups = {"overdue": [], "due_soon": [], "upcoming": [], "paid": []}
    for c in db.commitments():
        if (c["kind"] == "Savings") != savings:
            continue
        st = commit_state(c)
        if st == "paid" and c["paid_date"] and \
                (today - date.fromisoformat(c["paid_date"])).days > 31:
            continue
        groups[st].append(c)
    return groups


def commitment_card(app, c, st, pay_label="Pay", edit_fn=None):
    db = app.db
    rem = c["amount"] - c["paid_amount"]
    card = Card()
    card.add_widget(Row(
        Lbl(c["title"], 16, T("text"), True, size_hint_x=0.6),
        Lbl("UGX " + short(c["amount"]), 16, T("text"), True, "right", size_hint_x=0.4),
    ))
    card.add_widget(Lbl(f"{due_text(c)}  |  {pretty_date(c['due_date'])}",
                        13, T(COMMIT_COLORS[st]), True))
    meta = [c["kind"]]
    if c["account"]:
        meta.append(c["account"])
    if c["recurring"] != "None":
        meta.append(c["recurring"])
    card.add_widget(Lbl("  |  ".join(meta), 12, T("muted")))

    frac = 1.0 if st == "paid" else (min(c["paid_amount"] / c["amount"], 1.0) if c["amount"] else 0.0)
    pct = 100 if st == "paid" else int(frac * 100)          # never rounds up to 100% early
    verb = "saved" if c["kind"] == "Savings" else "paid"
    line = "Paid in full" if st == "paid" else \
        f"UGX {short(c['paid_amount'])} {verb}  |  UGX {short(rem)} left"
    card.add_widget(Row(Lbl(line, 12, T("muted"), size_hint_x=0.72),
                        Lbl(f"{pct}% {verb}" if pct < 100 else f"100% {verb}", 13, T("green"), True,
                            halign="right", size_hint_x=0.28)))
    card.add_widget(Bar(frac, T("green")))

    btns = []
    if st != "paid":
        pay_btn = Btn(pay_label, bg=T("green_bg"), fg=T("green"), size=13, h=38)

        def _pay(_b, c=c, btn=pay_btn):
            if btn.disabled:
                return
            btn.disabled = True
            btn.opacity = 0.6
            app.pay_form(c)
        pay_btn.bind(on_release=_pay)
        btns.append(pay_btn)
        btns.append(Btn("Edit", bg=T("card2"), size=13, h=38,
                        on_release=lambda _b, c=c: (edit_fn or app.commitment_form)(c["id"])))
    btns.append(Btn("Delete", bg=T("card2"), size=13, h=38,
                    on_release=lambda _b, c=c: ConfirmPopup(
                        f"Delete '{c['title']}'?",
                        lambda: db.delete_commitment(c["id"]),
                        app.refresh).open()))
    card.add_widget(Row(*btns))
    return card


class CommitPage(Page):
    """Bills & Debts. Savings commitments live on the Savings tab, not here."""
    def build(self, b):
        app, db = self.app, self.app.db
        s = db.summary()
        due = s["groups"]["Bills"] + s["groups"]["Debt"]
        b.add_widget(title("Bills & Debts", "amber"))
        b.add_widget(Lbl(f"UGX {fmt(due)} due this month", 14, T("amber")))
        b.add_widget(sbtn("+ Add bill or debt", "amber",
                          on_release=lambda *_: app.bill_form()))

        groups = commitment_groups(db, savings=False)
        if not any(groups.values()):
            b.add_widget(Lbl("No bills or debts yet. Add rent, bills, debts, subscriptions "
                             "or planned purchases.", 14, T("muted")))
        for st in ("overdue", "due_soon", "upcoming", "paid"):
            if not groups[st]:
                continue
            b.add_widget(heading(COMMIT_TITLES[st]))
            for c in groups[st]:
                b.add_widget(commitment_card(app, c, st, edit_fn=app.bill_form))


class GoalsPage(Page):
    def build(self, b):
        app, db = self.app, self.app.db
        b.add_widget(title("Savings goals", "green"))
        b.add_widget(sbtn("+ New goal", "green", on_release=lambda *_: app.goal_form()))

        goals = db.goals()
        if not goals:
            b.add_widget(Lbl(
                "Create a goal (e.g. New Camera) and set a monthly amount to save.",
                14, T("muted"),
            ))
        for g in goals:
            pct = g["saved"] / g["target"] if g["target"] else 0
            c = Card()
            c.add_widget(Row(
                Lbl(g["name"], 17, T("text"), True, size_hint_x=0.6),
                Lbl(f"{pct * 100:.0f}%", 17, T("green"), True,
                    "right", size_hint_x=0.4),
            ))
            c.add_widget(Lbl(f"UGX {short(g['saved'])} / {short(g['target'])}",
                             14, T("muted")))
            c.add_widget(Bar(pct, T("green")))
            line = f"UGX {short(max(g['target'] - g['saved'], 0))} remaining"
            if g["monthly_target"]:
                line += f"  |  Monthly target: UGX {short(g['monthly_target'])}"
            c.add_widget(Lbl(line, 12, T("muted")))

            add_btn = Btn("Add savings", bg=T("green_bg"), fg=T("green"),
                          size=13, h=38)
            add_btn.bind(on_release=lambda _b, g=g, btn=add_btn:
                         self._contribute_guarded(g, btn))
            c.add_widget(Row(
                add_btn,
                Btn("Delete", bg=T("card2"), size=13, h=38,
                    on_release=lambda _b, g=g: ConfirmPopup(
                        f"Delete goal '{g['name']}'?",
                        lambda: db.delete_goal(g["id"]),
                        app.refresh).open()),
            ))
            b.add_widget(c)

        groups = commitment_groups(db, savings=True)
        if any(groups.values()):
            b.add_widget(title("Savings commitments", "green"))
            for st in ("overdue", "due_soon", "upcoming", "paid"):
                if not groups[st]:
                    continue
                b.add_widget(heading(COMMIT_TITLES[st]))
                for c in groups[st]:
                    b.add_widget(commitment_card(app, c, st, pay_label="Save",
                                                 edit_fn=app.savings_commit_form))

    def _contribute_guarded(self, g, btn):
        if btn.disabled:
            return
        btn.disabled = True
        btn.opacity = 0.6
        self.app.contribute_form(g)


class InsightsPage(Page):
    def build(self, b):
        app, db = self.app, self.app.db
        today = date.today()
        ym = today.strftime("%Y-%m")
        # budgets
        b.add_widget(heading(f"{calendar.month_name[today.month]} budget"))
        spent = dict(db.spending_by_category(ym))
        budgets = db.budgets()
        if not budgets:
            b.add_widget(Lbl(
                "Set category budgets to see how close you are to each limit.",
                13, T("muted"),
            ))
        for bd in budgets:
            used = spent.get(bd["category"], 0)
            frac = used / bd["limit_amount"]
            col = T("red") if frac >= 1 else T("amber") if frac >= 0.8 else T("green")
            c = Card()
            c.add_widget(Row(
                Lbl(bd["category"], 15, T("text"), True, size_hint_x=0.5),
                Lbl(f"{short(used)} / {short(bd['limit_amount'])}",
                    14, col, True, "right", size_hint_x=0.5),
            ))
            c.add_widget(Bar(frac, col))
            if frac >= 1:
                c.add_widget(Lbl("Over budget", 12, T("red")))
            elif frac >= 0.8:
                c.add_widget(Lbl("Approaching limit", 12, T("amber")))
            b.add_widget(c)
        b.add_widget(Btn("Set a budget", bg=T("card2"),
                         on_release=lambda *_: app.budget_form()))

        # where money goes
        b.add_widget(heading("Where your money goes"))
        cats = db.spending_by_category(ym)
        if not cats:
            b.add_widget(Lbl("No spending recorded this month.", 13, T("muted")))
        else:
            top = cats[0][1]
            c = Card()
            for name, amt in cats:
                c.add_widget(kvrow(name, short(amt), 14, T("text"), T("text"), True))
                c.add_widget(Bar(amt / top, T("accent")))
            b.add_widget(c)

        # forecast
        b.add_widget(heading("Cash-flow forecast (30 days)"))
        f = db.forecast()
        c = Card()
        c.add_widget(kvrow(today.strftime("%d %b") + "  (today)",
                           short(f["start"]), 14, T("text"), T("text"), True))
        for d, label, delta, bal in f["events"]:
            c.add_widget(Row(
                Lbl(f"{d.day} {calendar.month_abbr[d.month]}  {label}",
                    13, T("muted"), size_hint_x=0.5),
                Lbl(("+" if delta > 0 else "-") + short(abs(delta)),
                    13, T("green") if delta > 0 else T("red"),
                    halign="right", size_hint_x=0.2),
                Lbl(short(bal), 14,
                    T("red") if bal < 0 else T("text"),
                    True, "right", size_hint_x=0.3),
            ))
        low_col = T("red") if f["lowest"] < 0 else T("green")
        c.add_widget(Lbl(f"Lowest projected balance: UGX {short(f['lowest'])}",
                         14, low_col, True))
        if f["lowest"] < 0:
            c.add_widget(Lbl(
                "You may not have enough to cover everything coming up.",
                12, T("red"),
            ))
        b.add_widget(c)


# -------------------------------------------------------------------- app
# ============================================================ KiraVault: work, calendar, settings
from kivy.uix.gridlayout import GridLayout
from kivy.utils import get_color_from_hex as hx
from theme import mix


def project_card(app, p, back="work"):
    late = p["status"] not in DONE and p["deadline"] and p["deadline"] < date.today().isoformat()
    col = "red" if late else STATUS_COLOR.get(p["status"], "accent")
    c = TapCard(on_release=lambda *_: app.open_project(p["id"], back), stripe=T(col))
    c.add_widget(Row(Lbl(p["title"], 16, T("text"), True, size_hint_x=0.7),
                     Lbl(f"{p['progress'] or 0}%", 14, T(col), True, halign="right", size_hint_x=0.3)))
    c.add_widget(Lbl("  |  ".join(x for x in (p["client"], p["work_type"], p["status"]) if x), 12, T("muted")))
    c.add_widget(Bar((p["progress"] or 0) / 100, T(col)))
    if p["source"] != "Job":
        c.add_widget(Lbl(f"UGX {fmt(p['amount'])}  |  Paid {fmt(p['paid'])}", 12, T("text")))
    if p["deadline"]:
        c.add_widget(Lbl(("OVERDUE  " if late else "Due ") + pretty_date(p["deadline"]), 12,
                         T("red") if late else T("muted")))
    return c


class WorkPage(Page):
    tab, flt = "Projects", "All"

    def _set(self, key, v):
        setattr(self, key, v)
        self.refresh()

    def build(self, b):
        app, w = self.app, self.app.work
        b.add_widget(title("Work", "purple"))
        b.add_widget(tab_row(["Projects", "Tasks", "Clients"], self.tab, lambda v: self._set("tab", v), "purple"))
        if self.tab == "Projects":
            b.add_widget(chip_row(["All", "Freelance", "Job", "Unpaid", "Archive"], self.flt,
                                  lambda v: self._set("flt", v), "purple"))
            b.add_widget(sbtn("+ New project", "purple", on_release=lambda *_: app.project_form()))
            n = 0
            for p in w.projects():
                arch = is_archived(p)
                ok = arch if self.flt == "Archive" else (
                    not arch and (self.flt not in SOURCES or p["source"] == self.flt)
                    and (self.flt != "Unpaid" or wbal(p) > 0))
                if ok:
                    n += 1
                    b.add_widget(project_card(app, p))
            if not n:
                b.add_widget(Lbl("Nothing here yet. Tap + New project.", 14, T("muted")))
        elif self.tab == "Tasks":
            ts = w.open_tasks()
            if not ts:
                b.add_widget(Lbl("No open tasks. Add them inside a project.", 14, T("muted")))
            for t in ts:
                c = TapCard(on_release=lambda _b, t=t: (w.toggle_task(t["id"], True), self.refresh()),
                            stripe=T("purple"))
                c.add_widget(Lbl("[ ]  " + t["title"], 15, T("text"), True))
                c.add_widget(Lbl(t["project"] + "  |  tap to complete", 11, T("muted")))
                b.add_widget(c)
        else:
            cls = w.clients_summary()
            for cl in cls:
                c = TapCard(on_release=lambda _b, cid=cl["id"]: app.open_client(cid), stripe=T("teal"))
                c.add_widget(Row(Lbl(cl["name"], 16, T("text"), True, size_hint_x=0.65),
                                 Lbl(">", 16, T("muted"), True, halign="right", size_hint_x=0.35)))
                c.add_widget(Lbl(f"{cl['n']} project(s)  |  UGX {fmt(cl['billed'])} billed", 12, T("muted")))
                if cl["outstanding"] > 0:
                    c.add_widget(Lbl(f"UGX {fmt(cl['outstanding'])} outstanding", 12, T("red"), True))
                elif cl["billed"] > 0:
                    c.add_widget(Lbl("All paid up", 12, T("green"), True))
                b.add_widget(c)
            if not cls:
                b.add_widget(Lbl("Clients are added automatically with projects.", 14, T("muted")))


class ClientPage(Page):
    cid = None

    def build(self, b):
        app, w = self.app, self.app.work
        cl = w.client(self.cid) if self.cid else None
        if not cl:
            return b.add_widget(Lbl("Client not found.", 14, T("muted")))
        b.add_widget(Row(Btn("< Back", bg=T("card2"), size=12, h=36, size_hint_x=0.3,
                             on_release=lambda *_: app.go("work")),
                         Lbl("Client", 18, T("text"), True, halign="center", size_hint_x=0.7)))
        projs = w.client_projects(cl["id"])
        t = w._totals(projs)
        c = Card()
        c.add_widget(Lbl(cl["name"], 20, T("text"), True))
        sub = f"{t['n']} project(s), {t['active']} active"
        if t["since"]:
            sub += f"  |  since {pretty_date(t['since'])}"
        c.add_widget(Lbl(sub, 12, T("muted")))
        c.add_widget(kvrow("Total billed", "UGX " + fmt(t["billed"])))
        c.add_widget(kvrow("Total paid", "UGX " + fmt(t["paid"]), rcolor=T("green")))
        c.add_widget(kvrow("Outstanding", "UGX " + fmt(t["outstanding"]),
                           rcolor=T("red") if t["outstanding"] else T("text"), bold=True))
        if t["billed"] > 0:
            c.add_widget(Bar(min(1.0, t["paid"] / t["billed"]), T("green")))
            c.add_widget(Lbl(f"{round(100 * min(1.0, t['paid'] / t['billed']))}% of billed work paid  |  "
                             f"average project UGX {fmt(t['average'])}", 11, T("muted")))
        b.add_widget(c)
        owing = [p for p in projs if p["status"] != "Cancelled" and wbal(p) > 0]
        if owing:
            oc = Card()
            oc.add_widget(heading("Unpaid, to chase"))
            for p in sorted(owing, key=lambda p: (p["deadline"] is None, p["deadline"] or "")):
                oc.add_widget(Row(Lbl(p["title"], 13, T("text"), size_hint_x=0.6),
                                  Lbl("UGX " + fmt(wbal(p)), 13, T("red"), True, halign="right", size_hint_x=0.4)))
            b.add_widget(oc)
        b.add_widget(heading("All projects"))
        if not projs:
            b.add_widget(Lbl("No projects for this client yet.", 14, T("muted")))
        for p in projs:
            b.add_widget(project_card(app, p, back="client"))


class ProjectPage(Page):
    pid, back = None, "work"

    def build(self, b):
        app, w = self.app, self.app.work
        p = w.project(self.pid) if self.pid else None
        if not p:
            return b.add_widget(Lbl("Project not found.", 14, T("muted")))
        pid, bal = p["id"], wbal(p)
        late = p["status"] not in DONE and p["deadline"] and p["deadline"] < date.today().isoformat()
        col = "red" if late else STATUS_COLOR.get(p["status"], "accent")
        b.add_widget(Row(Btn("< Back", bg=tint("purple"), fg=T("purple"), size=12, h=36, size_hint_x=0.3,
                             on_release=lambda *_: app.go(self.back)),
                         Lbl("Project details", 18, T("text"), True, halign="center", size_hint_x=0.7)))
        c = Card(stripe=T(col))
        c.add_widget(Lbl(p["title"], 20, T("text"), True))
        c.add_widget(Lbl("  |  ".join(x for x in (p["client"], p["work_type"], p["source"],
                                                  p["priority"] + " priority") if x), 12, T("muted")))
        c.add_widget(Lbl(f"Cancelled at {p['progress'] or 0}%" if p["status"] == "Cancelled"
                         else f"{p['progress'] or 0}% complete", 13, T(col), True))
        c.add_widget(Bar((p["progress"] or 0) / 100, T(col)))
        c.add_widget(kvrow("Quoted", "UGX " + fmt(p["amount"])))
        c.add_widget(kvrow("Paid", "UGX " + fmt(p["paid"]), rcolor=T("green")))
        c.add_widget(kvrow("Outstanding", "UGX " + fmt(bal), rcolor=T("red") if bal else T("text"), bold=True))
        c.add_widget(kvrow("Start / Deadline", f"{pretty_date(p['start_date'])} / {pretty_date(p['deadline'])}"))
        if p["est_days"]:
            left = p["est_days"] * (1 - (p["progress"] or 0) / 100)
            c.add_widget(kvrow("Estimated effort", f"{dd(p['est_days'])} days ({dd(left)} left)"))
        b.add_widget(c)
        b.add_widget(chip_row(STATUSES, p["status"], lambda v: (w.set_status(pid, v), self.refresh())))
        b.add_widget(Row(
            tbtn("Edit", "blue", on_release=lambda *_: app.project_form(pid)),
            Btn("Add payment", bg=T("green_bg"), fg=T("green"), on_release=lambda *_: app.work_payment_form(pid)),
            Btn("Delete", bg=T("red_bg"), fg=T("red"), on_release=lambda *_: ConfirmPopup(
                f"Delete '{p['title']}' with its tasks and payments?", lambda: w.delete_project(pid),
                lambda: app.go(self.back)).open())))
        ts = w.tasks(pid)
        tc = Card(stripe=T("purple"))
        tc.add_widget(heading(f"Tasks  {sum(t['done'] for t in ts)}/{len(ts)} done", T("purple")))
        for t in ts:
            tc.add_widget(Row(
                Btn(("[x]  " if t["done"] else "[ ]  ") + t["title"], size=13, h=38, size_hint_x=0.85,
                    bg=T("green_bg") if t["done"] else T("card2"), fg=T("muted") if t["done"] else T("text"),
                    on_release=lambda _b, t=t: (w.toggle_task(t["id"], not t["done"]), self.refresh())),
                Btn("x", bg=T("card2"), fg=T("muted"), h=38, size_hint_x=0.15,
                    on_release=lambda _b, t=t: (app.delete_with_undo("Task deleted", lambda: w.delete_task(t["id"])),
                                                self.refresh()))))
        tc.add_widget(tbtn("+ Add task", "purple", on_release=lambda *_: app.task_form(pid)))
        b.add_widget(tc)
        pc = Card(stripe=T("green"))
        pc.add_widget(heading("Payments", T("green")))
        pays = w.payments(pid)
        if not pays:
            pc.add_widget(Lbl("No payments recorded yet.", 12, T("muted")))
        for r in pays:
            msg = f"Delete the UGX {fmt(r['amount'])} payment?"
            if r["tx_id"]:
                msg += " Its entry in Finance is removed too."
            pc.add_widget(Row(
                Btn(f"{pretty_date(r['paid_on'])}   UGX {fmt(r['amount'])}", size=13, h=38, size_hint_x=0.85,
                    bg=T("green_bg"), fg=T("green"),
                    on_release=lambda _b, r=r: app.payment_edit_form(r["id"])),
                Btn("x", bg=T("card2"), fg=T("muted"), h=38, size_hint_x=0.15,
                    on_release=lambda _b, r=r, msg=msg: ConfirmPopup(
                        msg, lambda: app.payment_delete(r["id"]), self.refresh).open())))
        if pays:
            pc.add_widget(Lbl("Tap a payment to edit its amount or date.", 11, T("muted")))
        b.add_widget(pc)


class CalendarPage(Page):
    cy, cm, csel = date.today().year, date.today().month, date.today()

    def _move(self, n):
        mm = self.cm + n
        self.cy, self.cm = self.cy + (mm - 1) // 12, (mm - 1) % 12 + 1
        self.csel = date(self.cy, self.cm, 1)
        self.refresh()

    def _pick(self, d):
        self.csel, self.cy, self.cm = d, d.year, d.month
        self.refresh()

    def build(self, b):
        app, w = self.app, self.app.work
        b.add_widget(title("Calendar", "teal"))
        late = w.overdue()
        if late:
            b.add_widget(Lbl(f"{len(late)} overdue: " + ", ".join(p["title"] for p in late[:3])
                             + (" ..." if len(late) > 3 else ""), 12, T("red"), True))
        b.add_widget(Row(
            tbtn("<", "teal", h=38, size_hint_x=0.2, on_release=lambda *_: self._move(-1)),
            Lbl(f"{calendar.month_name[self.cm]} {self.cy}", 17, T("teal"), True, halign="center", size_hint_x=0.6),
            tbtn(">", "teal", h=38, size_hint_x=0.2, on_release=lambda *_: self._move(1))))
        b.add_widget(Row(*[Lbl(n, 11, T("muted"), True, halign="center")
                           for n in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")]))
        days = [d for wk in calendar.Calendar(0).monthdatescalendar(self.cy, self.cm) for d in wk]
        load = w.load(days)
        g = GridLayout(cols=7, spacing=dp(4), size_hint_y=None, row_default_height=dp(42), row_force_default=True)
        g.bind(minimum_height=g.setter("height"))
        for d in days:
            score, on = load[d][0], d == self.csel
            bg = T("teal") if on else T("card") if score <= 0 else mix(hx(level(score)[2]), T("card"), 0.3 if score < 1.0 else 0.55)
            g.add_widget(Btn(str(d.day), bg=bg, size=13, h=42, on_release=lambda _b, d=d: self._pick(d),
                             fg=(1, 1, 1, 1) if on else T("text") if d.month == self.cm else T("muted")))
        b.add_widget(g)
        b.add_widget(Lbl("Green = light   Amber = moderate   Red = a full day or more", 11, T("muted"), halign="center"))
        noest = [p for p in w.projects() if p["status"] not in DONE and not p["est_days"]]
        if noest:
            b.add_widget(Lbl(f"{len(noest)} active project{'s' if len(noest) != 1 else ''} without an "
                             "estimate (counted as a part day). Add one in Edit for a more accurate forecast.",
                             11, T("muted"), halign="center"))
        b.add_widget(Lbl(self.csel.strftime("%A, %d %B %Y"), 16, T("teal"), True))
        score, items = load.get(self.csel) or w.load([self.csel])[self.csel]
        b.add_widget(Lbl("Workload: " + level(score)[0]
                         + (f" ({score * 100:.0f}% of a full day)" if score > 0 else ""), 12, T("muted")))
        day_iso = self.csel.isoformat()
        b.add_widget(Row(
            tbtn("+ Project from this day", "purple", size=12, h=38,
                on_release=lambda *_: app.project_form(None, start=day_iso)),
            tbtn("+ Bill due this day", "amber", size=12, h=38,
                on_release=lambda *_: app.commitment_form(None, due=day_iso))))
        b.add_widget(sbtn("Can I take another job?", "teal", size=13, h=40,
                         on_release=lambda *_: app.job_check_form()))
        iso = self.csel.isoformat()
        bills = [c for c in app.db.commitments() if c["due_date"] == iso and c["status"] != "paid"]
        for c in bills:
            b.add_widget(Lbl(f"Bill due: {c['title']}  UGX {short(c['amount'] - c['paid_amount'])}", 13, T("amber"), True))
        for p in items:
            b.add_widget(project_card(app, p))
        if not items and not bills:
            b.add_widget(Lbl("Nothing scheduled.", 13, T("muted")))


class MoneyHub(Page):
    """Finance tab: one place for accounts, history, bills & debts, savings and reports."""
    tab = "Overview"
    TABS = {"Overview": MoneyOverview, "History": TxPage, "Bills & Debts": CommitPage,
            "Savings": GoalsPage, "Budget & Reports": InsightsPage}

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        self.subs = {}

    def _pick(self, t):
        self.tab = t
        self.refresh()

    def build(self, b):
        b.add_widget(title("Finance", "blue"))
        b.add_widget(chip_row(list(self.TABS), self.tab, self._pick, "blue"))
        if self.tab not in self.subs:
            self.subs[self.tab] = self.TABS[self.tab](self.app, name="_" + self.tab)
        self.subs[self.tab].build(b)


# ------------------------------------------------------------- Insights (assistant) tab
LEVEL_STYLE = {"warn": ("red_bg", "red", "!"), "good": ("green_bg", "green", "+"),
               "tip": ("accent_bg", "accent", "i"), "info": ("card", "muted", "i")}


SEG_COLORS = {"Financial": "blue", "Work": "purple"}


def segmented(options, selected, on_pick):
    """Rounded two-way switch, like Financial | Work in the design."""
    box = Card(orientation="horizontal", padding=dp(4), spacing=dp(4), radius=16)
    for o in options:
        on, k = o == selected, SEG_COLORS.get(o, "accent")
        box.add_widget(Btn(o, bg=T(k) if on else T("card"), fg=(1, 1, 1, 1) if on else T(k),
                           size=14, h=40, on_release=lambda _b, o=o: on_pick(o)))
    return box


def insight_card(ins, hero=False):
    bg, fg, glyph = LEVEL_STYLE[ins["level"]]
    c = Card(bg=T(bg), orientation="horizontal", spacing=dp(12), padding=dp(14))
    badge = Badge(glyph, T(fg))
    badge.pos_hint = {"top": 1}
    c.add_widget(badge)
    col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(3))
    col.bind(minimum_height=col.setter("height"))
    if hero:
        col.add_widget(heading("Smart insight"))
    col.add_widget(Lbl(ins["title"], 15, T("text"), True))
    col.add_widget(Lbl(ins["text"], 13, T("text") if hero else T("muted")))
    c.add_widget(col)
    return c


def stat_tile(label, value, key="text"):
    c = Card(bg=tint(key, 0.14) if key != "text" else None)
    c.add_widget(heading(label, T(key) if key != "text" else None))
    c.add_widget(Lbl(value, 20, T(key), True))
    return c


MAX_COMPARE = 6


def default_compare(today=None, n=3):
    """The last n months, oldest first, as [(year, month)]."""
    today = today or date.today()
    y, m, out = today.year, today.month, []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def month_cols(months):
    """Column captions: 'OCT', or 'OCT 25' when the months span more than one year."""
    multi = len({y for y, _ in months}) > 1
    return [calendar.month_abbr[m].upper() + (f" {y % 100:02d}" if multi else "") for y, m in months]


def compare_card(heading_text, key, months, rows, custom, on_choose, on_reset, note=""):
    """rows: [(label, [text per month], [colour per month])]."""
    n = len(months)
    lw = 0.31 if n <= 3 else 0.25
    cw = (1 - lw) / n
    fs = 13 if n <= 4 else 11
    c = Card(stripe=T(key))
    c.add_widget(Lbl(heading_text, 16, T("text"), True))
    c.add_widget(Lbl("Custom months" if custom else "Last 3 months", 12, T("muted")))
    c.add_widget(Row(Lbl("", 11, size_hint_x=lw),
                     *[Lbl(t, 10 if n > 4 else 11, T("muted"), True, "right", size_hint_x=cw)
                       for t in month_cols(months)], spacing=dp(2)))
    for label, vals, cols in rows:
        c.add_widget(Row(Lbl(label, fs, T("muted"), size_hint_x=lw),
                         *[Lbl(v, fs, col, True, "right", size_hint_x=cw) for v, col in zip(vals, cols)],
                         spacing=dp(2)))
    if note:
        c.add_widget(Lbl(note, 11, T("muted")))
    btns = [tbtn("Choose months", key, size=12, h=38, on_release=on_choose)]
    if custom:
        btns.append(tbtn("Last 3 months", key, size=12, h=38, on_release=on_reset))
    c.add_widget(Row(*btns))
    return c


class MonthPickerPopup(BasePopup):
    """Pick 2 to MAX_COMPARE months from any year."""
    def __init__(self, selected, on_apply, min_year, max_n=MAX_COMPARE):
        super().__init__()
        self.today = date.today()
        self.sel, self.on_apply, self.min_year, self.max_n = set(selected), on_apply, min_year, max_n
        self.year = max([y for y, _ in selected] or [self.today.year])
        self.msg = ""
        self.box.add_widget(Lbl("Choose months to compare", 18, T("text"), True))
        self.box.add_widget(Lbl(f"Pick 2 to {max_n} months, from any year.", 12, T("muted")))
        self.body = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self.body.bind(minimum_height=self.body.setter("height"))
        self.box.add_widget(self.body)
        self._draw()

    def _yr(self, d):
        self.year += d
        self._draw()

    def _toggle(self, ym):
        if ym in self.sel:
            self.sel.discard(ym)
            self.msg = ""
        elif len(self.sel) >= self.max_n:
            self.msg = f"You can compare up to {self.max_n} months. Untick one first."
        else:
            self.sel.add(ym)
            self.msg = ""
        self._draw()

    def _reset(self, *_):
        self.sel, self.msg = set(default_compare()), ""
        self.year = self.today.year
        self._draw()

    def _apply(self, *_):
        if len(self.sel) < 2:
            self.msg = "Pick at least 2 months."
            self._draw()
            return
        self.dismiss()
        self.on_apply(sorted(self.sel))

    def _draw(self):
        b, t = self.body, self.today
        b.clear_widgets()
        b.add_widget(Row(
            Btn("<", bg=T("card2"), size=16, h=40, size_hint_x=0.2,
                disabled=self.year <= self.min_year, on_release=lambda *_: self._yr(-1)),
            Lbl(str(self.year), 18, T("text"), True, "center", size_hint_x=0.6),
            Btn(">", bg=T("card2"), size=16, h=40, size_hint_x=0.2,
                disabled=self.year >= t.year, on_release=lambda *_: self._yr(1)),
        ))
        g = GridLayout(cols=4, spacing=dp(6), size_hint_y=None,
                       row_default_height=dp(42), row_force_default=True)
        g.bind(minimum_height=g.setter("height"))
        for m in range(1, 13):
            ym = (self.year, m)
            if ym > (t.year, t.month):
                g.add_widget(Btn(calendar.month_abbr[m], bg=T("card2"), fg=T("muted"),
                                 size=13, h=42, disabled=True))
                continue
            on = ym in self.sel
            g.add_widget(Btn(calendar.month_abbr[m], bg=T("blue") if on else tint("blue"),
                             fg=(1, 1, 1, 1) if on else T("blue"), size=13, h=42,
                             on_release=lambda _b, ym=ym: self._toggle(ym)))
        b.add_widget(g)
        chosen = ", ".join(f"{calendar.month_abbr[m]} {y}" for y, m in sorted(self.sel)) or "None yet"
        b.add_widget(Lbl("Selected: " + chosen, 12, T("text")))
        if self.msg:
            b.add_widget(Lbl(self.msg, 12, T("red")))
        b.add_widget(Row(Btn("Cancel", bg=T("card2"), size=13, h=42, on_release=lambda *_: self.dismiss()),
                         Btn("Last 3", bg=T("card2"), size=13, h=42, on_release=self._reset),
                         Btn("Compare", size=13, h=42, on_release=self._apply)))


class AssistantPage(Page):
    """Bottom-nav 'Insights': plain-language observations plus charts, for money or work."""
    mode = "Financial"
    cmp_months = None            # None = default (last 3 months); else a sorted [(year, month)]

    def _pick(self, m):
        self.mode = m
        self.refresh()

    # ---- month comparison (shared by Financial and Work) ----
    def _cmp_list(self):
        return self.cmp_months or default_compare()

    def _earliest_year(self):
        ys = [date.today().year - 1]
        for src, sql in ((self.app.db, "SELECT MIN(substr(date,1,4)) AS y FROM transactions"),
                         (self.app.work, "SELECT MIN(substr(paid_on,1,4)) AS y FROM payments")):
            try:
                r = src.q(sql)
                if r and r[0]["y"]:
                    ys.append(int(r[0]["y"]))
            except Exception:
                pass
        return min(ys)

    def _choose_months(self, *_):
        MonthPickerPopup(self._cmp_list(), self._set_months, self._earliest_year()).open()

    def _set_months(self, months):
        self.cmp_months = None if months == default_compare() else months
        self.refresh()

    def _reset_months(self, *_):
        self._set_months(default_compare())

    def _compare_financial(self, b):
        db, months = self.app.db, self._cmp_list()
        data = [db.month_totals(f"{y}-{m:02d}") for y, m in months]
        txt, g, r = T("text"), T("green"), T("red")
        nets = [d["income"] - d["expenses"] for d in data]
        rows = [("Income", [short(d["income"]) for d in data], [txt] * len(data)),
                ("Expenses", [short(d["expenses"]) for d in data], [txt] * len(data)),
                ("Savings", [short(d["savings"]) for d in data], [txt] * len(data)),
                ("Net", [short(x) for x in nets], [g if x >= 0 else r for x in nets])]
        b.add_widget(compare_card("Month comparison", "blue", months, rows,
                                  self.cmp_months is not None, self._choose_months, self._reset_months,
                                  "Net = income minus expenses."))

    def _compare_work(self, b):
        w, months = self.app.work, self._cmp_list()
        data = [w.month_stats(y, m) for y, m in months]
        txt = T("text")
        rows = [("Completed", [str(len(d["completed"])) for d in data], [txt] * len(data)),
                ("Due", [str(len(d["scheduled"])) for d in data], [txt] * len(data))]
        b.add_widget(compare_card("Month comparison", "purple", months, rows,
                                  self.cmp_months is not None, self._choose_months, self._reset_months,
                                  "Completed = projects finished. "
                                  "Due = projects with a deadline that month."))

    def build(self, b):
        app = self.app
        b.add_widget(title("Insights", "pink"))
        b.add_widget(segmented(["Financial", "Work"], self.mode, self._pick))
        try:
            (self._financial if self.mode == "Financial" else self._work)(b)
        except Exception as e:           # never let an insight bug take the whole tab down
            b.add_widget(Lbl("Couldn't build insights: " + (str(e) or e.__class__.__name__), 13, T("red")))

    def _more(self, b, items):
        rest = items[1:6]
        if rest:
            b.add_widget(heading("More insights"))
            for i in rest:
                b.add_widget(insight_card(i))

    def _financial(self, b):
        app, db = self.app, self.app.db
        d = assistant.financial(db)
        b.add_widget(insight_card(d["insights"][0], hero=True))

        c = Card(stripe=T("pink"))
        c.add_widget(Lbl("Spending breakdown", 16, T("text"), True))
        c.add_widget(Lbl(f"{d['month_name']} so far", 12, T("muted")))
        if d["breakdown"]:
            c.add_widget(donut_with_legend(d["breakdown"], short(d["total"]), "spent", hole=T("card")))
        else:
            c.add_widget(Lbl("No spending recorded this month yet.", 13, T("muted")))
        b.add_widget(c)

        c = Card(stripe=T("blue"))
        c.add_widget(Lbl("Income vs expenses", 16, T("text"), True))
        g, r = T("green"), T("red")
        c.add_widget(bar_chart([m[0].upper() for m in d["months"]],
                               [[(m[1], g), (m[2], r)] for m in d["months"]],
                               legend=[(g, "Income"), (r, "Expenses")],
                               caption=f"Highest: UGX {short(max(max(m[1], m[2]) for m in d['months']))}"))
        b.add_widget(c)

        self._compare_financial(b)

        c = Card(stripe=T("teal"))
        c.add_widget(Lbl("Last 14 days", 16, T("text"), True))
        c.add_widget(bar_chart([x[0] for x in d["daily"]], [[(x[1], T("teal"))] for x in d["daily"]],
                               caption=f"Average UGX {short(d['avg_daily'])} a day"))
        b.add_widget(c)

        self._more(b, d["insights"])
        b.add_widget(tbtn("Budgets & full reports", "blue", on_release=self._to_reports))

    def _to_reports(self, *_):
        self.app.pages["money"].tab = "Budget & Reports"
        self.app.go("money")

    def _work(self, b):
        app = self.app
        d = assistant.work(app.work)
        st = d["stats"]
        b.add_widget(insight_card(d["insights"][0], hero=True))
        b.add_widget(Row(stat_tile("Active", str(st["active"]), "purple"),
                         stat_tile("Due in 7 days", str(st["due7"]), "amber")))
        b.add_widget(Row(stat_tile("Overdue", str(st["overdue"]), "red" if st["overdue"] else "green"),
                         stat_tile("Done this month", str(st["done_month"]), "green")))

        c = Card(stripe=T("amber"))
        c.add_widget(Lbl("Workload, next 14 days", 16, T("text"), True))
        c.add_widget(bar_chart([x[0] for x in d["load"]], [[(x[1], hx(x[2]))] for x in d["load"]],
                               legend=[(hx("#22A559"), "Light"), (hx("#F5A524"), "Moderate"),
                                       (hx("#E5484D"), "Heavy")],
                               caption="Taller bar = more work that day"))
        b.add_widget(c)

        self._compare_work(b)

        self._more(b, d["insights"])


class SettingsPage(Page):
    def build(self, b):
        app = self.app
        b.add_widget(Row(Btn("< Back", bg=T("card2"), size=12, h=36, size_hint_x=0.3,
                             on_release=lambda *_: app.go("home")),
                         Lbl("Settings", 18, T("text"), True, halign="center", size_hint_x=0.7)))
        c = Card()
        c.add_widget(heading("Profile"))
        c.add_widget(kvrow("Name", app.db.get_setting("user_name", "Kevin"), 15, rcolor=T("text"), bold=True))
        c.add_widget(Btn("Change name", bg=T("card2"), on_release=lambda *_: app.rename_form()))
        b.add_widget(c)
        c = Card()
        c.add_widget(heading("Starting balances"))
        c.add_widget(Lbl("Set how much money you have in each account. Use this when you first set up, "
                         "or to correct a balance.", 12, T("muted")))
        for a in app.db.accounts():
            c.add_widget(Row(
                Lbl(a["name"], 15, T("text"), size_hint_x=0.4),
                Lbl(fmt(a["balance"]), 15, T("red") if a["balance"] < 0 else T("text"),
                    True, "right", size_hint_x=0.35),
                Btn("Set", bg=T("card2"), size=12, h=32, size_hint_x=0.25,
                    on_release=lambda _b, a=a: app.balance_form(a)),
            ))
        b.add_widget(c)
        c = Card()
        c.add_widget(heading("Theme"))
        c.add_widget(chip_row(["Light", "Dark"], Theme.mode.title(), lambda v: app.set_theme(mode=v.lower())))
        c.add_widget(heading("Accent colour"))
        c.add_widget(chip_row(list(ACCENTS), Theme.accent, lambda v: app.set_theme(accent=v)))
        b.add_widget(c)
        c = Card()
        c.add_widget(heading("Reminders"))
        c.add_widget(Lbl("Get a notification for project deadlines and bills that are overdue or coming up.",
                         12, T("muted")))
        c.add_widget(chip_row(["On", "Off"], "On" if app.reminders_on() else "Off",
                              lambda v: app.set_reminder("reminders_on", "1" if v == "On" else "0")))
        c.add_widget(heading("Remind me"))
        c.add_widget(chip_row(["Same day", "1 day before", "2 days before", "3 days before"],
                              ["Same day", "1 day before", "2 days before", "3 days before"][app.lead_days()],
                              lambda v: app.set_reminder("reminder_lead", str(["Same day", "1 day before",
                                                                              "2 days before",
                                                                              "3 days before"].index(v)))))
        c.add_widget(Btn("Send a test notification", bg=T("card2"), on_release=lambda *_: app.test_notification()))
        b.add_widget(c)
        c = Card()
        c.add_widget(heading("Automatic backup"))
        c.add_widget(Lbl(app.last_backup_text(), 14, T("text"), True))
        where = app.db.get_setting("backup_where", "")
        if where:
            c.add_widget(Lbl(where, 11, T("muted")))
        err = app.db.get_setting("backup_error", "")
        if err:
            c.add_widget(Lbl("Last backup failed: " + err, 11, T("red")))
        c.add_widget(Lbl("Backed up once a day and after changes; the last 7 days are kept. "
                         "Copies outside the app are what survive a reinstall or a lost phone: "
                         "move them off the phone now and then.", 12, T("muted")))
        c.add_widget(Row(Btn("Back up now", bg=T("card2"), size=13, h=40,
                             on_release=lambda *_: app.backup_now_ui()),
                         Btn("Restore...", bg=T("card2"), size=13, h=40,
                             on_release=lambda *_: RestorePopup(app).open())))
        b.add_widget(c)
        c = Card()
        c.add_widget(heading("Backup & restore"))
        c.add_widget(Lbl("Money and work data are saved together. Export a JSON backup, "
                         "a CSV of transactions, or restore an earlier backup.", 12, T("muted")))
        c.add_widget(Btn("Backup & export", on_release=lambda *_: BackupPopup(app).open()))
        b.add_widget(c)
        c = Card()
        c.add_widget(heading("Reset data"))
        c.add_widget(Lbl("Erase all money and work data and start fresh. Your name, theme and "
                         "reminder settings are kept. A backup is saved first, so you can bring "
                         "everything back with Restore.", 12, T("muted")))
        c.add_widget(Btn("Reset all data...", bg=T("red_bg"), fg=T("red"),
                         on_release=lambda *_: ConfirmPopup(
                             "Erase ALL money and work data? This can't be undone from the app, "
                             "except by restoring the backup made just before.",
                             app.reset_data, lambda: None, yes_text="Erase everything").open()))
        b.add_widget(c)
        c = Card()
        c.add_widget(heading("About"))
        c.add_widget(Lbl("KiraVault - plan, work, grow. Your money and your work in one place. "
                         "Everything stays on this device (UGX).", 12, T("muted")))
        b.add_widget(c)


class GradientBtn(Button):
    """Pill button with a vertical mint-to-green gradient (matches the intro artwork's Get Started button)."""
    TOP, BOTTOM = (0x74, 0xF8, 0xA2, 255), (0x21, 0xD4, 0x9D, 255)

    def __init__(self, text="", **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(56))
        super().__init__(text=text, background_normal="", background_down="",
                         background_color=(0, 0, 0, 0), color=(0.04, 0.10, 0.22, 1),
                         font_size=sp(17), bold=True, **kw)
        tex = Texture.create(size=(1, 2), colorfmt="rgba")
        tex.blit_buffer(bytes(self.BOTTOM) + bytes(self.TOP), colorfmt="rgba", bufferfmt="ubyte")  # row 0 = bottom
        tex.mag_filter = "linear"
        with self.canvas.before:
            self._tint = Color(1, 1, 1, 1)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, texture=tex,
                                          radius=[self.height / 2])
        self.bind(pos=self._upd, size=self._upd, state=self._press)

    def _upd(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size
        self._rect.radius = [self.height / 2]

    def _press(self, *_):
        self._tint.a = 0.7 if self.state == "down" else 1


class IntroScreen(FloatLayout):
    """Full-screen splash shown when the app opens: intro artwork + Login button."""
    def __init__(self, on_login, **kw):
        super().__init__(**kw)
        self._on_login = on_login
        with self.canvas.before:
            Color(0.02, 0.10, 0.36, 1)               # matches the artwork's deep blue
            self._bg = RoundedRectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd)
        self.add_widget(Image(source=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "assets", "KiraVault_intro_Image.png"),
                              fit_mode="cover", size_hint=(1, 1)))
        self.add_widget(GradientBtn("Login", size_hint_x=0.78,
                                    pos_hint={"center_x": 0.5, "y": 0.07},
                                    on_release=self._login))

    def _upd(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size

    # swallow touches so nothing underneath the splash can be tapped before Login
    def on_touch_down(self, touch):
        super().on_touch_down(touch)
        return True

    def on_touch_move(self, touch):
        super().on_touch_move(touch)
        return True

    def on_touch_up(self, touch):
        super().on_touch_up(touch)
        return True

    def _login(self, *_):
        cb, self._on_login = self._on_login, None
        if cb is None:                                # ignore double taps
            return
        anim = Animation(opacity=0, duration=0.25)
        anim.bind(on_complete=lambda *_: cb())
        anim.start(self)


class KiraVaultApp(App):
    title = "KiraVault"
    NAV = [("home", "Home"), ("work", "Work"), ("calendar", "Calendar"), ("money", "Finance"),
           ("insights", "Insights")]       # Settings now lives on Home (top right)
    current = "home"

    def build(self):
        path = os.path.join(self.user_data_dir, "kiravault.db")
        self.db, self.work = DB(path), WorkDB(path)
        Theme.apply(self.db.get_setting("theme", "light"), self.db.get_setting("accent", "Blue"))
        Window.softinput_mode = "below_target"
        if platform not in ("android", "ios"):
            Window.size = (400, 780)
        self.root_fl = FloatLayout()
        self.undo, self._undo_bar, self._undo_ev = Undo(self.db), None, None
        self._bk_changes = self.db.con.total_changes + self.work.c.total_changes
        self._ui()
        self._intro = IntroScreen(on_login=self._close_intro)
        self.root_fl.add_widget(self._intro)          # sits on top of the app until Login is tapped
        return self.root_fl

    def _close_intro(self):
        if self._intro.parent:
            self._intro.parent.remove_widget(self._intro)

    # ---- backups ----
    def last_backup_text(self):
        return autobackup.describe(self.db.get_setting("last_backup", ""), datetime.now())

    def maybe_backup(self, force=False):
        try:
            changes = self.db.con.total_changes + self.work.c.total_changes
            if not (force or autobackup.due(self.db.get_setting("last_backup", ""), datetime.now(),
                                            changes != self._bk_changes)):
                return None
            res = autobackup.backup_now(self.db, self.user_data_dir)
            self.db.set_setting("backup_error", "")
            self._bk_changes = self.db.con.total_changes + self.work.c.total_changes
            return res
        except Exception as e:
            self.db.set_setting("backup_error", (str(e) or e.__class__.__name__)[:100])
            return None

    def backup_now_ui(self):
        res = self.maybe_backup(force=True)
        self.refresh()
        if res is None:
            InfoPopup("The backup failed. See the message under Automatic backup.").open()
        elif res[0]:
            InfoPopup("Backed up. Extra copy: " + res[1]).open()
        else:
            InfoPopup("Backed up inside the app. A copy outside the app wasn't possible: " + res[1]).open()

    def restore_backup(self, path):
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict) or "accounts" not in data:
                raise ValueError("that file isn't a KiraVault backup")
            autobackup.make(self.user_data_dir, self.db.export_json(), kind="before")
            self.db.import_json(data)
            self.undo.clear()
            Theme.apply(self.db.get_setting("theme", "light"), self.db.get_setting("accent", "Blue"))
            self._ui()
            InfoPopup("Backup restored.").open()
        except Exception as e:
            InfoPopup(f"Couldn't restore: {e}").open()

    def reset_data(self):
        """Wipe money + work data. Saves a restorable backup first; keeps profile/theme/reminder prefs."""
        try:
            autobackup.make(self.user_data_dir, self.db.export_json(), kind="before")
            keep = {k: self.db.get_setting(k) for k in ("user_name", "theme", "accent",
                                                        "reminders_on", "reminder_lead")
                    if self.db.get_setting(k) != ""}
            con = self.db.con
            for t in ("payments", "tasks", "projects", "clients", "work_types", "transactions",
                      "commitments", "expected_income", "goals", "budgets", "settings", "accounts"):
                con.execute(f"DELETE FROM {t}")
            con.executemany("INSERT INTO accounts(name, balance) VALUES(?, 0)", [(n,) for n in ACCOUNTS])
            con.executemany("INSERT INTO work_types(name) VALUES(?)", [(n,) for n in DEFAULT_TYPES])
            con.commit()
            for k, v in keep.items():
                self.db.set_setting(k, v)
            self.undo.clear()
            self.current = "home"
            Theme.apply(self.db.get_setting("theme", "light"), self.db.get_setting("accent", "Blue"))
            self._ui()
            InfoPopup("All data erased. Restore the backup from Settings if you change your mind.").open()
        except Exception as e:
            self.db.con.rollback()
            InfoPopup(f"Couldn't reset: {e}").open()

    # ---- undo ----
    def delete_with_undo(self, label, fn):
        self.undo.run(fn)
        self._show_undo(label)

    def _show_undo(self, label):
        self._hide_undo(clear=False)
        bar = BoxLayout(size_hint=(0.92, None), height=dp(50), pos_hint={"center_x": 0.5, "y": 0.14},
                        padding=[dp(14), dp(7), dp(8), dp(7)], spacing=dp(8))
        with bar.canvas.before:
            Color(*T("text"))
            rect = RoundedRectangle(pos=bar.pos, size=bar.size, radius=[dp(12)])
        bar.bind(pos=lambda *_: setattr(rect, "pos", bar.pos), size=lambda *_: setattr(rect, "size", bar.size))
        lab = Label(text=label, color=T("bg"), font_size=sp(13), halign="left", valign="middle", shorten=True)
        lab.bind(size=lambda *_: setattr(lab, "text_size", lab.size))
        bar.add_widget(lab)
        bar.add_widget(Btn("Undo", size=13, h=36, size_hint_x=None, width=dp(80), pos_hint={"center_y": 0.5},
                           on_release=lambda *_: self.undo_delete()))
        self.root_fl.add_widget(bar)
        self._undo_bar = bar
        self._undo_ev = Clock.schedule_once(lambda *_: self._hide_undo(), 6)

    def _hide_undo(self, clear=True):
        if self._undo_ev:
            self._undo_ev.cancel()
            self._undo_ev = None
        if self._undo_bar is not None and self._undo_bar.parent:
            self._undo_bar.parent.remove_widget(self._undo_bar)
        self._undo_bar = None
        if clear:
            self.undo.clear()

    def undo_delete(self):
        ok = self.undo.undo()
        self._hide_undo()
        if ok:
            self.refresh()
        else:
            InfoPopup("Can't undo, other changes were made after the delete.").open()

    # ---- reminders ----
    def reminders_on(self):
        return self.db.get_setting("reminders_on", "1") == "1"

    def lead_days(self):
        try:
            return min(max(int(self.db.get_setting("reminder_lead", "1")), 0), 3)
        except ValueError:
            return 1

    def set_reminder(self, key, value):
        self.db.set_setting(key, value)
        self.refresh()
        self.check_reminders()

    def check_reminders(self, *_):
        if not self.reminders_on():
            return
        items = reminders.collect(self.work, self.db, lead=self.lead_days())
        new, sent = reminders.fresh(self.db, items)
        if new and reminders.notify(*reminders.summary(new)):
            reminders.mark_sent(self.db, sent, new)

    def test_notification(self):
        ok = reminders.notify("KiraVault", "Reminders are working.")
        InfoPopup("Notification sent. If you don't see it, allow notifications for KiraVault "
                  "in your phone settings." if ok else
                  "Notifications aren't available here. On your phone, install the app with the "
                  "'plyer' requirement and allow notifications.").open()

    def on_start(self):
        if platform == "android":
            try:
                from android.permissions import request_permissions
                request_permissions(["android.permission.POST_NOTIFICATIONS",
                                     "android.permission.WRITE_EXTERNAL_STORAGE"])
            except Exception:
                pass
        Clock.schedule_once(self.check_reminders, 4)
        Clock.schedule_interval(self.check_reminders, 1800)
        Clock.schedule_once(lambda *_: self.maybe_backup(), 8)
        Clock.schedule_interval(lambda *_: self.maybe_backup(), 1800)

    def on_pause(self):
        self.maybe_backup()          # last chance: Android may close the app while it's paused
        return True

    def on_resume(self):
        self.refresh()
        Clock.schedule_once(self.check_reminders, 1)
        Clock.schedule_once(lambda *_: self.maybe_backup(), 3)

    def on_stop(self):
        self.maybe_backup()

    def _ui(self):
        Window.clearcolor = T("bg")
        root = self.root_fl
        root.clear_widgets()
        main = BoxLayout(orientation="vertical")
        self.sm, self.pages = ScreenManager(transition=NoTransition()), {}
        for name, cls in (("home", HomePage), ("work", WorkPage), ("project", ProjectPage), ("client", ClientPage),
                          ("calendar", CalendarPage), ("money", MoneyHub), ("insights", AssistantPage),
                          ("settings", SettingsPage)):
            self.pages[name] = cls(self, name=name)
            self.sm.add_widget(self.pages[name])
        main.add_widget(self.sm)
        nav = BoxLayout(size_hint_y=None, height=dp(60), spacing=dp(2), padding=[dp(4), dp(4), dp(4), dp(6)])
        self.navbtns = {}
        for name, label in self.NAV:
            self.navbtns[name] = Btn(label, bg=T("card"), size=12, h=48, on_release=lambda _b, n=name: self.go(n))
            nav.add_widget(self.navbtns[name])
        main.add_widget(nav)
        root.add_widget(main)
        add = Btn("+", size=26, h=48, size_hint_x=None, width=dp(48))
        add.pos_hint = {"right": 0.95, "y": 0.1}
        add.bind(on_release=lambda *_: AddMenuPopup(self).open())
        root.add_widget(add)
        self.add_btn = add          # shown on Home only; other screens have their own add buttons
        self.go(self.current)

    def go(self, name):
        self.current = name
        self.sm.current = name
        tab = {"project": "work", "client": "work"}.get(name, name)
        for n, btn in self.navbtns.items():
            on = n == tab
            btn.color = T(SECTION[n])
            btn.set_bg(tint(SECTION[n], 0.28) if on else T("card"))
        self.add_btn.opacity, self.add_btn.disabled = (1, False) if name == "home" else (0, True)
        self.pages[name].refresh()

    def refresh(self):
        self.pages[self.current].refresh()

    def open_project(self, pid, back="work"):
        self.pages["project"].pid, self.pages["project"].back = pid, back
        self.go("project")

    def open_client(self, cid):
        self.pages["client"].cid = cid
        self.go("client")

    def set_theme(self, mode=None, accent=None):
        Theme.apply(mode or Theme.mode, accent or Theme.accent)
        self.db.set_setting("theme", Theme.mode)
        self.db.set_setting("accent", Theme.accent)
        self._ui()

    def project_form(self, pid=None, start=None, deadline=None, est=None):
        w, p = self.work, (self.work.project(pid) if pid else None)

        def submit(v):
            if v["source"] == "Job":
                amt = 0.0          # day-job work has no quoted amount
            else:
                amt = parse_amount(v["amount"], allow_zero=True)
            if not v["title"] or amt is None:
                return "Enter a project name and a valid amount (0 if unknown)"
            est_days = parse_days(v["est"])
            if v["deadline"] < v["start"]:
                return "The deadline is before the start date"
            w.save_project({"title": v["title"], "client": v["client"], "work_type": v["type"],
                            "source": v["source"], "status": p["status"] if p else "Pending",
                            "start_date": v["start"], "deadline": v["deadline"], "amount": amt,
                            "progress": p["progress"] if p else 0, "priority": v["priority"],
                            "est_days": est_days,
                            "notes": (p["notes"] if p else "") or ""}, pid)

        pop = self._popup("Edit project" if p else "New project", [
            ("title", "Project", "text", p["title"] if p else "", None),
            ("client", "Client", "text", p["client"] if p else "", None),
            ("type", "Work type", "choice", (p and p["work_type"]) or "Video Editing", w.work_types()),
            ("source", "Source", "choice", p["source"] if p else "Freelance", SOURCES),
            ("priority", "Priority", "choice", p["priority"] if p else "Medium", PRIORITIES),
            ("start", "Start date", "date", (p and p["start_date"]) or start or "today", None),
            ("deadline", "Deadline", "date", (p and p["deadline"]) or deadline or start or "today", None),
            ("est", "Estimated effort (days or hours, optional)", "text",
             f"{p['est_days']:g}" if p and p["est_days"] else (f"{est:g}" if est else ""), None),
            ("amount", "Quoted amount (UGX)", "text", f"{p['amount']:.0f}" if p else "", None),
        ], submit)
        src = pop.widgets["source"]
        src.bind(text=lambda *_: pop.set_visible("amount", src.text != "Job"))
        pop.set_visible("amount", src.text != "Job")

    def job_check_form(self):
        def submit(v):
            est = parse_days(v["est"])
            if not est:
                return "Enter the effort needed, e.g. 5 (days) or 12h"
            a, b = date.fromisoformat(v["start"]), date.fromisoformat(v["deadline"])
            if b < a:
                return "The deadline is before the start date"
            VerdictPopup(self, self.work.capacity_check(est, a, b), est, v["start"], v["deadline"]).open()
        self._popup("Can I take another job?", [
            ("est", "Effort needed (days or hours)", "text", "", None),
            ("start", "Start", "date", "today", None),
            ("deadline", "Client's deadline", "date",
             (date.today() + timedelta(days=7)).isoformat(), None),
        ], submit, "Check")

    def task_form(self, pid):
        def submit(v):
            if not v["title"]:
                return "Enter a task name"
            self.work.add_task(pid, v["title"])
        self._popup("New task", [("title", "Task", "text", "", None)], submit, "Add")

    def work_payment_form(self, pid):
        p = self.work.project(pid)

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            tid = self.db.add_tx("income", amt, self.db.account_id(v["account"]), description=p["title"],
                                 source={"Job": "Salary", "Freelance": "Freelance"}.get(p["source"], "Other"),
                                 date_=v["date"])
            self.work.add_payment(pid, amt, "Payment", paid_on=v["date"], tx_id=tid)
        self._popup(f"Payment for {p['title']}", [
            ("amount", "Amount received (UGX)", "text", f"{wbal(p):.0f}", None),
            ("date", "Date received", "date", "today", None),
            ("account", "Received via", "choice", "Mobile Money", self.acct_names()),
        ], submit, "Record payment")

    def payment_edit_form(self, payid):
        r = self.work.payment(payid)
        if not r:
            return

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            tid = r["tx_id"]
            old = self.db.q("SELECT * FROM transactions WHERE id=?", (tid,)) if tid else []
            if old:   # keep the Money entry in step with the payment
                old = old[0]
                self.db.delete_tx(tid)
                tid = self.db.add_tx("income", amt, old["account_id"], description=old["description"],
                                     source=old["source"], date_=v["date"])
            self.work.update_payment(payid, amt, v["date"], tid)
        self._popup("Edit payment", [
            ("amount", "Amount received (UGX)", "text", f"{r['amount']:.0f}", None),
            ("date", "Date received", "date", r["paid_on"], None),
        ], submit)

    def payment_delete(self, payid):
        r = self.work.payment(payid)
        if r and r["tx_id"]:
            self.db.delete_tx(r["tx_id"])
        self.work.delete_payment(payid)

    def acct_names(self):
        return [a["name"] for a in self.db.accounts()]

    def messages(self, s):
        out = []
        if s["shortfall"] > 0:
            out.append((f"Warning: your commitments exceed what you have "
                        f"by UGX {fmt(s['shortfall'])}.", T("red")))
        else:
            out.append((
                f"You have UGX {short(s['available'])} available and "
                f"UGX {short(s['committed'])} committed. "
                f"UGX {short(s['safe'])} is safe to spend.", T("green")))
        if s["overdue_n"]:
            out.append((f"{s['overdue_n']} commitment(s) overdue, "
                        f"UGX {short(s['overdue_sum'])} in total.", T("red")))
        if s["expected"]:
            out.append((f"UGX {short(s['expected'])} is expected but only "
                        f"counts once it's received.", T("amber")))
        goals = [g for g in self.db.goals() if g["target"] and g["saved"] < g["target"]]
        if goals:
            g = max(goals, key=lambda g: g["saved"] / g["target"])
            out.append((f"You're {g['saved'] / g['target'] * 100:.0f}% "
                        f"toward {g['name']}.", T("text")))
        ym = date.today().strftime("%Y-%m")
        spent = dict(self.db.spending_by_category(ym))
        for bd in self.db.budgets():
            frac = spent.get(bd["category"], 0) / bd["limit_amount"]
            if frac >= 0.8:
                out.append((f"{bd['category']} budget is at {frac * 100:.0f}%.",
                            T("amber") if frac < 1 else T("red")))
        return out

    # ---- forms ----
    def _popup(self, title, fields, submit, text="Save"):
        pop = FormPopup(title, fields, submit, self.refresh, text)
        pop.open()
        return pop

    def income_form(self):
        db = self.db

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount, e.g. 450000 or 450k"
            d = v["date"]
            aid = db.account_id(v["account"])
            note = v["project"] or v["client"]
            if v["status"] == "Expected":
                db.add_expected(v["client"], v["project"], amt, d, v["source"], aid)
            else:
                db.add_tx("income", amt, aid, source=v["source"],
                          description=note, date_=d)

        self._popup("Add income", [
            ("amount", "Amount (UGX)", "text", "", None),
            ("source", "Source", "choice", "Salary", INCOME_SOURCES),
            ("account", "Received via", "choice", "Bank", self.acct_names()),
            ("status", "Status", "choice", "Received", ["Received", "Expected"]),
            ("date", "Date", "date", "today", None),
            ("client", "Client (optional)", "text", "", None),
            ("project", "Project / note (optional)", "text", "", None),
        ], submit, "Add income")

    def expense_form(self):
        db = self.db

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount, e.g. 35000 or 35k"
            if v["category"] == "Others" and not v["desc"]:
                return "Describe this expense (e.g. Phone repair)"
            db.add_tx("expense", amt, db.account_id(v["account"]),
                      v["category"], v["desc"], date_=v["date"])

        self._popup("Add expense", [
            ("amount", "Amount (UGX)", "text", "", None),
            ("category", "Category", "choice", "Food", EXPENSE_CATEGORIES),
            ("account", "Paid using", "choice", "Cash", self.acct_names()),
            ("date", "Date", "date", "today", None),
            ("desc", "Description", "text", "", None),
        ], submit, "Add expense")

    def commitment_form(self, cid=None, due=None):
        db = self.db
        c = next((x for x in db.commitments() if x["id"] == cid), None) if cid else None
        goals = db.goals()
        goal_names = {g["name"]: g["id"] for g in goals}
        goal_now = next((g["name"] for g in goals if c and g["id"] == c["goal_id"]), "(none)")

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            args = (v["title"], v["kind"], amt, v["due"], db.account_id(v["account"]),
                    v["recurring"], goal_names.get(v["goal"]))
            if c:
                db.update_commitment(c["id"], *args)
            else:
                db.add_commitment(*args)

        self._popup("Edit commitment" if c else "New commitment", [
            ("title", "Name (e.g. Internet)", "text", c["title"] if c else "", None),
            ("kind", "Type", "choice", c["kind"] if c else "Rent", KINDS),
            ("amount", "Amount (UGX)", "text", f"{c['amount']:.0f}" if c else "", None),
            ("due", "Due date", "date", (c and c["due_date"]) or due or "today", None),
            ("account", "Pay from", "choice", (c and c["account"]) or "Mobile Money", self.acct_names()),
            ("recurring", "Recurring", "choice", c["recurring"] if c else "None",
             ["None", "Monthly", "Weekly"]),
            ("goal", "Savings goal (if Savings)", "choice", goal_now,
             ["(none)"] + list(goal_names)),
        ], submit)

    def bill_form(self, cid=None):
        """Bills & Debts screen only: add/edit a bill or debt (no savings option)."""
        db = self.db
        c = next((x for x in db.commitments() if x["id"] == cid), None) if cid else None

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            args = (v["title"], v["kind"], amt, v["due"], db.account_id(v["account"]),
                    v["recurring"], None)
            if c:
                db.update_commitment(c["id"], *args)
            else:
                db.add_commitment(*args)

        self._popup("Edit bill or debt" if c else "Add bill or debt", [
            ("title", "Name (e.g. Internet)", "text", c["title"] if c else "", None),
            ("kind", "Type", "choice", c["kind"] if c else "Rent",
             [k for k in KINDS if k != "Savings"]),
            ("amount", "Amount (UGX)", "text", f"{c['amount']:.0f}" if c else "", None),
            ("due", "Due date", "date", (c and c["due_date"]) or "today", None),
            ("account", "Pay from", "choice", (c and c["account"]) or "Mobile Money", self.acct_names()),
            ("recurring", "Recurring", "choice", c["recurring"] if c else "None",
             ["None", "Monthly", "Weekly"]),
        ], submit, "Save")

    def savings_commit_form(self, cid):
        """Savings tab only: edit a savings commitment."""
        db = self.db
        c = next((x for x in db.commitments() if x["id"] == cid), None)
        if not c:
            return
        goals = db.goals()
        goal_names = {g["name"]: g["id"] for g in goals}
        goal_now = next((g["name"] for g in goals if g["id"] == c["goal_id"]), "(none)")

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            db.update_commitment(c["id"], v["title"], "Savings", amt, v["due"],
                                 db.account_id(v["account"]), v["recurring"],
                                 goal_names.get(v["goal"]))

        self._popup("Edit savings commitment", [
            ("title", "Name", "text", c["title"], None),
            ("amount", "Amount (UGX)", "text", f"{c['amount']:.0f}", None),
            ("due", "Due date", "date", c["due_date"] or "today", None),
            ("account", "Save from", "choice", c["account"] or "Mobile Money", self.acct_names()),
            ("recurring", "Recurring", "choice", c["recurring"], ["None", "Monthly", "Weekly"]),
            ("goal", "Savings goal", "choice", goal_now, ["(none)"] + list(goal_names)),
        ], submit)

    def expected_form(self):
        db = self.db

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            db.add_expected(v["client"], v["project"], amt, v["date"],
                            v["source"], db.account_id(v["account"]))

        self._popup("Expected income", [
            ("client", "Client", "text", "", None),
            ("project", "Project", "text", "", None),
            ("amount", "Amount (UGX)", "text", "", None),
            ("source", "Source", "choice", "Freelance", INCOME_SOURCES),
            ("date", "Expected date", "date", "today", None),
            ("account", "Expected via", "choice", "Mobile Money", self.acct_names()),
        ], submit)

    def receive_form(self, e):
        db = self.db

        def submit(v):
            db.receive_expected(e["id"], db.account_id(v["account"]), v["date"])

        self._popup(f"Received UGX {fmt(e['amount'])}", [
            ("account", "Received via", "choice", "Mobile Money", self.acct_names()),
            ("date", "Date", "date", "today", None),
        ], submit, "Mark received")

    def goal_form(self):
        db = self.db

        def submit(v):
            target = parse_amount(v["target"])
            if not v["name"] or not target:
                return "Enter a goal name and target amount"
            saved = parse_amount(v["saved"] or "0", True)
            monthly = parse_amount(v["monthly"] or "0", True)
            if saved is None or monthly is None:
                return "Check the amounts"
            db.add_goal(v["name"], target, saved, monthly)

        self._popup("New savings goal", [
            ("name", "Goal (e.g. New Camera)", "text", "", None),
            ("target", "Target (UGX)", "text", "", None),
            ("saved", "Already saved (optional)", "text", "", None),
            ("monthly", "Save per month (optional)", "text", "", None),
        ], submit, "Create goal")

    def contribute_form(self, g):
        db = self.db

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            db.contribute(g["id"], db.account_id(v["account"]), amt)

        self._popup(f"Save towards {g['name']}", [
            ("amount", "Amount (UGX)", "text", "", None),
            ("account", "Take from", "choice", "Bank", self.acct_names()),
        ], submit, "Save")

    def pay_form(self, c):
        db = self.db
        rem = c["amount"] - c["paid_amount"]

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            db.pay_commitment(c["id"], db.account_id(v["account"]), amt)

        is_sav = c["kind"] == "Savings"
        self._popup(f"Save towards {c['title']}" if is_sav else f"Pay {c['title']}", [
            ("amount", "Amount (UGX)", "text", f"{rem:.0f}", None),
            ("account", "Pay from", "choice", c["account"] or "Cash",
             self.acct_names()),
        ], submit, "Save" if is_sav else "Pay")

    def transfer_form(self):
        db = self.db

        def submit(v):
            amt = parse_amount(v["amount"])
            if not amt:
                return "Enter a valid amount"
            db.transfer(db.account_id(v["from"]), db.account_id(v["to"]), amt)

        self._popup("Transfer money", [
            ("amount", "Amount (UGX)", "text", "", None),
            ("from", "From", "choice", "Bank", self.acct_names()),
            ("to", "To", "choice", "Mobile Money", self.acct_names()),
        ], submit, "Transfer")

    def balance_form(self, a):
        db = self.db

        def submit(v):
            bal = parse_amount(v["balance"], allow_zero=True)
            if bal is None:
                return "Enter a valid amount"
            db.set_balance(a["id"], bal)

        self._popup(f"{a['name']} starting balance", [
            ("balance", "Amount (UGX)", "text", f"{a['balance']:.0f}", None),
        ], submit, "Save")

    def budget_form(self):
        db = self.db

        def submit(v):
            lim = parse_amount(v["limit"], allow_zero=True)
            if lim is None:
                return "Enter a valid amount (0 removes the budget)"
            db.set_budget(v["category"], lim)

        self._popup("Set budget", [
            ("category", "Category", "choice", "Food", EXPENSE_CATEGORIES),
            ("limit", "Monthly limit (UGX, 0 = remove)", "text", "", None),
        ], submit)

    def rename_form(self):
        def submit(v):
            if not v["name"]:
                return "Enter a name"
            self.db.set_setting("user_name", v["name"])

        self._popup("Your name", [
            ("name", "Name", "text", self.db.get_setting("user_name", "Kevin"), None),
        ], submit)


if __name__ == "__main__":
    KiraVaultApp().run()
