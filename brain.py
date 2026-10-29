"""Turns a dictated sentence into a list of monitor actions.

Uses an LLM on Groq when GROQ_API_KEY is set, otherwise a simple
built-in keyword parser so the app still works offline / for free.
"""
import json
import re
import urllib.request

import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# The on-screen menu, laid out like the monitor's own buttons menu.
# Each item: (setting, label, kind, options). kind is "pct" (0-100), "range10" (0-10) or "choice".
MENU = [
    ("Modes", [("mode", "Picture mode", "choice",
                ["user", "standard", "eco", "graphics", "movie", "action", "racing", "sports"])]),
    ("Picture", [("brightness", "Brightness", "pct", None), ("contrast", "Contrast", "pct", None),
                 ("black_boost", "Black Boost", "range10", None),
                 ("blue_light", "Blue Light", "choice", ["off", "80%", "70%", "60%", "50%"])]),
    ("Color", [("color_preset", "Color temp", "choice", ["warm", "normal", "cool", "user"]),
               ("red", "Red", "pct", None), ("green", "Green", "pct", None), ("blue", "Blue", "pct", None)]),
    ("Audio", [("volume", "Volume", "pct", None), ("mute", "Mute", "choice", ["off", "on"])]),
    ("Input", [("input", "Source", "choice", ["hdmi", "vga"])]),
    ("Display", [("display_mode", "Screens", "choice", ["extend", "duplicate", "laptop_only", "monitor_only"]),
                 ("laptop_brightness", "Laptop screen", "pct", None)]),
]
MENU_ITEMS = [item for _, items in MENU for item in items]


def resolve_focus(name):
    """Map a setting or category name ("audio") to a menu setting ("volume"). None if unknown."""
    name = str(name).lower().replace(" ", "_")
    for category, items in MENU:
        if name == category.lower():
            return items[0][0]
    return name if any(item[0] == name for item in MENU_ITEMS) else None

SETTINGS = [item[0] for item in MENU_ITEMS] + ["power", "focus", "close_menu", "mood", "party"]

# Mood tints: red/green/blue levels (50 = neutral) plus an optional brightness
MOODS = {
    "neutral": {"red": 50, "green": 50, "blue": 50},
    "sunset": {"red": 70, "green": 42, "blue": 25, "brightness": 60},
    "cozy": {"red": 66, "green": 46, "blue": 26, "brightness": 40},
    "cyberpunk": {"red": 62, "green": 28, "blue": 75},
    "ocean": {"red": 25, "green": 50, "blue": 70},
    "forest": {"red": 30, "green": 66, "blue": 35},
    "rose": {"red": 70, "green": 38, "blue": 50},
    "arctic": {"red": 40, "green": 55, "blue": 72, "brightness": 80},
}

# Named "scenes" the user can ask for in plain words (on top of the monitor's own picture modes)
SCENES = {
    "reading": [("brightness", 40), ("contrast", 50), ("color_preset", "warm")],
    "night": [("brightness", 15), ("contrast", 45), ("color_preset", "warm"), ("blue_light", "50%")],
    "work": [("brightness", 60), ("contrast", 50), ("color_preset", "normal")],
    "presentation": [("brightness", 100), ("contrast", 60), ("color_preset", "normal")],
}

MENU_TEXT = "\n".join(
    f"  {cat}: " + ", ".join(f"{s} ({kind if not opts else '/'.join(opts)})" for s, _, kind, opts in items)
    for cat, items in MENU
)

