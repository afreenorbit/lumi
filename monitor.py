"""Talks to the external monitor over HDMI using DDC/CI (Windows dxva2 API).

DDC/CI lets the PC read and change the same settings you'd normally change
with the buttons on the back of the monitor. Each setting has a "VCP code".
"""
import ctypes
import re
import subprocess
import time
from ctypes import wintypes

# VCP codes from the MCCS standard. Not every monitor supports every code.
VCP = {
    "brightness": 0x10,
    "contrast": 0x12,
    "color_preset": 0x14,
    "red": 0x16,
    "green": 0x18,
    "blue": 0x1A,
    "input": 0x60,
    "volume": 0x62,
    "mute": 0x8D,
    "power": 0xD6,
}

# Acer-specific codes (found by probing an Acer HA270). Only used on Acer monitors,
# since other brands may use these codes for something else.
ACER_VCP = {"mode": 0xE2, "black_boost": 0xE5, "blue_light": 0xE8}
MODES = {"user": 0, "standard": 1, "eco": 2, "graphics": 3, "movie": 4, "action": 5, "racing": 6, "sports": 7}
BLUE_LIGHT = {"off": 0, "80%": 1, "70%": 2, "60%": 3, "50%": 4}

COLOR_PRESETS = {"srgb": 0x01, "warm": 0x05, "normal": 0x06, "cool": 0x08, "user": 0x0B}
INPUTS = {"vga": 0x01, "dvi": 0x03, "hdmi": 0x11, "hdmi2": 0x12, "displayport": 0x0F}
POWER = {"on": 0x01, "standby": 0x04, "off": 0x05}

# Settings stored as 0-100 numbers
NUMERIC = ("brightness", "contrast", "red", "green", "blue", "volume")


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("handle", wintypes.HANDLE), ("description", wintypes.WCHAR * 128)]


_dxva2 = ctypes.WinDLL("dxva2")
_user32 = ctypes.WinDLL("user32")
_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)
# Declare signatures so 64-bit handles aren't truncated
_user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONITORENUMPROC, wintypes.LPARAM]
_dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, ctypes.POINTER(wintypes.DWORD)]
_dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, wintypes.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
_dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [
    wintypes.HANDLE, ctypes.c_ubyte, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)
]
_dxva2.SetVCPFeature.argtypes = [wintypes.HANDLE, ctypes.c_ubyte, wintypes.DWORD]
_dxva2.GetCapabilitiesStringLength.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_dxva2.CapabilitiesRequestAndCapabilitiesReply.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD]


def _physical_monitors():
    hmons = []

    def cb(hmon, hdc, rect, lparam):
        hmons.append(hmon)
        return True

    _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(cb), 0)
    result = []
    for hmon in hmons:
        count = wintypes.DWORD()
        if not _dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
            continue
        arr = (PHYSICAL_MONITOR * count.value)()
        if _dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, arr):
            result.extend(arr)
    return result


class DDCMonitor:
    """A real monitor reachable over DDC/CI."""

    simulated = False

    def __init__(self, handle, name):
        self.handle = handle
        self.name = name or "External monitor"
        self.caps = ""
        self.saved_volume = None
        self.last = {}  # last values we set, for settings the monitor can't report back

    def load_capabilities(self):
        """Read the monitor's capabilities string: its model and which values it accepts."""
        length = wintypes.DWORD()
        if _dxva2.GetCapabilitiesStringLength(self.handle, ctypes.byref(length)):
            buf = ctypes.create_string_buffer(length.value)
            if _dxva2.CapabilitiesRequestAndCapabilitiesReply(self.handle, buf, length):
                self.caps = buf.value.decode(errors="replace")
        model = re.search(r"model\(([^)]*)\)", self.caps)
        if model:
            self.name = model.group(1)
        if self.name.upper().startswith("ACER"):
            self.extra = ACER_VCP

    extra = {}  # brand-specific settings this monitor supports

    def allowed_values(self, code):
        """Values the monitor lists for a code, e.g. 14(05 06 08 0B) -> {5, 6, 8, 11}. None if unknown."""
        match = re.search(rf"\b{code:02X}\(([0-9A-Fa-f ]+)\)", self.caps)
        return {int(v, 16) for v in match.group(1).split()} if match else None

    def get(self, code):
        cur, mx = wintypes.DWORD(), wintypes.DWORD()
        ok = _dxva2.GetVCPFeatureAndVCPFeatureReply(
            self.handle, code, None, ctypes.byref(cur), ctypes.byref(mx)
        )
        return (cur.value, mx.value) if ok else None

    def set(self, code, value):
        return bool(_dxva2.SetVCPFeature(self.handle, code, int(value)))


