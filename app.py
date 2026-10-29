"""Lumi: talk to your monitor (via Wispr Flow).

Press Ctrl+Alt+M anywhere -> the on-screen menu pops up with the cursor in the box ->
dictate with Wispr Flow ("go to color", "down", "more", "movie mode", "sunset vibes", "party") ->
the command runs automatically.

The whole window is drawn on one canvas so the frosted-glass cards can blend
with the soft background (Tk widgets can't be see-through).
"""
import colorsys
import ctypes
import math
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

import brain
import monitor
import stats
import voice

APP_NAME = "Lumi"
HOTKEY_ID = 1
MOD_ALT, MOD_CONTROL, VK_M = 0x0001, 0x0002, 0x4D
AUTO_RUN_DELAY_MS = 1200  # wait this long after dictation stops before running
PARTY_SECONDS = 16

# Layout is designed at 1120x800 and scaled to the screen (see set_scale)
S, W, H, MARGIN, GAP = 1.0, 1120, 800, 28, 16
BG_STEP = 4             # background is rendered every 4 pixels and enlarged (it's soft anyway)

# Light theme
BASE = (246, 244, 251)  # near-white lavender
# Soft colour blobs behind the glass: (rgb, centre x, centre y, radius, strength) as fractions of the window
DEFAULT_BLOBS = [((203, 189, 255), .08, .02, .60, .75),
                 ((255, 211, 196), .98, .25, .50, .60),
                 ((181, 233, 221), .55, 1.10, .62, .60)]
WHITE, INK = (255, 255, 255), (60, 50, 110)
FG, MUTED, ACCENT, WARN = "#2a2640", "#726d8a", "#7b61ff", "#f0a93c"
SLIDER_COLORS = {"red": "#f09aaa", "green": "#78cba3", "blue": "#8eaef0"}

ICONS = {"Modes": "", "Picture": "", "Color": "", "Audio": "",
         "Input": "", "Display": "", "mic": ""}
FALLBACK_ICONS = {"Modes": "▶", "Picture": "☀", "Color": "◐", "Audio": "♪", "Input": "⇄", "Display": "▭", "mic": "●"}
PRETTY = {"eco": "ECO", "hdmi": "HDMI", "vga": "VGA", "extend": "Extend", "duplicate": "Mirror",
          "laptop_only": "Laptop", "monitor_only": "Monitor", "off": "Off", "on": "On"}


def set_scale(scale):
    global S, W, H, MARGIN, GAP
    S, W, H, MARGIN, GAP = scale, round(1120 * scale), round(800 * scale), 28 * scale, 16 * scale


def px(value):
    return value * S


def pretty(value):
    return PRETTY.get(str(value), str(value).replace("_", " ").capitalize())


# ---- colour helpers ----------------------------------------------------------