SYSTEM_PROMPT = f"""You control a computer monitor connected over HDMI, plus a few Windows display settings.
An on-screen menu shows every setting and highlights one item ("focus"). The user navigates it by voice.
Convert the user's spoken request into actions. Use absolute values computed from the current state.

Menu (category: setting (allowed values)), in order; pct = integer 0-100, range10 = integer 0-10:
{MENU_TEXT}

Other actions:
- power: "standby" or "off" (warn the user they need the power button to turn it back on)
- focus: a setting name from the menu, to move the highlight there
- close_menu: "yes", to hide the menu
- mood: one of {", ".join(MOODS)}, a preset color tint for the whole screen
- party: "on" starts a short party mode where the screen slowly cycles through colors; "off" stops it

Scenes you can use as a starting point: {json.dumps(SCENES)}

Moods and colors:
- Vibes, moods and tints ("sunset vibes", "make it cozy", "cyberpunk", "make the screen pink") change the screen's
  color tint. Use the mood action if one of the presets fits. Otherwise set red, green and blue directly
  (50 = neutral; raise a channel to push the screen toward that color, lower the others) and maybe brightness.
  Example: "rainy café" -> red 55, green 50, blue 58, brightness 45.
- "normal colors" / "reset the colors" / "remove the tint" -> mood neutral.
- "party", "disco", "let's party" -> party on. "stop" / "stop the party" -> party off.
- For moods and party mode, make the reply playful and short, e.g. "Sunset mode. Golden hour, all day."

Menu navigation:
- "go to / open / show <category or setting>" -> focus on that setting (a category means its first setting).
- "down" / "next" -> focus the next setting in menu order; "up" / "previous" / "back" -> the one before.
- "more" / "increase" / "higher" / "less" / "decrease" / "lower" with no setting named -> change the focused setting
  (pct: +-10, range10: +-1, choice: next/previous option).
- A bare value ("70", "warm", "movie", "max") with no setting named applies to the focused setting if it fits.
- When the user changes a specific setting, also focus it so they can see it.
- "close" / "hide the menu" / "that's all" -> close_menu. NEVER use close_menu unless the user explicitly asks
  to close or hide the menu.

Guidance:
- Picture mode names (user, standard, eco, graphics, movie, action, racing, sports) are the monitor's built-in modes:
  "movie mode" -> mode movie, "gaming mode" -> mode action.
- One sentence can contain several requests ("brightness full, sound full"): return one action for each.
- "full" / "max" = 100, "half" = 50, "minimum" / "lowest" = 0. "sound" / "audio" means volume.
- "a bit" / "slightly" means about 10-15 points, "a lot" about 30.
- Tired eyes, eye strain, late night, or comfort: apply the "night" scene (low brightness AND warm colors).
- "Second screen only" means monitor_only.
- The text comes from speech-to-text, so allow for misheard words ("brightens" = brightness).
- If the request has nothing to do with the display, return no actions and say so in reply.
- reply: one short spoken-style sentence confirming what you changed, e.g. "Brightness and volume are at full."."""

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "setting": {"type": "string", "enum": SETTINGS},
                    "value": {"type": "string"},
                },
                "required": ["setting", "value"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reply", "actions"],
    "additionalProperties": False,
}


def ai_available():
    return bool(config.get("GROQ_API_KEY"))


def interpret(text, state, focus="brightness"):
    """Return (reply, [(setting, value), ...], source)."""
    if ai_available():
        try:
            reply, actions = _interpret_ai(text, state, focus)
            return reply, actions, "Groq AI"
        except Exception as e:  # network down, bad key, etc. -> fall back
            reply, actions = _interpret_rules(text, state)
            return reply, actions, f"rules (AI failed: {e})"
    reply, actions = _interpret_rules(text, state)
    return reply, actions, "rules"


def _interpret_ai(text, state, focus):
    body = {
        "model": config.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        "reasoning_effort": "low",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Current monitor state: {json.dumps(state)}\n"
                                        f"Menu focus: {focus}\n\nRequest: {text}"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "monitor_actions", "strict": True, "schema": SCHEMA},
        },
    }
    request = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {config.get('GROQ_API_KEY')}",
            "Content-Type": "application/json",
            "User-Agent": "Lumi/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    data = json.loads(result["choices"][0]["message"]["content"])
    return data["reply"], [(a["setting"], a["value"]) for a in data["actions"]]


NEXT_WORDS = {"down", "next", "go down", "next one", "move down"}
PREV_WORDS = {"up", "previous", "go up", "back", "go back", "move up"}
MORE_WORDS = {"more", "increase", "higher", "plus", "raise", "turn it up"}
LESS_WORDS = {"less", "decrease", "lower", "minus", "reduce", "turn it down"}
CLOSE_WORDS = {"close", "hide", "close menu", "hide menu", "done", "that's all", "thats all", "exit"}
STOP_WORDS = {"stop", "stop it", "stop the party", "party over", "enough"}


def quick_command(text, state, focus):
    """Instant handling of one-word menu navigation, without calling the AI. None if not a quick command."""
    t = re.sub(r"[^\w\s']", "", text.lower()).strip()
    keys = [item[0] for item in MENU_ITEMS]
    i = keys.index(focus) if focus in keys else 0
    if t in NEXT_WORDS:
        return [("focus", keys[(i + 1) % len(keys)])]
    if t in PREV_WORDS:
        return [("focus", keys[(i - 1) % len(keys)])]
    if t in CLOSE_WORDS:
        return [("close_menu", "yes")]
    if t in STOP_WORDS:
        return [("party", "off")]
    if t in MORE_WORDS or t in LESS_WORDS:
        direction = 1 if t in MORE_WORDS else -1
        _, _, kind, options = MENU_ITEMS[i]
        current = state.get(focus)
        if kind == "choice":
            pos = options.index(current) if current in options else 0
            return [(focus, options[max(0, min(len(options) - 1, pos + direction))])]
        step, top = (1, 10) if kind == "range10" else (10, 100)
        current = current if isinstance(current, int) else top // 2
        return [(focus, max(0, min(top, current + direction * step)))]
    return None


