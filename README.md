# Lumi

**Talk to your monitor.**

Lumi lets you control your monitor's settings with your voice, instead of reaching behind the screen and pressing tiny buttons you can't see.

[![Watch the Lumi demo](https://img.youtube.com/vi/xnDDsh-2P4c/maxresdefault.jpg)](https://youtu.be/xnDDsh-2P4c)

▶ **Demo video:** https://youtu.be/xnDDsh-2P4c

---

## The problem

Changing a monitor setting usually means reaching behind the screen, pressing buttons you can't see, and clicking through menu after menu. On an Acer HA270, switching to Movie mode takes about 8 button presses. For people with limited hand mobility, tremors or poor eyesight, those buttons are a real barrier.

## What Lumi does

You just say what you want:

> "Brightness full, sound full"
> "Movie mode"
> "My eyes are tired"
> "Go to color… down… more"
> "Sunset vibes"

Lumi understands normal sentences (even several requests at once), changes the real settings on the monitor, and tells you out loud what it did.

It works over the **HDMI cable you already have**. No extra hardware.

## Features

- **Natural language.** "Make it a bit brighter and turn the sound down to half" works. So do misheard words from speech-to-text.
- **Real monitor settings.** Brightness, contrast, color temperature, red/green/blue levels, volume, input source, and on Acer monitors the built-in picture modes (Movie, ECO, Racing…), Black Boost and Blue Light filter.
- **Windows display settings.** Extend or duplicate screens, laptop-only or monitor-only, and laptop screen brightness.
- **An on-screen menu you navigate by voice.** Every setting is visible at once, like the monitor's own menu, with the current item highlighted.
- **Talks back.** Every change is confirmed out loud ("Brightness and volume are at full.").
- **Mood colors.** "Sunset vibes", "cozy", "cyberpunk", "ocean", or anything you can describe ("make it feel like a rainy café"). Lumi tints the whole monitor, and the app's own background changes color to match.
- **Party mode.** "Party" slowly cycles the screen through colors for about 15 seconds, then puts everything back. "Stop" ends it early.
- **Scenes.** "Reading", "night", "work" and "presentation" change several settings at once.
- **Smooth fades.** Brightness, contrast and colors glide to their new values instead of jumping.
- **Buttons-saved counter.** Shows how many monitor button presses your voice commands have saved you.
- **Works without a monitor.** With no compatible monitor plugged in, Lumi starts in demo mode with a simulated monitor, so every command can still be tried.
- **Mouse fallback.** Every option and slider can also be clicked.

## Using it

Press **Ctrl+Alt+M** anywhere to open Lumi, then speak (or type) a command. It runs automatically about a second after you stop talking.

| Say | What happens |
|---|---|
| "brightness 70", "a bit brighter" | changes a setting directly |
| "brightness full, sound full" | several changes at once |
| "movie mode", "gaming mode" | switches the monitor's own picture mode |
| "my eyes are tired" | dims the screen and warms the colors |
| "sunset vibes", "make it cozy" | tints the screen to a mood |
| "party" / "stop" | starts / stops party mode |
| "normal colors" | removes any tint |
| "extend my screen", "mirror" | changes the Windows display mode |
| "go to color", "open audio" | moves the highlight to that section |
| "down" / "up" | next / previous setting (instant) |
| "more" / "less" | changes the highlighted setting |
| "70", "warm" | sets the highlighted setting |
| "close" | hides the menu (Esc also works) |

## How it works

```
You speak  ──►  Lumi's command box receives the words
                    │
                    ▼
        brain.py: an LLM (Groq) turns the sentence into actions
          "brightness full, sound full"
          → brightness = 100, volume = 100
          (offline fallback: a built-in keyword parser)
                    │
                    ▼
        monitor.py: sends each change to the monitor over HDMI (DDC/CI)
          numbers fade smoothly to their new values
                    │
                    ▼
        voice.py: says the result out loud (ElevenLabs, or the Windows voice)
        app.py:   updates the on-screen menu
```

### DDC/CI: the hidden remote control in your cable

Most monitors made in the last 15 years support **DDC/CI**, a channel inside the HDMI/DisplayPort cable that lets a computer change the same settings as the buttons on the back. Windows exposes it through its built-in `dxva2` API, which Lumi calls directly.

Each setting has a number called a VCP code:

| Setting | VCP code |
|---|---|
| Brightness | `0x10` |
| Contrast | `0x12` |
| Color preset | `0x14` |
| Red / green / blue | `0x16` / `0x18` / `0x1A` |
| Input source | `0x60` |
| Volume | `0x62` |

So "brightness 70" means *write the value 70 to code `0x10`*.

Picture modes, Black Boost and Blue Light are **Acer-specific** codes (`0xE2`, `0xE5`, `0xE8`). They were found by reading the monitor's list of supported codes and probing an Acer HA270, and they're only used on Acer monitors.

### Understanding what you say

`brain.py` sends the LLM three things: the full menu, the monitor's current values, and your sentence. It asks for a fixed JSON reply:

```json
{
  "reply": "Brightness and volume are at full.",
  "actions": [
    {"setting": "brightness", "value": "100"},
    {"setting": "volume", "value": "100"}
  ]
}
```

Because the model knows the current values, relative requests work ("a bit brighter" from 60 becomes 70). Because it knows the menu, navigation works ("go to color" moves the highlight). One-word navigation like "down" or "more" is handled instantly without the AI.

## Getting started

Requires **Windows** and **Python 3.10+**. No packages to install: Lumi only uses the Python standard library.

```bash
python app.py
```

Or double-click `run.bat`.

**API keys (optional).** Copy `.env.example` to `.env` and fill it in:

```
GROQ_API_KEY=your-groq-key
GROQ_MODEL=openai/gpt-oss-120b
ELEVENLABS_API_KEY=your-elevenlabs-key
```

- `GROQ_API_KEY` enables natural-language understanding ("I'm about to watch a movie", "my eyes are tired"). Without it, a built-in keyword parser handles simpler phrases.
- `ELEVENLABS_API_KEY` enables the spoken replies. Without it, the built-in Windows voice is used.

The `.env` file is ignored by git, so your keys stay on your machine.

### Checking your monitor

```bash
python probe.py --blink
```

This lists which settings your monitor supports and briefly dims it to prove DDC/CI works. If everything shows "not supported", turn on **DDC/CI** in your monitor's own on-screen menu.

## Project structure

| File | What it does |
|---|---|
| `app.py` | The window: the frosted-glass menu, the command box, the Ctrl+Alt+M hotkey, animations, party mode |
| `brain.py` | Turns a sentence into actions (Groq LLM, plus the offline keyword parser); defines the menu, scenes and moods |
| `monitor.py` | Talks to the monitor over DDC/CI, smooth fades, Windows display settings, demo-mode monitor |
| `voice.py` | Spoken replies (ElevenLabs, falling back to the Windows voice) |
| `stats.py` | The buttons-saved counter |
| `config.py` | Reads API keys from `.env` |
| `probe.py` | Hardware check for your monitor |
| `run.bat` | Double-click launcher |

## Compatibility

- Built and tested on: HP EliteBook + Acer HA270 over HDMI.
- Should work with most monitors that support DDC/CI (most made since around 2010), over HDMI, DisplayPort, DVI or VGA.
- Some USB-C docks and adapters block DDC/CI.
- Laptop built-in screens don't use DDC/CI. Their brightness is set through Windows instead.
- Picture modes, Black Boost and Blue Light use Acer-specific codes and are only enabled on Acer monitors.
- Not every monitor supports every setting (for example volume on monitors without speakers). Lumi tells you when something isn't available.
- Party mode is kept short on purpose: monitors save setting changes to their memory, so continuous changes for long periods aren't good for them.
- Avoid switching the input away from the one Lumi is connected through: the screen goes blank and you'd need the monitor's buttons to switch back.