class SimulatedMonitor:
    """Stand-in used when no DDC monitor is connected (demo mode)."""

    simulated = True
    name = "Demo monitor (simulated)"
    saved_volume = None
    extra = ACER_VCP  # demo behaves like the Acer so every option can be tried

    def allowed_values(self, code):
        return None

    def __init__(self):
        self.last = {}
        self.values = {
            ACER_VCP["mode"]: MODES["user"], ACER_VCP["black_boost"]: 5, ACER_VCP["blue_light"]: 0,
            VCP["brightness"]: 70, VCP["contrast"]: 50, VCP["color_preset"]: COLOR_PRESETS["normal"],
            VCP["red"]: 50, VCP["green"]: 50, VCP["blue"]: 50, VCP["input"]: INPUTS["hdmi"],
            VCP["volume"]: 50, VCP["mute"]: 2, VCP["power"]: POWER["on"],
        }

    def get(self, code):
        return (self.values[code], 100) if code in self.values else None

    def set(self, code, value):
        self.values[code] = int(value)
        return True


def find_monitor():
    """Return the first monitor that answers DDC/CI, or a simulated one."""
    for pm in _physical_monitors():
        mon = DDCMonitor(pm.handle, pm.description)
        if mon.get(VCP["brightness"]) is not None:
            mon.load_capabilities()
            return mon
    return SimulatedMonitor()


def read_state(mon):
    """Current values of the settings the monitor reports, as a dict."""
    state = {}
    for name in NUMERIC:
        reply = mon.get(VCP[name])
        if reply:
            cur, mx = reply
            state[name] = round(cur * 100 / mx) if mx else cur
    reply = mon.get(VCP["color_preset"])
    if reply:
        state["color_preset"] = next((k for k, v in COLOR_PRESETS.items() if v == reply[0]), hex(reply[0]))
    reply = mon.get(VCP["input"])
    if reply:
        state["input"] = next((k for k, v in INPUTS.items() if v == reply[0]), hex(reply[0]))
    if mon.extra:
        reply = mon.get(ACER_VCP["mode"])
        if reply:
            state["mode"] = next((k for k, v in MODES.items() if v == reply[0]), hex(reply[0]))
        reply = mon.get(ACER_VCP["black_boost"])
        if reply:
            state["black_boost"] = reply[0]
        reply = mon.get(ACER_VCP["blue_light"])
        state["blue_light"] = next((k for k, v in BLUE_LIGHT.items() if reply and v == reply[0]),
                                   mon.last.get("blue_light", "off"))
    if "volume" in state:
        state["mute"] = "on" if state["volume"] == 0 else "off"
    return state


