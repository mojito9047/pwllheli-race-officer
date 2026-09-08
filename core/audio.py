"""Central audio: queued VHF/speaker announcements spoken on the hut PC.

Extracted verbatim from app.py. A daemon worker drains AUDIO_QUEUE and speaks
each announcement with the platform TTS engine (Windows SAPI via PowerShell,
espeak/say elsewhere), so countdown calls come from the server clock rather
than a browser tab. Start-sequence audio for a race can be purged when its
start time is edited. Rates come from core.settings.race_console_config.
"""
from __future__ import annotations

import platform
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from core.eventlog import log_event
from core.settings import race_console_config

# Central audio state.  Audio is generated on the server/race-office PC so the
# VHF feed does not depend on which browser page the RO has open.  The worker
# prefers pyttsx3/SAPI but falls back to OS command-line speech tools where
# available.
AUDIO_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue()
AUDIO_RUNTIME_STATE: Dict[str, Any] = {
    "started": False,
    "last_status": {"ok": False, "enabled": False, "message": "Central audio worker has not started yet."},
    "last_spoken_at": None,
}
AUDIO_LOCK = threading.Lock()


def central_audio_status() -> Dict[str, Any]:
    """Return current central-audio worker status for Settings."""
    with AUDIO_LOCK:
        status = dict(AUDIO_RUNTIME_STATE.get("last_status") or {})
        status["started"] = bool(AUDIO_RUNTIME_STATE.get("started"))
        status["last_spoken_at"] = AUDIO_RUNTIME_STATE.get("last_spoken_at")
    status.setdefault("enabled", race_console_config().get("start_automation_audio_enabled"))
    return status