def hexcolor(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def mix(rgb, other, amount):
    return tuple(a + (b - a) * amount for a, b in zip(rgb, other))


def blobs_for_tint(red, green, blue):
    """Pastel background colours that follow the monitor's colour tint (mood colours)."""
    if max(red, green, blue) - min(red, green, blue) < 8:
        return DEFAULT_BLOBS
    hue = colorsys.rgb_to_hsv(red / 100, green / 100, blue / 100)[0]
    blobs = []
    for (_, *place), shift in zip(DEFAULT_BLOBS, (0, .07, -.07)):
        rgb = tuple(c * 255 for c in colorsys.hsv_to_rgb((hue + shift) % 1, .32, 1))
        blobs.append((rgb, *place))
    return blobs


def _falloff(distance, radius):
    return math.exp(-2.2 * distance ** 2 / radius ** 2)


def bg_color(x, y, blobs):
    """Colour of the background at window point (x, y)."""
    c = BASE
    for rgb, cx, cy, radius, strength in blobs:
        k = strength * _falloff(x / W - cx, radius) * _falloff(y / W - cy * H / W, radius)
        c = mix(c, rgb, k)
    return c


def render_background(blobs):
    """Pixel data for the background at 1/BG_STEP size (run off the UI thread)."""
    w, h = W // BG_STEP + 1, H // BG_STEP + 1
    pre = [(rgb, strength,
            [_falloff(i * BG_STEP / W - cx, radius) for i in range(w)],
            [_falloff(j * BG_STEP / W - cy * H / W, radius) for j in range(h)])
           for rgb, cx, cy, radius, strength in blobs]
    rows = []
    for j in range(h):
        line = []
        for i in range(w):
            r, g, b = BASE
            for (br, bg, bb), strength, ex, ey in pre:
                k = strength * ex[i] * ey[j]
                r, g, b = r + (br - r) * k, g + (bg - g) * k, b + (bb - b) * k
            line.append("#%02x%02x%02x" % (int(r), int(g), int(b)))
        rows.append("{" + " ".join(line) + "}")
    return w, h, " ".join(rows)


# ---- drawing helpers -----------------------------------------------------------

def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


def glass(canvas, x1, y1, x2, y2, r, blobs, frost=.62, tag="glass"):
    """Draw a frosted-glass panel: the background seen through milky glass, a soft shadow and a white rim.

    Returns the panel's average fill colour (for widgets placed on top).
    """
    for offset, amount in ((px(10), .05), (px(5), .07)):  # soft two-step shadow
        shade = hexcolor(mix(bg_color((x1 + x2) / 2, y2, blobs), INK, amount))
        round_rect(canvas, x1 + px(2), y1 + offset, x2 - px(2), y2 + offset, r, fill=shade, outline="", tags=tag)
    xs = [x1 + (x2 - x1) * k / 4 for k in range(5)]
    fills = []
    step = 2
    for y in range(int(y1), int(y2), step):
        avg = tuple(sum(c) / len(xs) for c in zip(*(bg_color(x, y, blobs) for x in xs)))
        rgb = mix(avg, WHITE, frost)
        fills.append(rgb)
        edge = min(y - y1, y2 - y)
        inset = r - math.sqrt(max(0, r * r - (r - edge) ** 2)) if edge < r else 0
        canvas.create_line(x1 + inset, y, x2 - inset, y, fill=hexcolor(rgb), width=step + 1, tags=tag)
    mean = tuple(sum(c) / len(fills) for c in zip(*fills))
    round_rect(canvas, x1, y1, x2, y2, r, fill="", outline="#ffffff", width=max(1, round(px(1.5))), tags=tag)
    return mean


def capsule(canvas, x1, y, x2, height, fill, tags):
    """A perfectly round-ended bar (used for pills and sliders)."""
    return canvas.create_line(x1 + height / 2, y, max(x1 + height / 2, x2 - height / 2), y, width=height,
                              capstyle="round", fill=fill, tags=tags)


def pill(canvas, x, y, text, font, fill, fg, tags=(), anchor="w", height=30, min_width=0):
    """A rounded label. (x, y) is its left-middle (anchor w) or right-middle (anchor e). Returns its width."""
    height = px(height)
    width = max(px(min_width), tkfont.Font(font=font).measure(text) + px(28))
    x1 = x if anchor == "w" else x - width
    capsule(canvas, x1, y, x1 + width, height, fill, tags)
    canvas.create_text(x1 + width / 2, y, text=text, font=font, fill=fg, tags=tags)
    return width


def hotkey_listener(events):
    """Waits for Ctrl+Alt+M system-wide and posts an event to the UI thread."""
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_M):
        events.put(("hint", "Ctrl+Alt+M is taken by another app, so click this window to talk to me."))
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312 and msg.wParam == HOTKEY_ID:  # WM_HOTKEY
            events.put(("show",))


def light_title_bar(root):
    """Tint the Windows 11 title bar to match the app background."""
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        r, g, b = BASE
        color = ctypes.c_int((b << 16) | (g << 8) | r)  # caption colour as 0x00BBGGRR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
    except (AttributeError, OSError):
        pass


