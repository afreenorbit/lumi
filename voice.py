"""Speaks short confirmations out loud.

Uses ElevenLabs when ELEVENLABS_API_KEY is set, otherwise the built-in Windows voice.
"""
import io
import json
import subprocess
import urllib.request
import wave
import winsound

import config

DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"  # ElevenLabs default voice "Sarah" (works on the free plan)
SAMPLE_RATE = 22050


def speak(text):
    """Say text out loud. Blocks until finished, so call it from a worker thread."""
    if not text:
        return "skipped"
    if config.get("ELEVENLABS_API_KEY"):
        try:
            _speak_elevenlabs(text)
            return "ElevenLabs"
        except Exception as e:
            print(f"ElevenLabs failed, using Windows voice: {e}")
    _speak_windows(text)
    return "Windows voice"


def _speak_elevenlabs(text):
    voice = config.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE
    request = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_{SAMPLE_RATE}",
        data=json.dumps({"text": text, "model_id": "eleven_flash_v2_5"}).encode(),
        headers={"xi-api-key": config.get("ELEVENLABS_API_KEY"), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        pcm = response.read()
    # Wrap the raw 16-bit mono audio in a WAV header so Windows can play it
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY)


def _speak_windows(text):
    safe = text.replace("'", "''")
    cmd = f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{safe}')"
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True)