def speak_text_locally(text: str, rate: int) -> Dict[str, Any]:
    """Speak text on the server PC using pyttsx3 or a platform fallback.

    The audio comes from the machine running the app, which should be the hut PC
    connected to the VHF/audio interface.  This avoids browser speech output
    coming from a tablet or from whichever page the RO happens to have open.
    """
    text = (text or "").strip()
    if not text:
        return {"ok": True, "message": "No audio text to speak."}
    try:
        import pyttsx3  # type: ignore
        engine = pyttsx3.init()
        engine.setProperty("rate", int(rate))
        engine.say(text)
        engine.runAndWait()
        return {"ok": True, "engine": "pyttsx3", "message": "Spoken with pyttsx3."}
    except Exception as pyttsx3_exc:
        system = platform.system().lower()
        try:
            if system == "windows":
                sapi_rate = max(-10, min(10, int(round((int(rate) - 185) / 18))))
                script = (
                    "Add-Type -AssemblyName System.Speech;"
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                    f"$s.Rate = {sapi_rate};"
                    "$s.Speak([Console]::In.ReadToEnd())"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", script], input=text, text=True, timeout=30, check=True)
                return {"ok": True, "engine": "powershell-sapi", "message": "Spoken with Windows SAPI."}
            if system == "darwin" and shutil.which("say"):
                subprocess.run(["say", "-r", str(int(rate)), text], timeout=30, check=True)
                return {"ok": True, "engine": "say", "message": "Spoken with macOS say."}
            if shutil.which("spd-say"):
                subprocess.run(["spd-say", text], timeout=30, check=True)
                return {"ok": True, "engine": "spd-say", "message": "Spoken with spd-say."}
            if shutil.which("espeak"):
                subprocess.run(["espeak", "-s", str(int(rate)), text], timeout=30, check=True)
                return {"ok": True, "engine": "espeak", "message": "Spoken with espeak."}
            return {"ok": False, "message": f"No speech engine available. pyttsx3 error: {pyttsx3_exc}"}
        except Exception as fallback_exc:
            return {"ok": False, "message": f"Could not play central audio: {fallback_exc}. pyttsx3 error: {pyttsx3_exc}"}


def play_vox_tone(duration_ms: int = 450, frequency: int = 660) -> Dict[str, Any]:
    """Play a short tone on the server PC to key up a VHF's VOX before speech.

    The tone must come out of the same audio device the speech uses (the default
    output feeding the VHF), so the radio is already transmitting when the spoken
    announcement starts and the first words are not clipped.
    """
    system = platform.system().lower()
    try:
        if system == "windows":
            import winsound  # Windows-only; plays through the default audio device (Vista+).
            winsound.Beep(int(frequency), int(duration_ms))
            return {"ok": True, "engine": "winsound", "message": "VOX tone played."}
        if shutil.which("play"):  # sox
            subprocess.run(["play", "-nq", "synth", f"{duration_ms / 1000:.2f}", "sine", str(int(frequency))], timeout=10, check=True)
            return {"ok": True, "engine": "sox", "message": "VOX tone played."}
        return {"ok": False, "message": "No tone player available on this platform."}
    except Exception as exc:
        return {"ok": False, "message": f"Could not play VOX tone: {exc}"}


def queue_central_tone(label: str = "VOX tone", race_id: Optional[int] = None, source: str = "central-start-sequence", details: Optional[Dict[str, Any]] = None) -> None:
    """Queue a short VOX wake-up tone (played on the same device as speech)."""
    cfg = race_console_config()
    if not cfg.get("start_automation_audio_enabled"):
        return
    AUDIO_QUEUE.put({
        "kind": "tone",
        "label": label,
        "race_id": race_id,
        "source": source,
        "details": dict(details or {}),
        "queued_at": datetime.now().isoformat(timespec="seconds"),
    })


def queue_central_audio(text: str, rate: Optional[int] = None, label: str = "Audio announcement", race_id: Optional[int] = None, source: str = "central-audio", details: Optional[Dict[str, Any]] = None) -> None:
    """Queue a central audio announcement for the hut PC speaker/VHF feed."""
    cfg = race_console_config()
    if not cfg.get("start_automation_audio_enabled"):
        return
    AUDIO_QUEUE.put({
        "text": text,
        "rate": int(rate or cfg.get("central_audio_rate", 185)),
        "label": label,
        "race_id": race_id,
        "source": source,
        "details": dict(details or {}),
        "queued_at": datetime.now().isoformat(timespec="seconds"),
    })


def purge_queued_start_sequence_audio(race_id: int) -> int:
    """Remove queued, not-yet-spoken start-sequence audio for a race.

    This is used when the RO changes the first warning-signal time. It prevents
    stale announcements from the previous signal plan being spoken after the
    timer has been corrected.
    """
    removed = 0
    try:
        with AUDIO_QUEUE.mutex:  # type: ignore[attr-defined]
            kept = []
            for item in list(AUDIO_QUEUE.queue):  # type: ignore[attr-defined]
                if int(item.get("race_id") or -1) == int(race_id) and str(item.get("source") or "") == "central-start-sequence":
                    removed += 1
                else:
                    kept.append(item)
            if removed:
                AUDIO_QUEUE.queue.clear()  # type: ignore[attr-defined]
                AUDIO_QUEUE.queue.extend(kept)  # type: ignore[attr-defined]
                if hasattr(AUDIO_QUEUE, "unfinished_tasks"):
                    AUDIO_QUEUE.unfinished_tasks = max(0, AUDIO_QUEUE.unfinished_tasks - removed)  # type: ignore[attr-defined]
    except Exception:
        return 0
    return removed


def central_audio_worker_loop() -> None:
    """Sequentially play queued central audio announcements."""
    while True:
        item = AUDIO_QUEUE.get()
        try:
            if str(item.get("kind") or "") == "tone":
                result = play_vox_tone()
            else:
                result = speak_text_locally(str(item.get("text") or ""), int(item.get("rate") or race_console_config().get("central_audio_rate", 185)))
            with AUDIO_LOCK:
                AUDIO_RUNTIME_STATE["last_status"] = {
                    "ok": bool(result.get("ok")),
                    "enabled": race_console_config().get("start_automation_audio_enabled"),
                    "message": f"{item.get('label')}: {result.get('message')}",
                    "engine": result.get("engine"),
                }
                AUDIO_RUNTIME_STATE["last_spoken_at"] = datetime.now().isoformat(timespec="seconds")
        except Exception as exc:
            with AUDIO_LOCK:
                AUDIO_RUNTIME_STATE["last_status"] = {"ok": False, "enabled": True, "message": f"Central audio worker error: {exc}"}
        finally:
            AUDIO_QUEUE.task_done()


def start_central_audio_worker() -> None:
    """Start the central audio worker thread if needed."""
    with AUDIO_LOCK:
        if AUDIO_RUNTIME_STATE.get("started"):
            return
        AUDIO_RUNTIME_STATE["started"] = True
        AUDIO_RUNTIME_STATE["last_status"] = {"ok": True, "enabled": race_console_config().get("start_automation_audio_enabled"), "message": "Central audio worker is running."}
    thread = threading.Thread(target=central_audio_worker_loop, name="central-audio-worker", daemon=True)
    thread.start()
