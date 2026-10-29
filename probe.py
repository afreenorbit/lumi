"""Hardware check: does the monitor answer DDC/CI over HDMI?

Run with the monitor plugged in:  python probe.py
Add --blink to briefly dim the monitor and restore it.
"""
import ctypes
import sys
import time
from ctypes import wintypes

import monitor

_dxva2 = ctypes.WinDLL("dxva2")
_dxva2.GetCapabilitiesStringLength.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_dxva2.CapabilitiesRequestAndCapabilitiesReply.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD]


def capabilities(handle):
    length = wintypes.DWORD()
    if not _dxva2.GetCapabilitiesStringLength(handle, ctypes.byref(length)):
        return None
    buf = ctypes.create_string_buffer(length.value)
    if not _dxva2.CapabilitiesRequestAndCapabilitiesReply(handle, buf, length):
        return None
    return buf.value.decode(errors="replace")


monitors = monitor._physical_monitors()
print(f"Found {len(monitors)} display(s)\n")
for pm in monitors:
    mon = monitor.DDCMonitor(pm.handle, pm.description)
    print(f"== {mon.name}")
    for name, code in monitor.VCP.items():
        reply = mon.get(code)
        print(f"   {name:13} {'not supported' if reply is None else f'{reply[0]} (max {reply[1]})'}")
    caps = capabilities(pm.handle)
    print(f"   capabilities: {caps or 'none reported'}\n")

    if "--blink" in sys.argv and mon.get(monitor.VCP["brightness"]):
        before = mon.get(monitor.VCP["brightness"])[0]
        print(f"   Dimming to 0 for 2 seconds, then back to {before}...")
        mon.set(monitor.VCP["brightness"], 0)
        time.sleep(2)
        mon.set(monitor.VCP["brightness"], before)
        print("   Done. If the monitor went dark and came back, DDC/CI works!\n")
