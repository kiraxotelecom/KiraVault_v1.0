"""Small canvas-drawn charts for KiraVault (donut, bars, legend). Drawing only, no data logic.
Colours come from Theme so charts follow light/dark mode."""
from kivy.graphics import Color, Ellipse, RoundedRectangle, Line
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex as hx

from theme import Theme

# Fixed palette (independent of the accent colour so slices never repeat the same hue)
PALETTE = [hx(c) for c in ("#4C8DF6", "#2DD4A7", "#F5A524", "#E5666B", "#9B6BFF", "#6B7A99")]


def slice_color(i, name=""):
    return PALETTE[-1] if name == "Other" else PALETTE[i % (len(PALETTE) - 1)]


def _T(key):
    return Theme.c[key]


class Dot(Widget):
    def __init__(self, rgba, d=10, **kw):
        super().__init__(size_hint=(None, None), size=(dp(d), dp(d)), **kw)
        with self.canvas:
            Color(*rgba)
            self._e = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_: setattr(self._e, "pos", self.pos))


class Badge(FloatLayout):
    """Round icon with a single glyph ('!', '+', 'i'): no emoji, so it renders on every phone."""
    def __init__(self, glyph, rgba, **kw):
        super().__init__(size_hint=(None, None), size=(dp(40), dp(40)), **kw)
        with self.canvas.before:
            Color(*rgba)
            self._e = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_: setattr(self._e, "pos", self.pos))
        self.add_widget(Label(text=glyph, bold=True, font_size=sp(20), color=(1, 1, 1, 1),
                              pos_hint={"center_x": 0.5, "center_y": 0.5}, size_hint=(None, None),
                              size=(dp(40), dp(40))))


class Donut(FloatLayout):
    """slices: [(rgba, value)]. Two centre captions. `hole` should match the card behind it."""
    GAP = 1.5

    def __init__(self, slices, top="", bottom="", hole=None, diameter=150, **kw):
        d = dp(diameter)
        super().__init__(size_hint=(None, None), size=(d, d), **kw)
        self.slices = [(c, v) for c, v in slices if v > 0]
        self.hole = hole if hole is not None else _T("card")
        self.add_widget(Label(text=top, bold=True, font_size=sp(16), color=_T("text"),
                              size_hint=(None, None), size=(d * 0.6, dp(22)),
                              pos_hint={"center_x": 0.5, "center_y": 0.57}))
        self.add_widget(Label(text=bottom, font_size=sp(11), color=_T("muted"),
                              size_hint=(None, None), size=(d * 0.6, dp(16)),
                              pos_hint={"center_x": 0.5, "center_y": 0.41}))
        self.bind(pos=self.draw, size=self.draw)

    def draw(self, *_):
        self.canvas.before.clear()
        d = min(self.width, self.height)
        x, y = self.center_x - d / 2, self.center_y - d / 2
        total = sum(v for _, v in self.slices)
        with self.canvas.before:
            if total <= 0:
                Color(*_T("track"))
                Ellipse(pos=(x, y), size=(d, d))
            else:
                gap = self.GAP if len(self.slices) > 1 else 0
                a = 0.0
                for col, v in self.slices:
                    sweep = 360.0 * v / total
                    Color(*col)
                    Ellipse(pos=(x, y), size=(d, d), angle_start=a, angle_end=a + max(sweep - gap, 0.6))
                    a += sweep
            h = d * 0.62
            Color(*self.hole)
            Ellipse(pos=(self.center_x - h / 2, self.center_y - h / 2), size=(h, h))


class LegendRow(BoxLayout):
    def __init__(self, rgba, name, value, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(26), spacing=dp(8), **kw)
        dot = Dot(rgba)
        dot.pos_hint = {"center_y": 0.5}
        self.add_widget(dot)
        n = Label(text=name, font_size=sp(13), color=_T("text"), halign="left", valign="middle",
                  shorten=True, shorten_from="right")
        n.bind(size=lambda w, s: setattr(w, "text_size", s))
        v = Label(text=value, font_size=sp(13), bold=True, color=_T("text"), halign="right",
                  valign="middle", size_hint_x=None, width=dp(44))
        v.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.add_widget(n)
        self.add_widget(v)


def donut_with_legend(items, top, bottom, hole=None):
    """items: [{'name','pct'}]. Donut on the left, legend on the right (like the mock-up)."""
    cols = [slice_color(i, it["name"]) for i, it in enumerate(items)]
    donut = Donut([(c, it["pct"]) for c, it in zip(cols, items)], top, bottom, hole)
    donut.pos_hint = {"center_y": 0.5}
    legend = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
    legend.bind(minimum_height=legend.setter("height"))
    legend.pos_hint = {"center_y": 0.5}
    for c, it in zip(cols, items):
        legend.add_widget(LegendRow(c, it["name"], f"{it['pct']:.0f}%"))
    row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(160), spacing=dp(14))
    row.add_widget(donut)
    row.add_widget(legend)
    return row


class _Bars(Widget):
    """groups: [[(value, rgba), ...], ...]: one cluster of 1-2 bars per x position."""
    def __init__(self, groups, height=120, **kw):
        super().__init__(size_hint_y=None, height=dp(height), **kw)
        self.groups = groups
        self.bind(pos=self.draw, size=self.draw)

    def draw(self, *_):
        self.canvas.clear()
        mx = max([v for g in self.groups for v, _ in g] or [0])
        n = max(len(self.groups), 1)
        slot = self.width / n
        top = self.height - dp(4)
        with self.canvas:
            Color(*_T("line"))
            Line(points=[self.x, self.y, self.right, self.y], width=1)
            for i, g in enumerate(self.groups):
                k = len(g)
                gap = dp(3)
                bw = min((slot * 0.72 - gap * (k - 1)) / k, dp(26))
                x0 = self.x + i * slot + (slot - (bw * k + gap * (k - 1))) / 2
                for j, (v, col) in enumerate(g):
                    h = 0 if mx <= 0 or v <= 0 else max(top * v / mx, dp(3))
                    if h <= 0:
                        continue
                    Color(*col)
                    RoundedRectangle(pos=(x0 + j * (bw + gap), self.y + 1), size=(bw, h),
                                     radius=[min(dp(3), h / 2)])


def _caption(text, size=11, color=None, halign="left"):
    lb = Label(text=text, font_size=sp(size), color=color if color is not None else _T("muted"),
               halign=halign, valign="middle", size_hint_y=None, height=dp(16))
    lb.bind(size=lambda w, s: setattr(w, "text_size", s))
    return lb


def bar_chart(labels, groups, legend=None, caption=None, height=120):
    """labels: x captions; groups: [[(value, rgba)...]...]; legend: [(rgba, text)] or None."""
    box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
    box.bind(minimum_height=box.setter("height"))
    if caption:
        box.add_widget(_caption(caption))
    box.add_widget(_Bars(groups, height))
    row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(16))
    for lab in labels:
        row.add_widget(_caption(lab, 10, halign="center"))
    box.add_widget(row)
    if legend:
        lg = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(20), spacing=dp(6))
        for col, text in legend:
            dot = Dot(col, 8)
            dot.pos_hint = {"center_y": 0.5}
            lg.add_widget(dot)
            t = _caption(text, 11)
            lg.add_widget(t)
        box.add_widget(lg)
    return box