def apply(mon, setting, value):
    """Apply one action. Returns a short human-readable result string."""
    if setting == "display_mode":
        return set_display_mode(value)
    if setting == "laptop_brightness":
        return set_laptop_brightness(int(value))
    if setting in ACER_VCP:
        if setting not in mon.extra:
            return f"{setting}: not available on this monitor"
        if setting == "black_boost":
            raw = key = max(0, min(10, int(round(float(value)))))
        else:
            options = MODES if setting == "mode" else BLUE_LIGHT
            key = str(value).lower().replace(" mode", "").strip()
            if setting == "blue_light" and key.isdigit():
                key += "%"
            raw = options.get(key)
            if raw is None:
                return f"Unknown value '{value}' for {setting}"
        if not mon.set(ACER_VCP[setting], raw):
            return f"{setting}: monitor did not accept this"
        mon.last[setting] = key
        return f"{setting} -> {key}"

    if setting in NUMERIC:
        if setting in ("red", "green", "blue"):
            _use_user_colors(mon)
        value = max(0, min(100, int(round(float(value)))))
        reply = mon.get(VCP[setting])
        raw = round(value * reply[1] / 100) if reply and reply[1] else value
    elif setting == "color_preset":
        raw = COLOR_PRESETS.get(str(value).lower())
    elif setting == "input":
        raw = INPUTS.get(str(value).lower())
    elif setting == "power":
        raw = POWER.get(str(value).lower())
    elif setting == "mute":
        mute = str(value).lower() in ("on", "true", "1", "yes")
        if mon.get(VCP["mute"]) is not None:
            raw = 1 if mute else 2
        else:
            # Monitor has no mute command (e.g. Acer HA270): mute by setting volume to 0
            if mute:
                reply = mon.get(VCP["volume"])
                if reply and reply[0] > 0:
                    mon.saved_volume = reply[0]
                return apply(mon, "volume", 0).replace("volume -> 0", "muted")
            return apply(mon, "volume", mon.saved_volume or 50).replace("volume", "unmuted, volume")
    else:
        return f"Unknown setting '{setting}'"

    if raw is None:
        return f"Unknown value '{value}' for {setting}"
    allowed = mon.allowed_values(VCP[setting]) if setting in ("color_preset", "input", "power") else None
    if allowed is not None and raw not in allowed:
        return f"{setting}: this monitor doesn't support '{value}'"
    if mon.set(VCP[setting], raw):
        return f"{setting} -> {value}"
    return f"{setting}: monitor did not accept this (may be unsupported)"


GLIDE_SETTINGS = NUMERIC + ("black_boost",)


def _code_and_max(mon, setting):
    code = ACER_VCP["black_boost"] if setting == "black_boost" else VCP[setting]
    reply = mon.get(code)
    return code, reply


def glide(mon, targets, duration=0.5):
    """Move several numeric settings to new values gradually, so changes fade instead of jumping.

    targets: {setting: value}, values 0-100 (0-10 for black_boost). Returns result strings.
    """
    plan, results = {}, []
    for setting, value in targets.items():
        if setting == "black_boost" and setting not in mon.extra:
            results.append(f"{setting}: not available on this monitor")
            continue
        code, reply = _code_and_max(mon, setting)
        if reply is None:
            results.append(f"{setting}: monitor did not accept this (may be unsupported)")
            continue
        top = 10 if setting == "black_boost" else 100
        value = max(0, min(top, int(round(float(value)))))
        end = value if setting == "black_boost" or not reply[1] else round(value * reply[1] / 100)
        plan[setting] = (code, reply[0], end)
        results.append(f"{setting} -> {value}")
    if any(s in plan for s in ("red", "green", "blue")):
        _use_user_colors(mon)

    # Each DDC write takes ~50 ms, so use fewer steps when several settings move together
    steps = max(2, 8 // max(1, len(plan)))
    last = {s: start for s, (_, start, _) in plan.items()}
    for i in range(1, steps + 1):
        t = 1 - (1 - i / steps) ** 2  # ease out: fast start, gentle landing
        for setting, (code, start, end) in plan.items():
            value = round(start + (end - start) * t)
            if value != last[setting]:
                mon.set(code, value)
                last[setting] = value
        time.sleep(duration / steps)
    return results


def _use_user_colors(mon):
    """Red/green/blue levels only take effect in the monitor's "user" color mode."""
    reply = mon.get(VCP["color_preset"])
    if reply and reply[0] != COLOR_PRESETS["user"]:
        mon.set(VCP["color_preset"], COLOR_PRESETS["user"])


def set_rgb_now(mon, red, green, blue):
    """Set color levels immediately (used by party mode, which animates itself)."""
    for name, value in (("red", red), ("green", green), ("blue", blue)):
        mon.set(VCP[name], max(0, min(100, int(value))))


DISPLAY_MODES = {"extend": "/extend", "duplicate": "/clone", "laptop_only": "/internal", "monitor_only": "/external"}


def set_display_mode(mode):
    flag = DISPLAY_MODES.get(str(mode).lower())
    if not flag:
        return f"Unknown display mode '{mode}'"
    subprocess.Popen(["DisplaySwitch.exe", flag])
    return f"display mode -> {mode}"


def set_laptop_brightness(value):
    value = max(0, min(100, value))
    cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{value})"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True)
    return f"laptop brightness -> {value}" if r.returncode == 0 else "laptop brightness: failed"