def _interpret_rules(text, state):
    t = text.lower()
    actions = []

    if re.search(r"\b(party|disco)\b", t):
        return "Let's party!", [("party", "on")]
    if re.search(r"\b(normal|reset|neutral)\b.*\bcolou?rs?\b|\bremove the tint\b", t):
        return "Colors are back to normal.", [("mood", "neutral")]
    for mood in MOODS:
        if mood != "neutral" and re.search(rf"\b{mood}\b", t):
            return f"{mood.capitalize()} vibes, coming right up.", [("mood", mood)]

    for mode in MENU_ITEMS[0][3]:
        if re.search(rf"\b{mode} mode\b", t):
            return f"Switched to {mode} mode.", [("mode", mode), ("focus", "mode")]
    if re.search(r"blue ?light (filter )?(on|max)", t):
        actions.append(("blue_light", "50%"))
    elif re.search(r"blue ?light (filter )?off", t):
        actions.append(("blue_light", "off"))

    for scene, steps in SCENES.items():
        if re.search(rf"\b{scene}\b", t):
            return f"Switched to {scene} mode.", list(steps)

    if re.search(r"(eyes? (hurt|strain)|too bright|blinding|night)", t):
        return "Made it easier on your eyes.", list(SCENES["night"])

    if "extend" in t:
        actions.append(("display_mode", "extend"))
    elif re.search(r"(duplicate|mirror|clone)", t):
        actions.append(("display_mode", "duplicate"))
    elif re.search(r"(only (the )?(monitor|second|external)|(monitor|second screen|external) only)", t):
        actions.append(("display_mode", "monitor_only"))
    elif re.search(r"(laptop|main) (screen )?only", t):
        actions.append(("display_mode", "laptop_only"))

    for preset in ("warm", "cool", "srgb"):
        if preset in t:
            actions.append(("color_preset", preset))
    if re.search(r"\b(normal|neutral) colou?r", t):
        actions.append(("color_preset", "normal"))

    step = 30 if "a lot" in t else 10
    target = "laptop_brightness" if "laptop" in t else "brightness"
    for setting, up_words, down_words in (
        (target, r"(brighter|brighten|more bright|increase brightness)", r"(dimmer|dim|darker|less bright|reduce brightness|lower brightness)"),
        ("contrast", r"(more contrast|increase contrast|contrast up)", r"(less contrast|reduce contrast|lower contrast|contrast down)"),
        ("volume", r"(louder|volume up|increase volume)", r"(quieter|softer|volume down|lower volume)"),
    ):
        word = {"volume": "(?:volume|sound|audio)", "contrast": "contrast"}.get(setting, "brightness")
        current = state.get(setting, 50)
        number = re.search(rf"{word}\D{{0,15}}?(\d{{1,3}})", t)
        level = re.search(rf"{word}\W*(?:to |at |on )?(full|max(?:imum)?|half|min(?:imum)?|zero)\b", t) or \
            re.search(rf"\b(full|max(?:imum)?|half|min(?:imum)?|zero)\s+{word}", t)
        if number:
            actions.append((setting, int(number.group(1))))
        elif level:
            actions.append((setting, {"half": 50}.get(level.group(1), 0 if level.group(1)[:2] in ("mi", "ze") else 100)))
        elif re.search(up_words, t):
            actions.append((setting, min(100, current + step)))
        elif re.search(down_words, t):
            actions.append((setting, max(0, current - step)))

    if re.search(r"\bunmute\b", t):
        actions.append(("mute", "off"))
    elif re.search(r"\bmute\b", t):
        actions.append(("mute", "on"))

    for name in ("hdmi", "vga", "dvi", "displayport"):
        if re.search(rf"(switch|change|input).*\b{name}\b", t):
            actions.append(("input", name))

    if re.search(r"(turn off|switch off|sleep|standby).*(monitor|screen|display)", t):
        actions.append(("power", "standby"))

    if not actions:
        return "Sorry, I didn't understand that. Try 'brightness 50' or 'reading mode'.", []
    return "Done.", actions