def make_icon(size=32):
    """The Lumi logo (accent square with a white waveform) as a window icon."""
    img = tk.PhotoImage(width=size, height=size)
    img.put(ACCENT, to=(0, 0, size, size))
    for i, h in enumerate((8, 16, 11, 20, 9)):
        x = 7 + i * 4
        img.put("#ffffff", to=(x, (size - h) // 2, x + 2, (size + h) // 2))
    return img


class App:
    def __init__(self, root):
        self.root = root
        self.mon = monitor.find_monitor()
        self.events = queue.Queue()
        self.pending = None
        self.busy = False
        self.focus = "brightness"
        self.local_state = {"display_mode": "extend", "laptop_brightness": 50}  # Windows settings we can't read back
        self.state = {**monitor.read_state(self.mon), **self.local_state}
        self.shown = {}                    # numbers currently drawn (they glide toward self.state)
        self.saved = stats.load()
        self.saved_shown = float(self.saved)
        self.animating = False
        self.party_stop = threading.Event()
        self.party_thread = None
        self.rendering = False
        self.reply_text = "Hi! Try “sunset vibes”, “movie mode”, “go to color” or “party”."
        self.heard_text = ""

        families = set(tkfont.families(root))
        body = "Segoe UI Variable Text" if "Segoe UI Variable Text" in families else "Segoe UI"
        display = "Segoe UI Variable Display" if "Segoe UI Variable Display" in families else "Segoe UI"
        icon = "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else body
        self.icons = ICONS if icon != body else FALLBACK_ICONS
        size = lambda pixels: -round(pixels * S)  # negative = pixels, so text matches the scaled layout
        self.f = {"title": (display, size(32), "bold"), "h2": (display, size(17), "bold"),
                  "body": (body, size(14)), "bold": (body, size(14), "bold"), "small": (body, size(12)),
                  "chip": (body, size(13)), "reply": (body, size(16)), "italic": (body, size(12), "italic"),
                  "entry": (body, size(17)), "icon": (icon, size(18)), "mic": (icon, size(17))}

        root.title(APP_NAME)
        root.resizable(False, False)
        root.configure(bg=hexcolor(BASE))
        root.attributes("-topmost", True)
        root.bind("<Escape>", lambda e: root.withdraw())
        self.icon = make_icon()
        root.iconphoto(True, self.icon)

        self.layout()  # also sets the window height to fit the cards
        root.geometry(f"{W}x{H}")
        self.canvas = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0, bg=hexcolor(BASE))
        self.canvas.pack()

        # The text box is the only real widget; Wispr Flow types into it
        self.text = tk.StringVar()
        self.text.trace_add("write", self.on_text_changed)
        self.entry = tk.Entry(self.canvas, textvariable=self.text, fg=FG, insertbackground=ACCENT, relief="flat",
                              font=self.f["entry"], bd=0)
        self.entry.bind("<Return>", lambda e: self.run())
        self.placeholder = tk.Label(self.canvas, text="Press your Wispr key and just speak…", fg=MUTED,
                                    font=self.f["entry"], cursor="xterm", bd=0)
        self.placeholder.bind("<Button-1>", lambda e: self.entry.focus_set())
        x1, y1, x2, y2 = self.command_box
        self.canvas.create_window(x1 + px(64), (y1 + y2) / 2, window=self.entry, anchor="w",
                                  width=x2 - x1 - px(200))
        self.placeholder_item = self.canvas.create_window(x1 + px(66), (y1 + y2) / 2, window=self.placeholder,
                                                          anchor="w")

        self.auto = tk.BooleanVar(value=True)
        self.speak = tk.BooleanVar(value=True)
        self.bind_clicks()

        self.theme_key, blobs = self.theme_for_state()
        self.apply_theme(*render_background(blobs), blobs)
        self.refresh()
        self.entry.focus_set()
        light_title_bar(root)
        threading.Thread(target=hotkey_listener, args=(self.events,), daemon=True).start()
        root.after(100, self.poll_events)

    # ---- layout -------------------------------------------------------------

    def layout(self):
        """Work out where every card, row, chip and slider goes (drawn later by draw_ui)."""
        global H
        self.command_box = (MARGIN, px(108), W - MARGIN, px(166))
        col_w = (W - 2 * MARGIN - 2 * GAP) / 3
        pad, chip_gap, line = px(22), px(6), px(36)
        chip_font = tkfont.Font(font=self.f["chip"])
        self.cards, self.rows = [], {}
        heights = {}
        for n, (category, items) in enumerate(brain.MENU):
            x1 = MARGIN + (n % 3) * (col_w + GAP)
            x2 = x1 + col_w
            y = px(60)  # relative to card top
            for setting, label, kind, options in items:
                row = {"kind": kind, "label": label, "y": y, "x1": x1 + pad, "x2": x2 - pad}
                if kind == "choice":
                    chip_w = max(px(48), max(chip_font.measure(pretty(o)) for o in options) + px(22))
                    per_line = max(1, int((col_w - 2 * pad + chip_gap) // (chip_w + chip_gap)))
                    row["chips"] = [(o, x1 + pad + (i % per_line) * (chip_w + chip_gap),
                                     y + px(34) + (i // per_line) * line, chip_w) for i, o in enumerate(options)]
                    y += px(26) + math.ceil(len(options) / per_line) * line + px(8)
                else:
                    y += px(54)
                self.rows[setting] = row
            if category == "Modes":
                y += px(26)  # room for a tip
            heights[n] = y + pad - px(14)
            self.cards.append({"category": category, "x1": x1, "x2": x2, "row": n // 3})
        top = px(232)
        row_h = [max(heights[n] for n in range(len(brain.MENU)) if n // 3 == r) for r in (0, 1)]
        for n, card in enumerate(self.cards):
            card["y1"] = top + sum(row_h[:card["row"]]) + GAP * card["row"]
            card["y2"] = card["y1"] + row_h[card["row"]]
            for setting, *_ in brain.MENU[n][1]:
                row = self.rows[setting]
                row["y"] += card["y1"]
                if "chips" in row:
                    row["chips"] = [(o, x, y + card["y1"], w) for o, x, y, w in row["chips"]]
        H = round(max(card["y2"] for card in self.cards) + px(62))

    def bind_clicks(self):
        c = self.canvas
        hand = lambda e: c.config(cursor="hand2")
        arrow = lambda e: c.config(cursor="")
        for setting, row in self.rows.items():
            tags = [f"chip|{setting}|{o}" for o, *_ in row.get("chips", [])] or [f"slider|{setting}"]
            for tag in tags:
                if tag.startswith("chip"):
                    option = tag.split("|", 2)[2]
                    c.tag_bind(tag, "<Button-1>", lambda e, s=setting, o=option: self.click(s, o))
                else:
                    c.tag_bind(tag, "<Button-1>", lambda e, s=setting: self.slide_click(s, e.x))
                c.tag_bind(tag, "<Enter>", hand)
                c.tag_bind(tag, "<Leave>", arrow)
        for name, var in (("auto", self.auto), ("speak", self.speak)):
            tag = f"toggle|{name}"
            c.tag_bind(tag, "<Button-1>", lambda e, v=var: (v.set(not v.get()), self.draw_ui()))
            c.tag_bind(tag, "<Enter>", hand)
            c.tag_bind(tag, "<Leave>", arrow)
        c.tag_bind("run", "<Button-1>", lambda e: self.run())
        c.tag_bind("run", "<Enter>", hand)
        c.tag_bind("run", "<Leave>", arrow)

    # ---- theme (background + glass) -------------------------------------------

    def theme_for_state(self):
        r, g, b = (self.state.get(k, 50) for k in ("red", "green", "blue"))
        if self.state.get("color_preset") != "user":
            r = g = b = 50
        return (round(r / 4), round(g / 4), round(b / 4)), blobs_for_tint(r, g, b)

    def update_theme(self):
        """Re-tint the background when the monitor's colour levels change (runs in a thread)."""
        key, blobs = self.theme_for_state()
        if key == self.theme_key or self.rendering:
            return
        self.theme_key = key
        self.rendering = True
        threading.Thread(target=lambda: self.events.put(("theme", *render_background(blobs), blobs)),
                         daemon=True).start()

    def apply_theme(self, w, h, data, blobs):
        self.rendering = False
        small = tk.PhotoImage(width=w, height=h)
        small.put(data)
        self.bg_image = small.zoom(BG_STEP)
        self.blobs = blobs
        c = self.canvas
        c.delete("bg", "glass")
        c.create_image(0, 0, image=self.bg_image, anchor="nw", tags="bg")
        self.card_fill = {}
        for card in self.cards:
            self.card_fill[card["category"]] = glass(c, card["x1"], card["y1"], card["x2"], card["y2"], px(22),
                                                     blobs)
        fill = glass(c, *self.command_box, px(30), blobs, frost=.8)
        for widget in (self.entry, self.placeholder):
            widget.config(bg=hexcolor(fill))
        c.tag_lower("glass")
        c.tag_lower("bg")
        self.draw_ui()
        self.update_theme()  # catch up if the tint changed while rendering

    # ---- drawing ------------------------------------------------------------

    def draw_ui(self):
        c, f = self.canvas, self.f
        c.delete("ui")

        # Header: logo, name, tagline, badges
        lx, ly = MARGIN, px(36)
        round_rect(c, lx, ly, lx + px(52), ly + px(52), px(16), fill=ACCENT, outline="", tags="ui")
        for i, h in enumerate((12, 24, 16, 28, 14)):
            x = lx + px(13 + i * 6.5)
            c.create_line(x, ly + px(26 - h / 2), x, ly + px(26 + h / 2), width=px(3.5), capstyle="round",
                          fill="#ffffff", tags="ui")
        c.create_text(lx + px(68), ly + px(14), text=APP_NAME, font=f["title"], fill=FG, anchor="w", tags="ui")
        c.create_text(lx + px(70), ly + px(42), text="Talk to your monitor.", font=f["body"], fill=MUTED,
                      anchor="w", tags="ui")
        badge_bg = hexcolor(mix(bg_color(W - px(200), px(60), self.blobs), WHITE, .7))
        connected = not self.mon.simulated
        x, y = W - MARGIN, ly + px(26)
        x -= pill(c, x, y, "●  Monitor connected" if connected else "●  Demo mode", f["small"], badge_bg,
                  "#3f9a74" if connected else "#b07a1e", tags="ui", anchor="e") + px(10)
        saved = round(self.saved_shown)
        pill(c, x, y, f"✦  {saved:,} button press{'es' if saved != 1 else ''} saved", f["small"], badge_bg, ACCENT,
             tags="ui", anchor="e")

        # Command bar
        x1, y1, x2, y2 = self.command_box
        mid = (y1 + y2) / 2
        mic = WARN if self.busy else ("#e58ac0" if self.party_running() else ACCENT)
        c.create_oval(x1 + px(14), mid - px(19), x1 + px(52), mid + px(19), fill=mic, outline="", tags="ui")
        c.create_text(x1 + px(33), mid, text=self.icons["mic"], font=f["mic"], fill="#ffffff", tags="ui")
        pill(c, x2 - px(12), mid, "Run  ↵", f["bold"], ACCENT, "#ffffff", tags=("ui", "run"), anchor="e", height=38)
        c.create_text(MARGIN + px(6), px(190), text=self.reply_text, font=f["reply"], fill=FG, anchor="w",
                      width=W - 2 * MARGIN, tags="ui")
        c.create_text(MARGIN + px(6), px(213), text=self.heard_text, font=f["italic"], fill=MUTED, anchor="w",
                      tags="ui")

        # Cards
        focus_cat = next(cat for cat, items in brain.MENU if any(i[0] == self.focus for i in items))
        for card in self.cards:
            cat = card["category"]
            fill = self.card_fill[cat]
            focused = cat == focus_cat
            if focused:
                round_rect(c, card["x1"], card["y1"], card["x2"], card["y2"], px(22), fill="", outline="#b9a9ff",
                           width=max(2, round(px(2))), tags="ui")
            c.create_text(card["x1"] + px(24), card["y1"] + px(32), text=self.icons[cat], font=f["icon"],
                          fill=ACCENT if focused else MUTED, anchor="w", tags="ui")
            c.create_text(card["x1"] + px(54), card["y1"] + px(32), text=cat, font=f["h2"], fill=FG, anchor="w",
                          tags="ui")
            if cat == "Modes":
                last = self.rows["mode"]["chips"][-1]
                c.create_text(card["x1"] + px(24), last[2] + px(38), text="Try “movie mode” or “sunset vibes”",
                              font=f["small"], fill=MUTED, anchor="w", tags="ui")
            for setting, *_ in next(items for name, items in brain.MENU if name == cat):
                self.draw_row(setting, self.rows[setting], fill)

        # Footer
        y = H - px(30)
        x = MARGIN
        for name, label, var in (("auto", "Run when I stop talking", self.auto),
                                 ("speak", "Talk back to me", self.speak)):
            on = var.get()
            x += pill(c, x, y, f"{'●' if on else '○'}  {label}", f["small"],
                      hexcolor(mix(bg_color(x, y, self.blobs), WHITE, .75 if on else .45)), FG if on else MUTED,
                      tags=("ui", f"toggle|{name}"), height=28) + px(10)
        c.create_text(W - MARGIN, y, text="Ctrl+Alt+M to open   ·   Esc to hide", font=f["small"], fill=MUTED,
                      anchor="e", tags="ui")

    def draw_row(self, setting, row, card_fill):
        c, f = self.canvas, self.f
        focused = setting == self.focus
        y = row["y"]
        if focused:
            c.create_oval(row["x1"] - px(13), y - px(3), row["x1"] - px(7), y + px(3), fill=ACCENT, outline="",
                          tags="ui")
        c.create_text(row["x1"], y, text=row["label"], font=f["bold"] if focused else f["body"],
                      fill=FG if focused else MUTED, anchor="w", tags="ui")
        value = self.state.get(setting)
        if "chips" in row:
            for option, x, cy, w in row["chips"]:
                on = option == value
                chip_fill = ACCENT if on else hexcolor(mix(card_fill, INK, .07))
                pill(c, x, cy, pretty(option), f["chip"], chip_fill, "#ffffff" if on else MUTED,
                     tags=("ui", f"chip|{setting}|{option}"), min_width=w / S, height=30)
            return
        shown = self.shown.get(setting)
        top = 10 if row["kind"] == "range10" else 100
        c.create_text(row["x2"], y, text="–" if shown is None else str(round(shown)), font=f["bold"],
                      fill=FG, anchor="e", tags="ui")
        sy, thick = y + px(24), px(8)
        tag = ("ui", f"slider|{setting}")
        c.create_rectangle(row["x1"], sy - px(10), row["x2"], sy + px(10), fill="", outline="", tags=tag)  # click area
        capsule(c, row["x1"], sy, row["x2"], thick, hexcolor(mix(card_fill, INK, .09)), tag)
        if shown:
            end = row["x1"] + thick + (row["x2"] - row["x1"] - thick) * min(shown, top) / top
            color = SLIDER_COLORS.get(setting, ACCENT if focused else "#ab9cff")
            capsule(c, row["x1"], sy, end, thick, color, tag)
            if focused:
                r = px(8)
                c.create_oval(end - thick / 2 - r, sy - r, end - thick / 2 + r, sy + r, fill="#ffffff",
                              outline=color, width=max(2, round(px(2))), tags=tag)

    def animate(self):
        """Glide the drawn numbers toward the real values (smooth sliders and counter)."""
        moving = False
        for setting, row in self.rows.items():
            if "chips" in row:
                continue
            target = self.state.get(setting)
            if not isinstance(target, (int, float)):
                self.shown[setting] = None
                continue
            current = self.shown.get(setting)
            if current is None:
                self.shown[setting] = float(target)
            elif abs(target - current) > .4:
                self.shown[setting] = current + (target - current) * .2
                moving = True
            else:
                self.shown[setting] = float(target)
        if abs(self.saved - self.saved_shown) > .5:
            self.saved_shown += (self.saved - self.saved_shown) * .12
            moving = True
        else:
            self.saved_shown = float(self.saved)
        self.draw_ui()
        if moving:
            self.root.after(16, self.animate)
        else:
            self.animating = False

    def refresh(self, new_state=None):
        if new_state is not None:
            self.state = {**new_state, **self.local_state}
        if not self.animating:
            self.animating = True
            self.animate()
        self.update_theme()

    # ---- events -------------------------------------------------------------

    def poll_events(self):
        while not self.events.empty():
            event = self.events.get()
            kind = event[0]
            if kind == "show":
                self.show()
            elif kind == "hint":
                self.reply_text = event[1]
                self.draw_ui()
            elif kind == "theme":
                self.apply_theme(*event[1:])
            elif kind == "live":       # party mode colour updates
                self.state.update(event[1])
                self.refresh()
            elif kind == "state":
                self.refresh(event[1])
            elif kind == "done":
                self.finish(*event[1:])
        self.root.after(50, self.poll_events)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.text.set("")
        self.entry.focus_set()

    def on_text_changed(self, *args):
        self.canvas.itemconfig(self.placeholder_item, state="hidden" if self.text.get() else "normal")
        if not self.auto.get() or not self.text.get().strip():
            return
        if self.pending:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(AUTO_RUN_DELAY_MS, self.run)

    def click(self, setting, option):
        """Mouse fallback: clicking a chip sets it directly."""
        self.start(pretty(option), [(setting, option), ("focus", setting)])

    def slide_click(self, setting, x):
        row = self.rows[setting]
        top = 10 if row["kind"] == "range10" else 100
        value = round(max(0, min(1, (x - row["x1"]) / (row["x2"] - row["x1"]))) * top)
        self.start(f"{row['label']} {value}", [(setting, value), ("focus", setting)])

    def run(self):
        if self.pending:
            self.root.after_cancel(self.pending)
            self.pending = None
        text = self.text.get().strip()
        if text:
            self.start(text, brain.quick_command(text, self.state, self.focus))

    def start(self, text, quick):
        if self.busy:
            return
        self.busy = True
        if not quick:
            self.reply_text = "On it…"
        self.draw_ui()
        threading.Thread(target=self.work, args=(text, dict(self.state), self.focus, quick, self.speak.get()),
                         daemon=True).start()

    def party_running(self):
        return self.party_thread is not None and self.party_thread.is_alive()

    def work(self, text, state, focus, quick, speak):
        if quick:
            reply, actions = "", quick
        else:
            reply, actions, _ = brain.interpret(text, state, focus)

        party = next((str(v).lower() for s, v in actions if s == "party"), None)
        if self.party_running() and party != "on":
            self.party_stop.set()      # any other command ends the party first
            self.party_thread.join(timeout=5)
        if party == "off" and not reply:
            reply = "Party's over. Colors are back to how they were."

        results, new_focus, close, glides, after = [], None, False, {}, []
        for setting, value in actions:
            if setting == "focus":
                new_focus = brain.resolve_focus(value) or new_focus
            elif setting == "close_menu":
                close = True
            elif setting == "party":
                continue
            elif setting == "mood":
                mood = str(value).lower()
                glides.update(brain.MOODS.get(mood, brain.MOODS["neutral"]))
                if mood == "neutral":
                    after.append(("color_preset", "normal"))
                new_focus = new_focus or "red"
            elif setting in monitor.GLIDE_SETTINGS:
                glides[setting] = value
                new_focus = new_focus or setting
            else:
                results.append(monitor.apply(self.mon, setting, value))
                if setting in self.local_state:
                    self.local_state[setting] = int(value) if str(value).isdigit() else value
                new_focus = new_focus or (setting if setting in self.rows else None)
        if glides:
            results += monitor.glide(self.mon, glides)
        for setting, value in after:
            results.append(monitor.apply(self.mon, setting, value))

        # Count the monitor button presses this saved
        changes = [(s, v) for s, v in actions if s not in ("party", "close_menu", "mood")]
        changes += [(s, v) for s, v in glides.items() if all(s != a for a, _ in actions)]
        self.saved += sum(stats.presses_for(s, state.get(s), v) for s, v in changes)
        stats.save(self.saved)

        failed = [r for r in results if "support" in r or "not available" in r or "Unknown" in r]
        if failed:
            reply = f"{reply} Some of that isn't available on this monitor, though.".strip()
        new_state = monitor.read_state(self.mon)
        self.events.put(("done", text, reply, new_focus, close, new_state))
        if party == "on" and not self.party_running():
            self.party_stop.clear()
            self.party_thread = threading.Thread(target=self.party, daemon=True)
            self.party_thread.start()
        if speak and reply:
            voice.speak(reply)

    def party(self):
        """Slowly cycle the screen through colours for a few seconds, then put everything back."""
        before = monitor.read_state(self.mon)
        monitor.apply(self.mon, "color_preset", "user")
        start = time.time()
        while time.time() - start < PARTY_SECONDS and not self.party_stop.is_set():
            hue = ((time.time() - start) / 5) % 1
            r, g, b = (30 + 45 * c for c in colorsys.hsv_to_rgb(hue, .75, 1))
            monitor.set_rgb_now(self.mon, r, g, b)
            self.events.put(("live", {"red": round(r), "green": round(g), "blue": round(b), "color_preset": "user"}))
            time.sleep(.35)
        monitor.glide(self.mon, {k: before.get(k, 50) for k in ("red", "green", "blue")})
        if before.get("color_preset") in monitor.COLOR_PRESETS:
            monitor.apply(self.mon, "color_preset", before["color_preset"])
        self.events.put(("state", monitor.read_state(self.mon)))

    def finish(self, text, reply, new_focus, close, new_state):
        self.busy = False
        if new_focus in self.rows:
            self.focus = new_focus
        label = next(item[1] for item in brain.MENU_ITEMS if item[0] == self.focus)
        self.reply_text = reply or f"Now on {label}. Say “more”, “less” or a value."
        self.heard_text = f"You said: “{text}”"
        self.text.set("")
        self.refresh(new_state)
        if close:
            self.root.after(800, self.root.withdraw)


def screen_scale(root):
    """Scale the 1120x800 design to the display: follow Windows scaling, but always fit on screen."""
    dpi_scale = root.winfo_fpixels("1i") / 96
    fit = min((root.winfo_screenwidth() - 40) / 1120, (root.winfo_screenheight() - 130) / 830)
    return max(.8, min(dpi_scale, fit))


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    set_scale(screen_scale(root))
    App(root)
    root.mainloop()
