"""KiraVault themes: KiraFlow's blue/green/red/amber palette, light + dark, switchable accent."""


def _h(x):
    return tuple(int(x[i:i + 2], 16) / 255 for i in (1, 3, 5)) + (1,)


def mix(a, b, t):
    return tuple(a[i] * t + b[i] * (1 - t) for i in range(4))


_L = dict(bg="#F3F5FA", card="#FFFFFF", card2="#E8ECFF", text="#1B2236", muted="#7A8299",
          green="#22A559", green_bg="#DDF5E6", red="#E5484D", red_bg="#FFDADA",
          amber="#F5A524", amber_bg="#FFF1D0", accent="#3D5AFE", track="#E6E9F2", line="#E1E5EF",
          blue="#2F7BF5", blue_bg="#E1EDFF", teal="#0FB5A0", teal_bg="#D6F5F0",
          purple="#8453F0", purple_bg="#ECE4FF", pink="#E8458F", pink_bg="#FFE1EE")
_D = dict(bg="#0D1221", card="#161D33", card2="#222B47", text="#F4F6FF", muted="#94A3C1",
          green="#3FD08F", green_bg="#14403A", red="#F76B6B", red_bg="#3A1E2A",
          amber="#FDB844", amber_bg="#3A2D12", accent="#6673F5", track="#2D3652", line="#323B58",
          blue="#6AA8FF", blue_bg="#1A2C52", teal="#34D9C3", teal_bg="#10403F",
          purple="#A78BFA", purple_bg="#2D2552", pink="#F472B6", pink_bg="#472140")
LIGHT = {k: _h(v) for k, v in _L.items()}
DARK = {k: _h(v) for k, v in _D.items()}
ACCENTS = {"Blue": ("#3D5AFE", "#6673F5"), "Green": ("#22A559", "#3FD08F"),
           "Purple": ("#7C4DFF", "#A07BFF"), "Orange": ("#F57C00", "#FFA040")}


# Category colours (theme keys), so the same category looks the same everywhere
CATEGORY_COLORS = {"Food": "amber", "Transport": "blue", "Rent": "purple", "Internet": "teal",
                   "Bills": "red", "Shopping": "pink", "Entertainment": "purple", "Work": "blue",
                   "Salary": "green", "Freelance": "teal", "Business": "blue", "Gift": "pink"}


def category_color(name, bg=False):
    key = CATEGORY_COLORS.get(name, "muted")
    return Theme.c[key + "_bg"] if bg and key != "muted" else Theme.c.get(key + ("_bg" if bg else ""), Theme.c["muted"])


class Theme:
    c, mode, accent = dict(LIGHT), "light", "Blue"

    @classmethod
    def apply(cls, mode="light", accent="Blue"):
        mode = mode if mode in ("light", "dark") else "light"
        accent = accent if accent in ACCENTS else "Blue"
        c = dict(DARK if mode == "dark" else LIGHT)
        c["accent"] = _h(ACCENTS[accent][1 if mode == "dark" else 0])
        c["accent_bg"] = mix(c["accent"], c["card"], 0.16)
        cls.c, cls.mode, cls.accent = c, mode, accent
