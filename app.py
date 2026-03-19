#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Smart Reader v3.4 — Accessible Edition
---------------------------------------
Keyboard: 1 Capture, 2 Retry, 3 Speak, 4 Summarize, 5 Exit, Enter Skip
Voice:    photo, start, summary, back, again, light, guide, stop, quit, help
"""

from __future__ import annotations
import os
import sys
import time
import queue
import json
import threading
import tempfile
import traceback
import random
import requests
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional
import tkinter as tk

if getattr(sys, 'frozen', False):
    os.environ["PYTHONUTF8"] = "1"

# =============================================================================
# INSTANT UI & AUDIO WAKE-UP
# =============================================================================
# 1. Create ONE global window instantly to prevent the blue spinning circle
ROOT = tk.Tk()
ROOT.title("Smart Reader Loading")
ROOT.configure(bg="black")
ROOT.attributes("-fullscreen", True)
SPLASH_LABEL = tk.Label(ROOT, text="LOADING SMART READER...", font=("Segoe UI", 36, "bold"), fg="white", bg="black")
SPLASH_LABEL.pack(expand=True)
ROOT.update() 

# 2. Fast import for instant voice feedback
import pyttsx3
try:
    import winsound # Native Windows library, loads instantly
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

# This event tells the UI and the beeper when the heavy lifting is done
SYSTEM_FULLY_LOADED = threading.Event()

def instant_welcome():
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 130)
        voices = engine.getProperty('voices')
        selected_voice = None
        for v in voices:
            log_info(f"System Voice Found: {v.name}")
        for v in voices:
            search_string = (v.name + v.id).lower()
            if "heera" in search_string or "zira" in search_string or "hazel" in search_string or "female" in search_string:
                selected_voice = v.id
                break
        if not selected_voice and len(voices) > 1:
            selected_voice = voices[1].id
        if selected_voice:
            engine.setProperty('voice', selected_voice)
        engine.say("Welcome to Smart Reader. Please wait a moment while I power up.")
        engine.runAndWait()
    except Exception as e:
        log_err(f"Startup voice error: {e}")

    # ✅ FIXED: Beep only until libraries are ready (5-10s), NOT until Vosk loads (30-40s)
    while not LIBRARIES_LOADED.is_set():
        if HAS_WINSOUND:
            winsound.Beep(500, 100)
        time.sleep(1)

# Start the greeting and beeping immediately
threading.Thread(target=instant_welcome, daemon=True).start()
# =============================================================================
# ASYNCHRONOUS LIBRARY LOADER (THE 2-MINUTE FIX)
# =============================================================================
LIBRARIES_LOADED = threading.Event()

def load_heavy_libraries():
    global cv2, np, Image, ImageTk, webrtcvad, pygame, pytesseract, sr, gTTS, detect, fuzz
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageTk
        import webrtcvad
        import pygame
        import pytesseract
        import speech_recognition as sr
        from gtts import gTTS
        from langdetect import detect
        from thefuzz import fuzz
        LIBRARIES_LOADED.set()
        # ✅ FIXED: Signal UI to show camera NOW — don't wait for Vosk
        SYSTEM_FULLY_LOADED.set()
    except Exception as e:
        print(f"Error loading libraries: {e}")

# Push the heavy lifting to the background!
threading.Thread(target=load_heavy_libraries, daemon=True).start()

try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except Exception:
    PYTTSX3_AVAILABLE = False


# =============================================================================
#                                  GLOBALS
# =============================================================================

SPEECH_LOCK        = threading.RLock()
SPEECH_ACTIVE_EVENT = threading.Event()
TRAINING_MODE      = threading.Event()
COMMAND_ACTIVE_EVENT = threading.Event()
COMMAND_LOCK       = threading.Lock()
APP_READY_EVENT    = threading.Event()
VOSK_IGNORE_EVENT  = threading.Event()

AUTO_CAPTURE_ENABLED  = True
STEADY_TIME_REQUIRED  = 8
MOTION_THRESHOLD      = 40

GEMINI_AVAILABLE  = False
SUMMARIZER_MODEL  = None
OCR_MODEL         = None

CAMERA_INDEX      = 0
SHOW_CAMERA_WINDOW = True

LANG_CODE_TO_NAME = {'en': 'english', 'hi': 'hindi', 'mr': 'marathi'}
LANG_CODE_TO_SPOKEN = {'en': 'English', 'hi': 'Hindi', 'mr': 'Marathi'}

# Hardcoding to 0 stops the script from doing a 10-second search for cameras. 
# If you use an external USB camera, change this to 1.
FORCE_CAMERA_INDEX: Optional[int] = 0 
PREFERRED_CAMERA_NAME = "Logitech"
MAX_PROBE_INDICES = 10

# =============================================================================
# UTILS / LOGGING
# =============================================================================

# Crucial for PyInstaller to find the bundled Vosk model
def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# =============================================================================
#                           UTILS / LOGGING
# =============================================================================

def log_info(msg: str) -> None: print(f"[INFO] {msg}")
def log_warn(msg: str) -> None: print(f"[WARN] {msg}")
def log_err(msg:  str) -> None: print(f"[ERROR] {msg}")
def log_exc(msg:  str) -> None: print(f"[EXC] {msg}\n{traceback.format_exc()}")


# =============================================================================
#                         SOUND FX
# =============================================================================

class SoundFX:
    def __init__(self) -> None:
        self.ready = False
        self._np = None
        self.rate = 22050
        self.tones: Dict[str, pygame.mixer.Sound] = {}
        try:
            if not pygame.get_init():
                pygame.display.init()   
                pygame.mixer.pre_init(22050, -16, 2, 512)  
                pygame.mixer.init()
            import numpy as _np
            self._np = _np
            self.tones = {
                "startup":    self._double_tone(900, 1200, 120),
                "listen":     self._tone(1000, 110),
                "processing": self._tone(600,  140),
                "done":       self._double_tone(400, 500, 80),
                "capture":    self._tone(660,  90),
                "ok":         self._tone(1200, 100),
                "error":      self._tone(220,  160),
                "skip":       self._tone(440,  60),
                "busy":       self._tone(320,  120),
                "loading":    self._ping_tone(), 
                "goodbye":    self._double_tone(659, 523, 150), # NEW: Descending Power-Down chime
            }
            self.ready = True
            log_info("SoundFX ready.")
        except Exception as e:
            log_warn(f"SoundFX disabled: {e}")

    def _tone(self, freq: int, ms: int):
        t = self._np.linspace(0, ms/1000.0, int(self.rate*ms/1000.0), False)
        wave = (self._np.sin(2*self._np.pi*freq*t)*32767).astype(self._np.int16)
        return pygame.sndarray.make_sound(self._np.column_stack((wave, wave)))

    def _double_tone(self, f1: int, f2: int, ms_each: int):
        t = self._np.linspace(0, ms_each/1000.0, int(self.rate*ms_each/1000.0), False)
        w1 = (self._np.sin(2*self._np.pi*f1*t)*32767).astype(self._np.int16)
        w2 = (self._np.sin(2*self._np.pi*f2*t)*32767).astype(self._np.int16)
        wave = np.concatenate([w1, w2])
        return pygame.sndarray.make_sound(self._np.column_stack((wave, wave)))

    def _ping_tone(self):
        # Generates a soft 50ms beep followed by 950ms of silence (1 beep per second)
        t = self._np.linspace(0, 0.05, int(self.rate*0.05), False)
        wave_on = (self._np.sin(2*self._np.pi*500*t)*12000).astype(self._np.int16) # Softer volume
        wave_off = self._np.zeros(int(self.rate*0.95), dtype=self._np.int16)
        wave = np.concatenate([wave_on, wave_off])
        return pygame.sndarray.make_sound(self._np.column_stack((wave, wave)))

    # NEW: Added loops parameter to let sounds repeat infinitely
    def play(self, name: str, loops: int = 0) -> None:
        if self.ready and name in self.tones:
            try: self.tones[name].play(loops=loops)
            except Exception: pass

    # NEW: Allows us to cut a looping sound off
    def stop(self, name: str) -> None:
        if self.ready and name in self.tones:
            try: self.tones[name].stop()
            except Exception: pass

# =============================================================================
#                              UI
# =============================================================================

class SmartReaderUI:
    def __init__(self, speaker) -> None:
        self.speaker = speaker
        global ROOT
        self.root = ROOT
        self.root.title("Smart Reader")

        # Build the camera UI behind the scenes, but DO NOT draw it yet
        self.title_frame = tk.Frame(self.root, bg="#111111", height=70)
        self.title_label = tk.Label(self.title_frame, text="SMART READER", font=("Segoe UI", 28, "bold"), fg="white", bg="#111111")
        
        self.cam_frame = tk.Frame(self.root, bg="black")
        self.label = tk.Label(self.cam_frame, bg="black")

        self.running = True
        self.target_fps = 12
        self.last_frame_time = 0
        self.first_frame_shown = False # Tracks when it's safe to drop the loading screen

        self.root.focus_force()
        self.root.bind("<Escape>", self.exit_fullscreen)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind_all("1", lambda e: self._emit_key("capture"))
        self.root.bind_all("2", lambda e: self._emit_key("retry"))
        self.root.bind_all("3", lambda e: self._emit_key("speak"))
        self.root.bind_all("4", lambda e: self._emit_key("summarize"))
        self.root.bind_all("5", lambda e: self._emit_key("exit"))
        self.root.bind_all("6", lambda e: self._emit_key("audiobook")) # NEW
        self.root.bind_all("7", lambda e: self._emit_key("story"))  # NEW
        self.root.bind_all("<space>", lambda e: self._emit_key("hardreset"))
    def exit_fullscreen(self, event=None) -> None:
        self.root.attributes("-fullscreen", False)

    def close(self) -> None:
        self.running = False
        self.root.destroy()

    def update_frame(self, frame: np.ndarray) -> None:
        # ✅ FIXED: Show camera as soon as cv2/PIL are ready, not waiting for Vosk
        if not LIBRARIES_LOADED.is_set():
            self.root.update()
            return

        # 2. The exact millisecond everything is ready, swap the UI!
        if not self.first_frame_shown:
            global SPLASH_LABEL
            if SPLASH_LABEL:
                try: 
                    SPLASH_LABEL.destroy()
                except Exception: 
                    pass
                SPLASH_LABEL = None
            
            self.title_frame.pack(side="top", fill="x")
            self.title_label.pack(pady=10)
            self.cam_frame.pack(side="top", fill="both", expand=True)
            self.label.pack(fill="both", expand=True)
            self.first_frame_shown = True

        # 3. Standard Camera Update
        now = time.time()
        if now - self.last_frame_time < 1/self.target_fps:
            return
        self.last_frame_time = now
        
        h, w = frame.shape[:2]
        win_w = max(1, self.root.winfo_width())
        win_h = max(1, self.root.winfo_height())
        if win_w < 50 or win_h < 50: return
        
        scale = min(win_w/w, win_h/h)
        if scale <= 0: return
        
        frame = cv2.resize(frame, (max(1, int(w*scale)), max(1, int(h*scale))))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        imgtk = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.label.imgtk = imgtk
        self.label.configure(image=imgtk)
        
        self.root.update_idletasks()
        self.root.update()

    def _emit_key(self, action: str) -> None:
        try:
            if hasattr(self.speaker, "app") and self.speaker.app:
                self.speaker.app.key_queue.put(action)
        except Exception:
            pass
        print(f"[KEYBOARD] {action}")


# =============================================================================
#                              SPEECH MANAGER
# =============================================================================

class SpeechManager:
    def __init__(self, sfx: Optional[SoundFX] = None) -> None:
        self.enabled = True
        self.queue: queue.Queue[tuple] = queue.Queue()
        self._priority_queue: queue.Queue[tuple] = queue.Queue()
        self.current_file: Optional[str] = None
        self.cache: Dict[str, str] = {}
        self.alive = True
        self.sfx = sfx
        self.offline_engine = None
        self.app = None

        try:
            if not pygame.get_init():       pygame.init()
            if not pygame.mixer.get_init(): pygame.mixer.init()
        except Exception as e:
            log_warn(f"[Speech] Disabled audio: {e}")
            self.enabled = False

        # We set this to None for now. The worker thread will initialize it safely!
        self.offline_engine = None

        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        with SPEECH_LOCK:
            self.alive = False
            try:
                if self.enabled:
                    pygame.mixer.music.stop()
                    if hasattr(pygame.mixer.music, "unload"):
                        pygame.mixer.music.unload()
            except Exception:
                pass
        while not self.queue.empty():
            try: self.queue.get_nowait()
            except Exception: break

    def cleanup_cache(self) -> None:
        for fp in list(self.cache.values()):
            try:
                if fp and os.path.exists(fp): os.remove(fp)
            except Exception: pass
        self.cache.clear()
        if self.current_file and os.path.exists(self.current_file):
            try: os.remove(self.current_file)
            except Exception: pass
        self.current_file = None

    def interrupt(self) -> None:
        self._interrupt_current()

    def say(self, text: str, lang: str = "en", interrupt: bool = False,
            cacheable: bool = True, mute_mic: bool = True, offline_ok: bool = True, force_offline: bool = False) -> None:
        if interrupt:
            self._interrupt_current()
        if not self.enabled and not self.offline_engine:
            return
        if not text or not text.strip():
            return
            
        # Added force_offline to the payload
        payload = ("SAY", text.strip(), lang, bool(interrupt),
                   bool(cacheable), bool(mute_mic), bool(offline_ok), bool(force_offline))
                   
        if interrupt:
            self._priority_queue.put(payload)
        else:
            self.queue.put(payload)

    def _interrupt_current(self) -> None:
        if self.offline_engine:
            try: self.offline_engine.stop()
            except Exception: pass
        if not self.enabled:
            return
        with SPEECH_LOCK:
            try:
                if pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
                    if hasattr(pygame.mixer.music, "unload"):
                        pygame.mixer.music.unload()
            except Exception:
                pass

    def _ensure_tts_file(self, text: str, lang: str, cacheable: bool) -> Optional[str]:
        key = f"{lang}|{text}"
        if cacheable and key in self.cache and os.path.exists(self.cache[key]):
            return self.cache[key]
        tmp = tempfile.NamedTemporaryFile(prefix="sr_tts_", suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()
        for attempt in range(2):
            try:
                # ADDED: tld='co.uk' changes the Google voice to a softer British accent.
                # You can change 'co.uk' to 'co.in' if you prefer an Indian English accent.
                gTTS(text=text, lang=lang, tld='co.uk', slow=False).save(tmp_path)
                if cacheable:
                    self.cache[key] = tmp_path
                return tmp_path
            except Exception as e:
                log_warn(f"[Speech] TTS error (try {attempt+1}): {e}")
                time.sleep(0.3)
        try: os.remove(tmp_path)
        except Exception: pass
        return None

    def _play_file_blocking(self, filepath: str, mute_mic: bool) -> None:
        if not self.enabled or not filepath or not os.path.exists(filepath):
            return
        if mute_mic:
            SPEECH_ACTIVE_EVENT.set()
        # Set VOSK_IGNORE at start of playback so Vosk is deaf
        # from the very first word — not just after playback ends
        VOSK_IGNORE_EVENT.set()
        with SPEECH_LOCK:
            try:
                pygame.mixer.music.load(filepath)
                pygame.mixer.music.play()
                self.current_file = filepath
            except Exception as e:
                log_warn(f"[Speech] Playback error: {e}")
                if mute_mic: SPEECH_ACTIVE_EVENT.clear()
                VOSK_IGNORE_EVENT.clear()
                return
        start = time.time()
        while True:
            with SPEECH_LOCK:
                busy = pygame.mixer.music.get_busy() if self.enabled else False
            if not busy: break
            if time.time() - start > 120:
                try: pygame.mixer.music.stop()
                except Exception: pass
                break
            time.sleep(0.05)
        if filepath not in self.cache.values():
            try: os.remove(filepath)
            except Exception: pass
        self.current_file = None
        if mute_mic:
            SPEECH_ACTIVE_EVENT.clear()
            SmartReaderApp._speech_cleared_time = time.time()
            # Short echo guard — 1.0s is enough to kill speaker resonance
            # without blocking user who wants to speak immediately after TTS
            # DO NOT increase this — it was 2.0s and that blocked real user speech
            def _delayed_vosk_unblock():
                time.sleep(0.2)
                VOSK_IGNORE_EVENT.clear()
                log_info("Vosk echo guard lifted — mic fully open.")
            threading.Thread(target=_delayed_vosk_unblock, daemon=True).start()

    def _speak_offline_blocking(self, text: str, mute_mic: bool) -> None:
        if not self.offline_engine: return
        
        if mute_mic: 
            SPEECH_ACTIVE_EVENT.set()
            VOSK_IGNORE_EVENT.set() 
            
        try:
            # Safely read the page using the thread-locked engine
            self.offline_engine.say(text)
            self.offline_engine.runAndWait()
            
        except Exception as e:
            log_warn(f"Offline speech failed: {e}")
        finally:
            if mute_mic: 
                SPEECH_ACTIVE_EVENT.clear()
                
                # CRITICAL: Wait 1 second before un-deafening Vosk, 
                # but ONLY if the next page hasn't already started!
                def _delayed_vosk_unblock():
                    time.sleep(1.0)
                    if not SPEECH_ACTIVE_EVENT.is_set():
                        VOSK_IGNORE_EVENT.clear()
                        log_info("Vosk echo guard lifted (offline) — mic fully open.")
                        
                threading.Thread(target=_delayed_vosk_unblock, daemon=True).start()

    def wait_until_done(self):
        while SPEECH_ACTIVE_EVENT.is_set():
            time.sleep(0.1)

    def _run(self) -> None:
        # CRITICAL FIX: Initialize the voice EXACTLY ONCE on the dedicated audio thread
        if PYTTSX3_AVAILABLE:
            try:
                import pyttsx3
                self.offline_engine = pyttsx3.init()
                self.offline_engine.setProperty("rate", 168)
                voices = self.offline_engine.getProperty('voices')
                selected_voice = None
                for v in voices:
                    search_string = (v.name + v.id).lower()
                    if "heera" in search_string or "zira" in search_string or "hazel" in search_string or "female" in search_string:
                        selected_voice = v.id
                        break
                if not selected_voice and len(voices) > 1:
                    selected_voice = voices[1].id
                if selected_voice:
                    self.offline_engine.setProperty('voice', selected_voice)
            except Exception as e:
                log_warn(f"Threaded pyttsx3 init failed: {e}")
        while self.alive:
            try:
                job = self._priority_queue.get_nowait()
            except queue.Empty:
                try:
                    job = self.queue.get(timeout=0.2)
                except queue.Empty:
                    continue
            except Exception:
                continue
            if job[0] == "SAY":
                # Handle the new payload safely
                if len(job) == 8:
                    _, text, lang, interrupt, cacheable, mute_mic, offline_ok, force_offline = job
                else:
                    _, text, lang, interrupt, cacheable, mute_mic, offline_ok = job
                    force_offline = False

                if interrupt: self._interrupt_current()
                
                fp = None
                # Skip Google TTS entirely if force_offline is True
                if self.enabled and not force_offline:
                    fp = self._ensure_tts_file(text, lang, cacheable)
                    
                if fp:
                    self._play_file_blocking(fp, mute_mic=mute_mic)
                elif offline_ok and self.offline_engine:
                    try:
                        SPEECH_ACTIVE_EVENT.set()
                        self._speak_offline_blocking(text, mute_mic=mute_mic)
                    finally:
                        SPEECH_ACTIVE_EVENT.clear()
                else:
                    log_warn("No TTS engine available.")


# =============================================================================
#                             GEMINI SETUP
# =============================================================================

def setup_gemini() -> None:
    global GEMINI_AVAILABLE, SUMMARIZER_MODEL, OCR_MODEL
    
    try:
        import google.generativeai as genai
    except Exception:
        log_info("Gemini SDK not installed.")
        return
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log_warn("GOOGLE_API_KEY not set — Gemini disabled.")
        return
    try:
        genai.configure(api_key=api_key)
        OCR_MODEL_NAME     = "gemini-2.0-flash-lite"
        SUMMARY_MODEL_NAME = "gemini-2.0-flash"
        ocr_config     = genai.GenerationConfig(max_output_tokens=600, temperature=0.0)
        summary_config = genai.GenerationConfig(max_output_tokens=150, temperature=0.0)
        OCR_MODEL        = genai.GenerativeModel(OCR_MODEL_NAME,     generation_config=ocr_config)
        SUMMARIZER_MODEL = genai.GenerativeModel(SUMMARY_MODEL_NAME, generation_config=summary_config)
        GEMINI_AVAILABLE = True
        log_info(f"Gemini ready. OCR={OCR_MODEL_NAME} Summary={SUMMARY_MODEL_NAME}")

        def _prewarm():
            try:
                blank = np.ones((10, 10, 3), dtype=np.uint8) * 255
                _, buf = cv2.imencode(".jpg", blank, [cv2.IMWRITE_JPEG_QUALITY, 50])
                OCR_MODEL.generate_content(["Return empty.",
                    {"mime_type": "image/jpeg", "data": buf.tobytes()}])
                log_info("Gemini pre-warmed.")
            except Exception:
                pass
        threading.Thread(target=_prewarm, daemon=True).start()
    except Exception as e:
        log_warn(f"Gemini setup failed: {e}")
        GEMINI_AVAILABLE = False

# =============================================================================
#                           OCR HELPERS
# =============================================================================

def ocr_with_tesseract(roi_image: np.ndarray) -> Tuple[str, np.ndarray]:
    log_info("Using Tesseract OCR...")
    try:
        gray = cv2.cvtColor(roi_image, cv2.COLOR_BGR2GRAY)
        kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]], dtype=np.float32)
        gray = cv2.filter2D(gray, -1, kernel)
        processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
        text = pytesseract.image_to_string(
            processed, lang="eng",
            config="--oem 3 --psm 6 -c tessedit_do_invert=0"
        ).strip()
        return text, processed
    except Exception as e:
        log_warn(f"Tesseract OCR error: {e}")
        return "", roi_image


def ocr_quality_score(text: str) -> float:
    if not text: return 0.0
    length = len(text)
    alpha  = sum(c.isalpha() for c in text)
    spaces = text.count(" ")
    junk   = sum(c in "|~^_`" for c in text)
    score  = (alpha/length)*0.6 + (spaces/max(1,length))*0.3 - (junk/length)*0.5
    return round(max(0.0, score), 3)


def rotate_image(image: np.ndarray, angle: int) -> np.ndarray:
    if angle == 90:  return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180: return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270: return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


def detect_orientation_osd(image: np.ndarray) -> Optional[int]:
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        osd  = pytesseract.image_to_osd(gray, output_type=pytesseract.Output.DICT)
        conf = float(osd.get("orientation_confidence", 0))
        if conf < 5.0: return None
        return int(osd.get("rotate", 0))
    except Exception:
        return None


def frame_motion(prev_gray: np.ndarray, curr_gray: np.ndarray) -> float:
    # Changed to INTER_LINEAR (much faster) and lower resolution.
    sp = cv2.resize(prev_gray, (80, 60), interpolation=cv2.INTER_LINEAR)
    sc = cv2.resize(curr_gray, (80, 60), interpolation=cv2.INTER_LINEAR)
    diff = cv2.absdiff(sp, sc)
    _, diff = cv2.threshold(diff, 15, 255, cv2.THRESH_BINARY)
    return float(np.mean(diff))


def estimate_brightness(frame: np.ndarray) -> float:
    try:
        # Fast downscale before math. 100x faster processing.
        small = cv2.resize(frame, (64, 64), interpolation=cv2.INTER_LINEAR)
        ycc = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb)
        return float(np.mean(ycc[:, :, 0]))
    except Exception:
        return 100.0 # Safe fallback


# =============================================================================
#                               OCR PIPELINE
# =============================================================================

class OCRPipeline:
    QUOTA_FAIL_THRESHOLD = 2
    QUOTA_BACKOFF_SEC    = 300

    def __init__(self) -> None:
        self._cache: Dict[int, str] = {}
        self._cache_max = 12
        self._gemini_fail_count: int = 0
        self._gemini_blocked_until: float = 0.0

    def process(self, frame: np.ndarray) -> Tuple[str, float, bool]:
        roi = self._prepare(frame)
        if roi is None:
            return "", 0.0, False
        text, used_offline = self._run(roi)
        score = ocr_quality_score(text)
        if score < 0.35:
            text, score, used_offline = self._try_variants(roi, text, score, used_offline)
        return text, score, used_offline

    def summarize(self, text: str, lang_name: str = "english") -> str:
        if not self._gemini_ok(): return ""
        text = (text or "").strip()
        if not text: return "No text to summarize."
        if len(text) > 1500: text = text[:1500] + "..."
        prompt = f"In 2 sentences in {lang_name}, summarize for a blind listener: {text}"
        try:
            response = SUMMARIZER_MODEL.generate_content(prompt)
            result = (response.text or "").strip()
            self._gemini_fail_count = 0
            return result
        except Exception as e:
            log_warn(f"Gemini summarize error: {e}")
            self._record_gemini_fail()
            return ""

    def _prepare(self, frame: np.ndarray) -> Optional[np.ndarray]:
        if frame is None or frame.size == 0: return None
        h, w = frame.shape[:2]
        if w > 900:
            # Swapped to INTER_LINEAR. Roughly 40% faster on large images.
            frame = cv2.resize(frame, None, fx=900/w, fy=900/w, interpolation=cv2.INTER_LINEAR)
        return frame

    def _run(self, roi: np.ndarray) -> Tuple[str, bool]:
        text = self._gemini_ocr(roi)
        if text: return text, False
        text, _ = ocr_with_tesseract(roi)
        return text, True

    def _try_variants(self, roi, best_text, best_score, best_offline):
        for _name, img in [("rot180", rotate_image(roi, 180)), ("mirror", cv2.flip(roi, 1))]:
            t, offline = self._run(img)
            s = ocr_quality_score(t)
            if s > best_score:
                best_text, best_score, best_offline = t, s, offline
            if best_score > 0.6: break
        return best_text, best_score, best_offline

    def _gemini_ok(self) -> bool:
        if not GEMINI_AVAILABLE: return False
        if time.time() < self._gemini_blocked_until:
            log_info(f"Gemini backoff: {int(self._gemini_blocked_until - time.time())}s remaining.")
            return False
        return True

    def _record_gemini_fail(self) -> None:
        self._gemini_fail_count += 1
        if self._gemini_fail_count >= self.QUOTA_FAIL_THRESHOLD:
            self._gemini_blocked_until = time.time() + self.QUOTA_BACKOFF_SEC
            log_warn(f"Gemini quota hit. Pausing for {self.QUOTA_BACKOFF_SEC}s.")
            self._gemini_fail_count = 0

    def _gemini_ocr(self, roi: np.ndarray) -> Optional[str]:
        if not self._gemini_ok(): return None
        log_info("Using Gemini OCR...")
        try:
            img_hash = hash(roi.tobytes())
            if img_hash in self._cache:
                log_info("OCR cache hit.")
                return self._cache[img_hash]
            h, w = roi.shape[:2]
            if w > 480:
                # Swapped to INTER_LINEAR
                roi = cv2.resize(roi, None, fx=480/w, fy=480/w, interpolation=cv2.INTER_LINEAR)
            _, buf = cv2.imencode(".jpg", roi, [cv2.IMWRITE_JPEG_QUALITY, 72])
            response = OCR_MODEL.generate_content([
                "Extract all text exactly as written. Return text only, no commentary.",
                {"mime_type": "image/jpeg", "data": buf.tobytes()}
            ])
            text = (response.text or "").strip()
            if text:
                self._cache[img_hash] = text
                if len(self._cache) > self._cache_max:
                    self._cache.pop(next(iter(self._cache)))
            self._gemini_fail_count = 0
            return text or None
        except Exception as e:
            log_warn(f"Gemini OCR failed: {e}")
            self._record_gemini_fail()
            return None


# =============================================================================
#                            CAMERA MANAGER
# =============================================================================

class CameraManager:
    MOTION_CHECK_INTERVAL = 3

    def __init__(self) -> None:
        self.cap: Optional[cv2.VideoCapture] = None
        self.camera_index: int = 0
        self.prev_gray: Optional[np.ndarray] = None
        self._frame_counter: int = 0
        self._last_motion: float = 0.0
        
        # Threading variables
        self.grabbed: bool = False
        self.frame: Optional[np.ndarray] = None
        self.stopped: bool = False

    def start_thread(self):
        threading.Thread(target=self._update, daemon=True).start()

    def _update(self):
        # Constantly pull frames in the background so the main loop never waits
        while not self.stopped:
            if self.cap is not None and self.cap.isOpened():
                self.grabbed, self.frame = self.cap.read()
            else:
                time.sleep(0.01)

    def open(self, index: int) -> bool:
        self.camera_index = index
        self.cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(index, cv2.CAP_ANY)

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1120)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 630)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        
        self.grabbed, self.frame = self.cap.read()
        self.stopped = False
        self.start_thread() # Kick off the background grabber
        return self.cap.isOpened()

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        # Instantly return the latest frame from memory
        return self.grabbed, self.frame
        
    def motion_score(self, frame: np.ndarray) -> float:
        self._frame_counter += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._frame_counter % self.MOTION_CHECK_INTERVAL == 0:
            if self.prev_gray is not None:
                self._last_motion = frame_motion(self.prev_gray, gray)
            self.prev_gray = gray
        return self._last_motion

    def reset_motion(self) -> None:
        self.prev_gray = None
        self._last_motion = 0.0
        self._frame_counter = 0

    def release(self) -> None:
        self.stopped = True # Kill the thread
        if self.cap is not None:
            try: self.cap.release()
            except Exception: pass
            self.cap = None


# =============================================================================
#                       TRAINING MODE
# =============================================================================

@dataclass
class TrainingConfig:
    step_delay_sec: float = 1.5
    narration_mutes_mic: bool = True   # ✅ FIXED: Vosk goes deaf during narration
    min_restart_gap_sec: float = 2.0


class TrainingManager:
    def __init__(self, speaker: SpeechManager, sfx: Optional[SoundFX], cfg: TrainingConfig):
        self.speaker = speaker
        self.cfg = cfg
        self.sfx = sfx
        self._thread: Optional[threading.Thread] = None
        self._last_start_ts: float = 0.0

    def start(self) -> None:
        now = time.time()
        if now - self._last_start_ts < self.cfg.min_restart_gap_sec: return
        if self.is_running: return
        TRAINING_MODE.set()
        self._last_start_ts = now
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        TRAINING_MODE.clear()

    @property
    def is_running(self) -> bool:
        return TRAINING_MODE.is_set()

    def _run(self) -> None:
        # ✅ FIXED: Deaf Vosk for entire training so it cannot hear its own speech as commands
        VOSK_IGNORE_EVENT.set()
        COMMAND_ACTIVE_EVENT.set()

        steps = [
            "Welcome to Smart Reader training mode.",
            "You can control Smart Reader using either voice commands or keyboard buttons.",
            "To capture a page, say Photo, or press the number 1 key.",
            "To hear the text on the captured page, say Start, or press the number 3 key.",
            "To hear a short summary of the page, say Summary, or press the number 4 key.",
            "If you want to return to the camera and capture again, say Back, or press the number 2 key.",
            "If you want to hear the text again, say Again.",
            "To check lighting conditions for better reading, say Light.",
            "To listen to an audiobook, press the number 6 key.",
            "To listen to a short story, press the number 7 key.",
            "To skip or stop anything, press the Spacebar.",
            "To hear the list of commands again, say Help.",
            "To exit Smart Reader completely, say Quit, or press the number 5 key.",
            "Training is now complete. You can start using Smart Reader.",
        ]
        for line in steps:
            if not self.is_running:
                break
            self.speaker.say(line, mute_mic=True)
            # Wait for speech to actually start before timing
            time.sleep(0.3)
            end = time.time() + max(1.5, len(line.split()) / 2.8) + self.cfg.step_delay_sec
            while time.time() < end:
                if not self.is_running:
                    break
                time.sleep(0.1)

        if self.is_running:
            self.speaker.say("Training complete. You are now ready to use Smart Reader.", mute_mic=True)

        TRAINING_MODE.clear()
        # ✅ Small delay then fully reopen mic
        def _reopen():
            time.sleep(1.5)
            COMMAND_ACTIVE_EVENT.clear()
            VOSK_IGNORE_EVENT.clear()
            log_info("Training ended — mic fully reopened.")
        threading.Thread(target=_reopen, daemon=True).start()

class ArchiveClient:

    def __init__(self):

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": "SmartReader/1.0"
        })

        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504]
        )

        adapter = HTTPAdapter(max_retries=retry)

        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get_random_audio(self):

        url = "https://archive.org/advancedsearch.php"

        params = {
            "q": "collection:librivoxaudio AND mediatype:audio",
            "fl[]": ["identifier", "title"],
            "rows": 50,
            "page": random.randint(1, 6),
            "output": "json"
        }

        try:
            r = self.session.get(url, params=params, timeout=20)
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Archive request failed: {e}")
            return None, None

        # Validate response
        if r.status_code != 200 or not r.text.strip():
            print("[WARN] Archive returned empty response")
            return None, None

        try:
            data = r.json()
        except Exception as e:
            print(f"[WARN] JSON parse failed: {e}")
            return None, None

        docs = data.get("response", {}).get("docs", [])

        random.shuffle(docs)

        for book in docs:

            identifier = book.get("identifier")
            title = book.get("title", "Story")

            if not identifier:
                continue

            try:

                files_url = f"https://archive.org/metadata/{identifier}"
                try:
                    r2 = self.session.get(files_url, timeout=20)
                except requests.exceptions.RequestException:
                    continue

                if r2.status_code != 200 or not r2.text.strip():
                    continue

                try:
                    files_data = r2.json()
                except:
                    continue

                files = files_data.get("files", [])

                valid_mp3 = []

                for f in files:

                    name = f.get("name", "")
                    size = int(f.get("size", 0))

                    # Only real audiobook files
                    if name.endswith(".mp3") and size > 3000000:
                        valid_mp3.append(name)

                if not valid_mp3:
                    continue

                chosen = random.choice(valid_mp3)

                audio_url = f"https://archive.org/download/{identifier}/{chosen}"

                return title, audio_url

            except Exception:
                continue

        return None, None

class StoryMode:

    def __init__(self, speaker, sfx):
        self.speaker = speaker
        self.sfx = sfx
        self.client = ArchiveClient()
        self.active = False

    @property
    def is_active(self):
        return self.active

    def start(self, genre="librivoxaudio"):

        if self.active:
            self.speaker.say("A story is already playing.")
            return

        threading.Thread(target=self._run, args=(genre,), daemon=True).start()

    def stop(self):

        self.active = False

        try:
            pygame.mixer.music.stop()
            if hasattr(pygame.mixer.music, "unload"):
                pygame.mixer.music.unload()
        except:
            pass

        COMMAND_ACTIVE_EVENT.clear()
        SPEECH_ACTIVE_EVENT.clear()

        def unlock():
            time.sleep(0.2)
            VOSK_IGNORE_EVENT.clear()
            print("[INFO] Story stopped → system reset")

        threading.Thread(target=unlock, daemon=True).start()

    def _run(self, genre):

        self.active = True
        COMMAND_ACTIVE_EVENT.set()
        VOSK_IGNORE_EVENT.set()
        SPEECH_ACTIVE_EVENT.set()

        try:
            self.speaker.say("Finding a Audiobook.", mute_mic=True)
            self.speaker.wait_until_done()
            # keep mic disabled while audiobook plays
            VOSK_IGNORE_EVENT.set()
            SPEECH_ACTIVE_EVENT.set()
            # ✅ GET AUDIO
            result = self.client.get_random_audio()
            title, url = result if result else (None, None)

            if not url:
                self.speaker.say("Searching another story.", mute_mic=True)
                result = self.client.get_random_audio()
                title, url = result if result else (None, None)

            if not title or not url:
                self.speaker.say("Could not find a story. Please try again.", mute_mic=True)
                return   # ✅ DEFINE url HERE

            if not url:
                self.speaker.say("No audio found.", mute_mic=True)
                return

            self.speaker.say(f"Playing audiobook: {title}", mute_mic=True)
            self.speaker.wait_until_done()

            if not pygame.mixer.get_init():
                pygame.mixer.init()

            import tempfile
            import os

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            tmp_path = tmp.name

            r = requests.get(url, stream=True, timeout=30)

            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    tmp.write(chunk)
            tmp.flush()
            tmp.close()

            if not os.path.exists(tmp_path):
                raise RuntimeError("Audio download failed")

            # Start playing immediately
            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()
            # Disable mic during audiobook
            VOSK_IGNORE_EVENT.set()
            SPEECH_ACTIVE_EVENT.set()

            # Continue downloading in background
            def continue_download():
                for chunk in r.iter_content(chunk_size=8192):
                    if not self.active:
                        break
                    if chunk:
                        tmp.write(chunk)
                tmp.flush()
                tmp.close()
                r.close()

            threading.Thread(target=continue_download, daemon=True).start()

            while pygame.mixer.music.get_busy():

                if not self.active:
                    pygame.mixer.music.stop()
                    break

                time.sleep(0.2)

            # cleanup temp file
            try:
                os.remove(tmp_path)
            except:
                pass

        finally:
            self.active = False
            COMMAND_ACTIVE_EVENT.clear()
            SPEECH_ACTIVE_EVENT.clear()

            def unlock_mic():
                time.sleep(0.5)   # small delay to avoid echo trigger
                VOSK_IGNORE_EVENT.clear()
                print("[INFO] Mic re-enabled after story")

            threading.Thread(target=unlock_mic, daemon=True).start()
# =============================================================================
#                               VOICE LISTENER
# =============================================================================

@dataclass
class VoiceConfig:
    phrase_time_limit: int = 4
    timeout: int = 3
    similarity_threshold: int = 75


def listen_for_commands(
    command_queue: queue.Queue,
    stop_event: threading.Event,
    sfx: SoundFX,
    speaker: SpeechManager,
    cfg: VoiceConfig,
) -> None:

    COMMAND_SYNONYMS: Dict[str, List[str]] = {
        "capture":    ["photo", "capture", "pakad"],
        "retry":      ["back",  "retry",   "wapas"],
        "speak":      ["start", "speak",   "bolo"],
        "summarize":  ["summary", "summarize", "saar"],
        "training":   ["guide", "training", "seekh"],
        "help":       ["help",  "madad"],
        "repeat":     ["again", "repeat",  "dobara"],
        "brightness": ["light", "brightness", "roshni"],
        "exit":       ["quit",  "close",   "exit", "bahar"],
        "skip":       ["skip",    "rok"],
        "audiobook":  ["book", "audiobook", "audiostory"],
        "story":      ["story", "quick story", "kahani", "short story"],
    }
    CANON_MAP: Dict[str, str] = {}
    for canon, words in COMMAND_SYNONYMS.items():
        for w in words:
            CANON_MAP[w] = canon

    def match_command(text: str) -> Optional[str]:
        text_lower = text.lower().strip()
        if not text_lower: return None
        for w in text_lower.split():
            if w in CANON_MAP:
                log_info(f"Exact: '{w}' -> '{CANON_MAP[w]}'")
                return CANON_MAP[w]
        for w in text_lower.split():
            if len(w) < 3: continue
            prefix = w[:3]
            for cmd_word, canon in CANON_MAP.items():
                if len(cmd_word) >= 3 and cmd_word[:3] == prefix:
                    if fuzz.ratio(w, cmd_word) >= 80:
                        log_info(f"Fuzzy: '{w}' ~ '{cmd_word}' -> '{canon}'")
                        return canon
        return None

    try:
        from vosk import Model, KaldiRecognizer
        import pyaudio as _pyaudio
        import json as _json
        import audioop as _audioop

        model_path = resource_path("vosk-model-small-en-in-0.4")
        if not model_path:
            raise FileNotFoundError("Vosk model not found")

        log_info(f"Loading Vosk model: {model_path}")
        model = Model(model_path)

        pa = _pyaudio.PyAudio()
        log_info("Scanning audio input devices...")
        mic_index, best_score = None, -1
        for i in range(pa.get_device_count()):
            dev    = pa.get_device_info_by_index(i)
            name   = dev.get("name", "").lower()
            inputs = dev.get("maxInputChannels", 0)
            log_info(f"Device {i}: {name} (inputs={inputs})")
            if inputs > 0:
                s = 0
                if "microphone" in name: s += 5
                if "array"      in name: s += 3
                if "capture"    in name: s += 2
                if "stereo mix" in name: s -= 5
                if s > best_score:
                    best_score, mic_index = s, i

        default_device = pa.get_default_input_device_info()
        mic_index   = default_device["index"]
        native_rate = int(default_device["defaultSampleRate"])
        log_info(f"Using mic: {default_device['name']} @ {native_rate}Hz")

        # ── Audio frame sizing ─────────────────────────────────────────────
        # webrtcvad requires EXACTLY 10/20/30ms at target rate
        # We use 30ms: 480 samples at 16kHz = 960 bytes
        # native_frame_size = how many native-rate samples = 30ms of audio
        # e.g. at 44100Hz: 44100 * 30/1000 = 1323 native samples
        VOSK_TARGET_RATE  = 16000
        # Faster realtime frame processing
        FRAME_MS          = 20
        VOSK_FRAME_BYTES  = int(VOSK_TARGET_RATE * FRAME_MS / 1000) * 2
        native_frame_size = int(native_rate * FRAME_MS / 1000)

        # frames_per_buffer MUST be >= native_frame_size to avoid overflow
        # Use next power of 2 above native_frame_size for clean PyAudio operation
        buf_size = native_frame_size
        # buf_size is now 2048 at 44100Hz — stable, low enough latency (~46ms)

        log_info(f"Audio: native={native_rate}Hz frame={native_frame_size} buf={buf_size}")

        stream = pa.open(
            format=_pyaudio.paInt16,
            channels=1,
            rate=native_rate,
            input=True,
            frames_per_buffer=buf_size,
            input_device_index=mic_index,
        )
        stream.start_stream()
        log_info("Vosk stream opened successfully.")

        # Drain stale audio accumulated during model load
        drain_end = time.time() + 0.8
        while time.time() < drain_end:
            try: stream.read(native_frame_size, exception_on_overflow=False)
            except Exception: pass

        PARTIAL_CONFIRM_COUNT = 999
        _partial_cmd:   Optional[str] = None
        _partial_count: int = 0

        grammar = json.dumps([
            "photo", "capture", "start", "speak", "summary", "summarize",
            "back", "retry", "again", "repeat", "guide", "training", "help",
            "light", "brightness", "quit", "exit", "stop", "skip",
            "story", "audiobook","book"
        ])

        rec = KaldiRecognizer(model, VOSK_TARGET_RATE, grammar)
        rec.SetWords(True)
        rec.SetPartialWords(True)
        rec.SetMaxAlternatives(0)

        log_info("Vosk recognizer ready.")
        
        if sfx: 
            sfx.play("ok") # Play the success chime
            
        time.sleep(0.2)
        VOSK_IGNORE_EVENT.set()
        log_info("Vosk listener running.")

        last_dispatch        = 0.0
        COOLDOWN             = 0.5    # Lowered for faster back-to-back commands
        was_blocked          = True   # start blocked — wait for ready msg to finish
        _resample_state      = None
        _last_unblock_time   = 0.0
        POST_SPEECH_MUTE_SEC = 0.2    # Lowered so you can speak almost immediately after TTS finishes
        FLUSH_DURATION_SEC   = 0.1
        _last_health_reset   = time.time()
        HEALTH_RESET_SEC     = 20 * 60

        while not stop_event.is_set():

            # Periodic health reset for long sessions
            now_ts = time.time()
            if (now_ts - _last_health_reset) > HEALTH_RESET_SEC:
                if not SPEECH_ACTIVE_EVENT.is_set() and not COMMAND_ACTIVE_EVENT.is_set():
                    log_info("Health reset — refreshing Vosk recognizer.")
                    try:
                        rec = KaldiRecognizer(model, VOSK_TARGET_RATE, grammar)
                        rec.SetWords(True)
                        rec.SetPartialWords(True)
                        rec.SetMaxAlternatives(0)
                        _partial_cmd, _partial_count = None, 0
                        _resample_state  = None
                        _last_health_reset = now_ts
                        log_info("Health reset complete.")
                    except Exception as e:
                        log_warn(f"Health reset failed: {e}")

            blocked = (SPEECH_ACTIVE_EVENT.is_set()
                       or COMMAND_ACTIVE_EVENT.is_set()
                       or VOSK_IGNORE_EVENT.is_set())

            if not blocked and _last_unblock_time > 0.0:
                if (time.time() - _last_unblock_time) < POST_SPEECH_MUTE_SEC:
                    blocked = True

            if blocked:
                # Drain buffer using SAME frame size as normal reads — no overflow
                try: stream.read(native_frame_size, exception_on_overflow=False)
                except Exception: pass
                if not was_blocked:
                    _last_unblock_time = time.time()
                was_blocked = True
                time.sleep(0.005)
                continue

            if was_blocked:
                was_blocked     = False
                _resample_state = None
                _partial_cmd    = None
                _partial_count  = 0
                # Flush using consistent frame size
                flush_end = time.time() + FLUSH_DURATION_SEC
                while time.time() < flush_end:
                    try: stream.read(native_frame_size, exception_on_overflow=False)
                    except Exception: pass
                #rec.Reset()
                log_info("Mic open — listening.")

            # ── Read exactly one FRAME_MS chunk ───────────────────────────
            # native_frame_size frames at native_rate = FRAME_MS milliseconds
            # After resample: VOSK_FRAME_BYTES bytes at 16kHz
            # frames_per_buffer >= native_frame_size so read never overflows
            try:
                raw_data = stream.read(native_frame_size, exception_on_overflow=False)

                if native_rate != VOSK_TARGET_RATE:
                    data, _resample_state = _audioop.ratecv(
                        raw_data, 2, 1,
                        native_rate, VOSK_TARGET_RATE,
                        _resample_state
                    )
                else:
                    data = raw_data

                # Safety check — resampled data must be exactly VOSK_FRAME_BYTES
                if len(data) < VOSK_FRAME_BYTES:
                    continue

                # REMOVED VAD CHECK: Vosk handles silence automatically. 
                # Dropping frames here breaks the recognizer and causes the "takes 5 tries" bug.
                # vad_data = data[:VOSK_FRAME_BYTES]
                # if not vad.is_speech(vad_data, VOSK_TARGET_RATE):
                #     continue

            except Exception:
                continue

            if rec.AcceptWaveform(data):
                text = _json.loads(rec.Result()).get("text", "").strip()
                _partial_cmd, _partial_count = None, 0
                if text:
                    log_info(f"Vosk heard: '{text}'")
                    now = time.time()
                    if now - last_dispatch >= COOLDOWN:
                        action = match_command(text)
                        if action:
                            # ✅ "skip" always dispatches — even during active command/speech
                            is_priority = (action == "skip")
                            with COMMAND_LOCK:
                                if not COMMAND_ACTIVE_EVENT.is_set() or is_priority:
                                    if not is_priority:
                                        COMMAND_ACTIVE_EVENT.set()
                                    last_dispatch      = now
                                    _last_unblock_time = time.time()
                                    if sfx: sfx.play("processing")
                                    command_queue.put(action)
                                    log_info(f"Dispatched (final): {action}")
                        else:
                            log_info(f"No match: '{text}'")
                    else:
                        log_info("Cooldown — ignored.")
            else:
                p = _json.loads(rec.PartialResult()).get("partial", "").strip()
                if p:
                    log_info(f"Partial: '{p}'")
                    now = time.time()
                    if now - last_dispatch >= COOLDOWN:
                        action = match_command(p)
                        if action:
                            if action == _partial_cmd:
                                _partial_count += 1
                            else:
                                _partial_cmd, _partial_count = action, 1
                            if _partial_count >= PARTIAL_CONFIRM_COUNT:
                                with COMMAND_LOCK:
                                    if not COMMAND_ACTIVE_EVENT.is_set():
                                        COMMAND_ACTIVE_EVENT.set()
                                        last_dispatch      = now
                                        _last_unblock_time = time.time()
                                        _partial_cmd, _partial_count = None, 0
                                        if sfx: sfx.play("processing")
                                        command_queue.put(action)
                                        log_info(f"Dispatched (partial): {action}")
                        else:
                            _partial_cmd, _partial_count = None, 0

        stream.stop_stream()
        stream.close()
        pa.terminate()
        log_info("Vosk listener stopped.")
        return

    except Exception as e:
        log_warn(f"Vosk failed: {e}. Falling back to Google Speech.")

    # ── Google Speech fallback ─────────────────────────────────────────────
    log_info("Using Google Speech fallback.")
    recognizer = sr.Recognizer()
    recognizer.energy_threshold         = 300
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold          = 0.5
    mic = sr.Microphone()
    try:
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)
    except Exception as e:
        log_warn(f"Mic init failed: {e}")
        speaker.say("Microphone unavailable. Use keyboard controls.")
        return

    if sfx: sfx.play("listen")
    speaker.say("Ready. Listening now.")
    last_dispatch = 0.0

    while not stop_event.is_set():
        if SPEECH_ACTIVE_EVENT.is_set() or COMMAND_ACTIVE_EVENT.is_set():
            time.sleep(0.1)
            continue
        try:
            with mic as source:
                audio = recognizer.listen(source, timeout=3, phrase_time_limit=4)
            if SPEECH_ACTIVE_EVENT.is_set() or COMMAND_ACTIVE_EVENT.is_set():
                continue
            text = recognizer.recognize_google(audio, language="en-IN").lower()
            log_info(f"Google heard: '{text}'")
            now = time.time()
            if now - last_dispatch < 1.5: continue
            action = match_command(text)
            if action:
                with COMMAND_LOCK:
                    if not COMMAND_ACTIVE_EVENT.is_set():
                        COMMAND_ACTIVE_EVENT.set()
                        last_dispatch = now
                        if sfx: sfx.play("processing")
                        command_queue.put(action)
                        log_info(f"Dispatched: {action}")
        except sr.WaitTimeoutError: continue
        except sr.UnknownValueError: pass
        except sr.RequestError as e:
            log_warn(f"Speech API error: {e}")
            time.sleep(3)
        except Exception:
            log_exc("Google listener error")


# =============================================================================
#                              CAMERA SELECTION
# =============================================================================

def find_camera_index_by_name(preferred_name: str) -> Optional[int]:
    try:
        from pygrabber.dshow_graph import FilterGraph
        fg = FilterGraph()
        devices = fg.get_input_devices()
        log_info(f"Video devices: {devices}")
        for idx, name in enumerate(devices):
            if preferred_name.lower() in name.lower():
                log_info(f"Preferred camera '{name}' at index {idx}.")
                return idx
    except Exception as e:
        log_warn(f"pygrabber failed: {e}")
    return None


def probe_camera_indices(max_indices: int = MAX_PROBE_INDICES) -> Optional[int]:
    successful: List[int] = []
    for i in range(max_indices):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if not cap or not cap.isOpened():
            try: cap.release()
            except Exception: pass
            continue
        ret, _ = cap.read()
        cap.release()
        if ret:
            successful.append(i)
            log_info(f"Found camera at index: {i}")
    if not successful: return None
    for idx in successful:
        if idx != 0: return idx
    return successful[0]


def select_camera_index() -> int:
    if isinstance(FORCE_CAMERA_INDEX, int):
        return FORCE_CAMERA_INDEX
    idx = find_camera_index_by_name(PREFERRED_CAMERA_NAME)
    if isinstance(idx, int): return idx
    idx = probe_camera_indices()
    if isinstance(idx, int): return idx
    log_warn("Falling back to camera index 0.")
    return 0


# =============================================================================
#                         STARTUP HELPERS
# =============================================================================

def ensure_tesseract_langs(langs=("eng", "hin", "mar")) -> None:
    try:
        tess_exec = pytesseract.pytesseract.tesseract_cmd
        if not tess_exec: return
        tessdir = os.path.join(os.path.dirname(tess_exec), "tessdata")
        missing = [l for l in langs
                   if not os.path.exists(os.path.join(tessdir, f"{l}.traineddata"))]
        if missing:
            log_warn(f"Missing Tesseract traineddata: {missing}")
    except Exception as e:
        log_warn(f"Could not verify Tesseract traineddata: {e}")


def speak_help(speaker: SpeechManager) -> None:
    speaker.say(
        "Available voice commands are: "
        "Say Photo to capture a page. "
        "Say Start to read the captured text. "
        "Say Summary to hear a short summary. "
        "Say Back to return to the live camera. "
        "Say Again to repeat the last text. "
        "Say Light to check brightness. "
        "Say Guide to start training mode. "
        "Say Story to hear a short story. "
        "Say Book or Audio Book to play an audiobook from the internet. "
        "Say Stop or Skip to stop any speech or story. "
        "Say Help to hear this list again. "
        "Say Quit to exit the application. "
        "You can also use keyboard keys. "
        "Press 1 to capture a page. "
        "Press 2 to go back to the camera. "
        "Press 3 to read the text. "
        "Press 4 to hear a summary. "
        "Press 5 to exit Smart Reader. "
        "Press 6 to listen to a short story. "
        "Press 7 to listen to an audiobook. "
        "Press the Spacebar to skip or stop anything instantly."
    )

SHORT_STORIES = [

{
"title": "The Lion and the Mouse",
"story": """
One hot afternoon, a mighty lion was sleeping peacefully in the forest.
The tall trees moved gently with the wind.
Birds were singing softly in the branches.
Suddenly a tiny mouse ran across the lion's huge paw.
The lion woke up with a loud roar.
He quickly caught the mouse under his paw.
The mouse trembled with fear.
“Oh great king,” said the mouse, “please forgive me.”
“If you let me go, I promise I will help you someday.”
The lion laughed loudly.
“You are so small. How could you ever help a lion?”
But the lion was in a good mood that day.
He lifted his paw and allowed the mouse to run away.
The mouse quickly disappeared into the grass.
Days passed and the lion forgot about the tiny mouse.
One evening hunters came to the forest.
They set a strong rope net between the trees.
The lion walked into the trap without noticing it.
The ropes tightened around his powerful body.
The lion roared loudly for help.
His roar echoed across the entire forest.
Far away, the mouse heard the lion's roar.
The little mouse ran quickly toward the sound.
When he saw the lion trapped, he began chewing the ropes.
He worked hard with his sharp teeth.
Slowly the ropes began to break.
After some time the net finally opened.
The lion stood up proudly.
He thanked the tiny mouse.
“You were right,” said the lion kindly.
“Even the smallest friend can help the strongest king.”
From that day forward they remained good friends.
"""
},

{
"title": "The Honest Woodcutter",
"story": """
A poor woodcutter lived near a quiet forest.
Every morning he went to the forest to cut wood.
He worked very hard from sunrise to sunset.
The woodcutter sold the wood in the village market.
One day he was cutting a tree near a river.
Suddenly his axe slipped from his hands.
The axe fell into the deep river water.
The woodcutter became very sad.
Without his axe he could not work.
He sat beside the river and started crying.
Suddenly the river water sparkled with bright light.
A magical god appeared from the river.
The god asked the woodcutter why he was crying.
The woodcutter explained the problem honestly.
The god went underwater and returned with a golden axe.
“Is this your axe?” the god asked.
The woodcutter replied honestly, “No, mine is iron.”
The god went underwater again.
This time he brought a silver axe.
“Is this yours?” the god asked again.
The woodcutter shook his head.
“No sir, my axe is made of iron.”
The god went underwater one more time.
He came back holding the iron axe.
The woodcutter smiled happily.
“Yes, that is my axe.”
The god was pleased by the woodcutter's honesty.
He rewarded the woodcutter with the gold and silver axes.
The woodcutter returned home happily.
The villagers admired his honesty.
They learned that honesty always brings reward.
"""
},

{
"title": "The Boy Who Cried Wolf",
"story": """
A young shepherd boy lived near a quiet village.
His job was to watch over the sheep.
Every day he took the sheep to the green hills.
The sheep grazed peacefully in the grass.
The boy often felt bored watching them.
One day he decided to play a trick.
He shouted loudly, “Wolf! Wolf! Help!”
The villagers heard his cry and ran quickly to the hill.
They came with sticks and tools to scare the wolf away.
But when they arrived, they saw no wolf.
The boy laughed loudly at the villagers.
The villagers were angry but returned home.
The next day the boy played the same trick again.
He shouted, “Wolf! Wolf! Help!”
Once again the villagers ran to help him.
But again there was no wolf.
The villagers became very upset.
They warned the boy not to lie again.
A few days later a real wolf appeared.
The wolf slowly approached the sheep.
The boy became very frightened.
He shouted loudly, “Wolf! Wolf! Please help!”
The villagers heard him but did not believe him.
They thought he was lying again.
No one came to help the boy.
The wolf chased the sheep away.
The boy learned a painful lesson.
From that day on he never lied again.
The villagers also learned to trust carefully.
"""
}

]
# =============================================================================
#                                APP ORCHESTRATOR
# =============================================================================

class SmartReaderApp:
    _speech_cleared_time: float = 0.0

    def __init__(self) -> None:
        self.sfx          = SoundFX()
        self.speaker      = SpeechManager(self.sfx)
        self.training     = TrainingManager(self.speaker, self.sfx, TrainingConfig())
        self.story = StoryMode(self.speaker, self.sfx)
        self.ui           = SmartReaderUI(self.speaker)
        self.speaker.app  = self

        self.ocr_pipeline = OCRPipeline()
        self.camera       = CameraManager()
        self.cap: Optional[cv2.VideoCapture] = None
        self.camera_index: int = 0

        self.last_captured_text: str = ""
        self.last_lang_code:     str = "en"
        self.last_announced_lang: Optional[str] = None
        self.captured_frame:     Optional[np.ndarray] = None
        self.last_bbox:          Optional[Tuple[int,int,int,int]] = None
        self.capture_needs_retry: bool = False
        self.used_offline_ocr:   bool = False
        self._ocr_done_for_frame: bool = False
        self.prev_gray:          Optional[np.ndarray] = None
        self.steady_start_time:  Optional[float] = None
        self.auto_captured:      bool = False

        self.command_queue      = queue.Queue()
        self.key_queue          = queue.Queue()
        self.stop_listener_event = threading.Event()
        self.listener_cfg       = VoiceConfig()
        self.listener_thread    = threading.Thread(
            target=listen_for_commands,
            args=(self.command_queue, self.stop_listener_event,
                  self.sfx, self.speaker, self.listener_cfg),
            daemon=True,
        )
        self._story_mode_active = False
        self._reset_token = 0  # ✅ Increments on every hard reset — lets bg threads detect stale finish # NEW: Protects story from Watchdog
        def _watchdog():
            last_set = [0.0]
            while True:
                time.sleep(2.0)
                # If story_mode_active is True, the Watchdog will ignore the "stuck" command event
                is_story = self.story.is_active or SPEECH_ACTIVE_EVENT.is_set()
                if COMMAND_ACTIVE_EVENT.is_set() and not is_story:
                    if last_set[0] == 0.0: last_set[0] = time.time()
                    elif time.time() - last_set[0] > 15.0:
                        COMMAND_ACTIVE_EVENT.clear()
                        SPEECH_ACTIVE_EVENT.clear()
                        last_set[0] = 0.0
                else:
                    last_set[0] = 0.0
        threading.Thread(target=_watchdog, daemon=True).start()

    def _finish_command(self, say_ready: bool = True, _token: int = -1) -> None:
        # ✅ If a hard reset happened since this command started, silently discard finish
        if _token >= 0 and _token != self._reset_token:
            log_info("_finish_command skipped — hard reset occurred mid-command.")
            COMMAND_ACTIVE_EVENT.clear()
            return
        COMMAND_ACTIVE_EVENT.clear()
        VOSK_IGNORE_EVENT.clear()
        if self.sfx: self.sfx.play("done")
        if say_ready: self.speaker.say("Ready for the next command.")
        log_info("Command finished. Ready for next input.")
    # ── setup / teardown ─────────────────────────────────────────────────

    def setup(self) -> bool:
        try:
            if sys.platform.startswith("win"):
                tp = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
                if os.path.exists(tp):
                    pytesseract.pytesseract.tesseract_cmd = tp
                    log_info(f"Using Tesseract at: {tp}")
        except Exception as e:
            log_warn(f"Tesseract path note: {e}")

        threading.Thread(target=setup_gemini, daemon=True).start()
        ensure_tesseract_langs()

        self.camera_index = select_camera_index()
        if not self.camera.open(self.camera_index):
            if self.sfx: self.sfx.play("error")
            self.speaker.say(
                f"Critical error. Could not open the camera at index {self.camera_index}.",
                interrupt=True)
            time.sleep(0.3)
            return False
        self.cap = self.camera.cap

        if self.sfx:
            self.sfx.play("startup")
            time.sleep(0.2)

        # The background native thread is already handling the ping and audio, 
        # so we just quietly clear the events here.
        COMMAND_ACTIVE_EVENT.clear()
        SPEECH_ACTIVE_EVENT.clear()

        def _start_after_speech():
        # Wait until welcome speech finishes
            while SPEECH_ACTIVE_EVENT.is_set():
                time.sleep(0.1)

            # 🚀 SPEAK INSTRUCTIONS IMMEDIATELY
            self.speaker.say(
                "Smart Reader ready. "
                "Say Photo to capture. "
                "Say Start to read the text. "
                "Press 7 for short stories. "
                "Say Help for commands.",
                interrupt=True
            )

            APP_READY_EVENT.set()

            self.steady_start_time = None
            self.auto_captured = False
            self._capture_warned = False
            self._capture_warn_time = 0.0

            self._app_ready_time = time.time()

            # Start voice listener AFTER UI is ready
            self.listener_thread.start()

            log_info("Voice listener started.")
        threading.Thread(target=_start_after_speech, daemon=True).start()
        return True

    def teardown(self) -> None:
        APP_READY_EVENT.clear()
        log_info("Shutting down...")
        if hasattr(self, "ui") and self.ui:
            self.ui.close()
        try: self.stop_listener_event.set()
        except Exception: pass
        try: self.camera.release()
        except Exception: pass
        try:
            self.speaker.stop()
            time.sleep(0.2)
            self.speaker.cleanup_cache()
        except Exception: pass
        try: pygame.quit()
        except Exception: pass
        self.story.stop()

    # ── helpers ──────────────────────────────────────────────────────────

    def _reconnect_camera(self) -> Optional[np.ndarray]:
        log_warn("Camera feed lost. Reconnecting...")
        self.speaker.say("Camera connection lost. Trying to reconnect.", interrupt=True)
        for _ in range(5):
            time.sleep(1)
            try: self.cap.release()
            except Exception: pass
            self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret:
                    log_info("Camera reconnected.")
                    self.speaker.say("Camera reconnected. Say Capture to start.", interrupt=True)
                    if self.sfx: self.sfx.play("ok")
                    return frame
        log_err("Failed to reconnect.")
        if self.sfx: self.sfx.play("error")
        self.speaker.say("Could not reconnect to the camera. Shutting down.", interrupt=True)
        return None

    def _drain_last_command(self) -> Optional[str]:
        cmd = None
        try:
            while True: cmd = self.command_queue.get_nowait()
        except queue.Empty: pass
        return cmd

    def _detect_language_code(self, text: str) -> str:
        try:
            if len(text) < 20: raise ValueError("too short")
            code = detect(text)
            return code if code in LANG_CODE_TO_NAME else "en"
        except Exception as e:
            log_warn(f"Language detection fallback to English: {e}")
            return "en"

    # ── actions ──────────────────────────────────────────────────────────

    def do_capture(self, frame: np.ndarray) -> None:
        frame_copy = frame.copy()
        threading.Thread(target=self._do_capture_bg, args=(frame_copy,), daemon=True).start()

    def _do_capture_bg(self, frame: np.ndarray) -> None:
        _tok = self._reset_token
        try:
            self.captured_frame       = frame
            self.auto_captured        = True
            # ── FIX 4: Record manual capture time so auto-capture
            # does not immediately re-fire after this capture completes
            self._manual_command_time = time.time()
            self.last_bbox            = None
            self.used_offline_ocr     = False
            self.last_announced_lang  = None
            self.last_captured_text   = ""
            self._ocr_done_for_frame  = False

            b = estimate_brightness(frame)
            if self.sfx: self.sfx.play("capture")
            VOSK_IGNORE_EVENT.set()
            self.speaker.say("Image captured.", mute_mic=True)
            if b < 70:
                self.speaker.say("Lighting is low. Try moving to better light.", mute_mic=True)
            elif b > 200:
                self.speaker.say("The page looks overexposed. Reduce direct light.", mute_mic=True)
        finally:
            self._finish_command(_token=_tok)
    def do_retry(self) -> None:
        try:
            self.speaker.interrupt()
            while not self.speaker.queue.empty():
                try: self.speaker.queue.get_nowait()
                except Exception: break
            while not self.speaker._priority_queue.empty():
                try: self.speaker._priority_queue.get_nowait()
                except Exception: break

            self.captured_frame      = None
            self.prev_gray           = None
            self.steady_start_time   = None
            self.auto_captured       = False
            self._capture_warned     = False
            self._capture_warn_time  = 0.0
            self._app_ready_flushed  = True
            # ── FIX 4: Set app_ready_time far enough back so grace period
            # does NOT immediately re-arm auto-capture after retry.
            # User needs time to say next command like "photo" first.
            # 8 seconds cooldown before auto-capture can fire again.
            self._app_ready_time         = time.time()
            self._manual_command_time    = time.time()  # blocks auto-capture
            self.last_captured_text      = ""
            self._ocr_done_for_frame     = False
            self.last_bbox               = None
            self.capture_needs_retry     = False
            self.last_announced_lang     = None
            self.used_offline_ocr        = False
            self.camera.reset_motion()
            APP_READY_EVENT.set()
            self.speaker.say("Returning to live camera. Say Capture to start.")
        finally:
            self._finish_command()

    def do_speak(self, active_frame: np.ndarray) -> None:
        frame_copy = active_frame.copy()
        threading.Thread(target=self._do_speak_bg, args=(frame_copy,), daemon=True).start()

    def _do_speak_bg(self, active_frame: np.ndarray) -> None:
        _tok = self._reset_token
        try:
            if self.last_captured_text and self._ocr_done_for_frame:
                self.speaker.say("Reading the text now.")
                self.speaker.say(self.last_captured_text,
                                 lang=self.last_lang_code, cacheable=False, mute_mic=True)
                return

            if self.sfx: self.sfx.play("processing")
            self.speaker.say("Reading.", mute_mic=True)

            text, score, used_offline = self.ocr_pipeline.process(active_frame)

            if not text or score < 0.15:
                if self.sfx: self.sfx.play("error")
                self.speaker.say(
                    "I could not read the text clearly. "
                    "Try better lighting, hold the page closer and keep it straight. "
                    "Then say Retry and try again."
                )
                return

            self.last_captured_text  = text
            self.last_lang_code      = self._detect_language_code(text)
            self._ocr_done_for_frame = True

            if self.last_lang_code != self.last_announced_lang:
                spoken_lang = LANG_CODE_TO_SPOKEN.get(self.last_lang_code, "Unknown language")
                self.speaker.say(f"Detected language: {spoken_lang}.")
                self.last_announced_lang = self.last_lang_code

            if used_offline and not self.used_offline_ocr:
                self.speaker.say("Using offline reading. Some words may be less accurate.")
                self.used_offline_ocr = True

            self.speaker.say("Reading the text now.")
            self.speaker.say(self.last_captured_text,
                             lang=self.last_lang_code, cacheable=False, mute_mic=True)
        finally:
            self._finish_command(_token=_tok)

    def do_summarize(self) -> None:
        threading.Thread(target=self._do_summarize_bg, daemon=True).start()

    def _do_summarize_bg(self) -> None:
        try:
            if not self.last_captured_text:
                self.speaker.say(
                    "No text to summarize. Please capture an image and say Start first.")
                return
            if not GEMINI_AVAILABLE:
                self.speaker.say(
                    "Summarization is not available right now. "
                    "Please say Start to hear the full text instead.")
                return
            self.speaker.say("Summarizing. Please wait.", mute_mic=True)
            if self.sfx: self.sfx.play("processing")
            lang_name = LANG_CODE_TO_NAME.get(self.last_lang_code, "english")
            summary   = self.ocr_pipeline.summarize(self.last_captured_text, lang_name)
            if not summary:
                if self.sfx: self.sfx.play("error")
                self.speaker.say("Sorry, I could not generate a summary. Please try again.")
            else:
                self.speaker.say("Here is the summary.")
                self.speaker.say(summary, lang=self.last_lang_code, cacheable=False, mute_mic=True)
        finally:
            self._finish_command()

    def do_short_story(self):

        threading.Thread(target=self._do_short_story_bg, daemon=True).start()


    def _do_short_story_bg(self):

        COMMAND_ACTIVE_EVENT.set()
        VOSK_IGNORE_EVENT.set()

        start_token = self._reset_token   # 🔥 store reset token

        try:
            story = random.choice(SHORT_STORIES)
            title = story["title"]
            text  = story["story"]

            self.speaker.say(f"Here is a story called {title}.", mute_mic=True)
            self.speaker.wait_until_done()

            for line in text.strip().split("\n"):

                # 🛑 STOP instantly if reset happened
                if start_token != self._reset_token:
                    return

                if not line.strip():
                    continue

                if start_token != self._reset_token:
                    return

                self.speaker.say(line.strip(), mute_mic=True)
                self.speaker.wait_until_done()

                # 🛑 check again after speaking
                if start_token != self._reset_token:
                    return

        except Exception as e:
            log_err(f"Short story error: {e}")

        finally:

            COMMAND_ACTIVE_EVENT.clear()

            def unlock():
                time.sleep(0.3)
                VOSK_IGNORE_EVENT.clear()

            threading.Thread(target=unlock, daemon=True).start()
    def do_training(self) -> None:
        # ✅ FIXED: Do NOT clear COMMAND_ACTIVE_EVENT here.
        # TrainingManager._run sets AND clears it internally.
        # Clearing here causes a race where the main thread clears it
        # before the training thread has a chance to set it.
        if self.training.is_running:
            self.speaker.say("Training is already running. Press Spacebar to stop it.")
            COMMAND_ACTIVE_EVENT.clear()
            return
        self.speaker.say("Starting training mode.")
        self.training.start()
        # COMMAND_ACTIVE_EVENT is now owned by training thread — do not touch it here
        log_info("Training started — events handed to training thread.")

    def do_hard_reset(self) -> None:
        """
        Spacebar HARD RESET:
        - Stops ALL speech, stories, training instantly
        - Drains ALL queued commands (voice + keyboard) — no stale inputs
        - Resets every blocking event
        - Mic reopens immediately
        """
        self._reset_token += 1  # ✅ Invalidate all in-flight _finish_command calls
        log_info(f"HARD RESET triggered. Token={self._reset_token}")

        # 1. Stop training if running
        if self.training.is_running:
            self.training.stop()

        # 2. Stop story if running
        if self.story.is_active:
            self.story.stop()

        # 3. Kill all speech immediately
        self.speaker.interrupt()
        # Clear speech queue to prevent story resume
        while not self.speaker.queue.empty():
            try:
                self.speaker.queue.get_nowait()
            except:
                break

        while not self.speaker._priority_queue.empty():
            try:
                self.speaker._priority_queue.get_nowait()
            except:
                break

        # 4. Drain ALL queued voice commands — no stale commands
        while not self.command_queue.empty():
            try: self.command_queue.get_nowait()
            except Exception: break

        # 5. Drain ALL queued key presses — no stale keys
        while not self.key_queue.empty():
            try: self.key_queue.get_nowait()
            except Exception: break

        # 6. Clear ALL blocking events instantly
        COMMAND_ACTIVE_EVENT.clear()
        SPEECH_ACTIVE_EVENT.clear()
        TRAINING_MODE.clear()

        # 7. Reopen mic after a tiny echo guard
        def _reopen_mic():
            time.sleep(0.3)
            VOSK_IGNORE_EVENT.clear()
            log_info("Hard reset complete — system fully ready.")
        threading.Thread(target=_reopen_mic, daemon=True).start()

        if self.sfx: self.sfx.play("skip")
        self.speaker.say("Stopped. Ready.", mute_mic=True)

    def do_skip(self) -> None:
        threading.Thread(target=self.do_hard_reset, daemon=True).start()

    def do_help(self) -> None:
        try:    speak_help(self.speaker)
        finally: self._finish_command()

    def do_repeat(self) -> None:
        try:
            if not self.last_captured_text:
                self.speaker.say("No previous text available. Please say Capture and Speak first.")
                return
            self.speaker.say("Repeating the last text.")
            self.speaker.say(self.last_captured_text,
                             lang=self.last_lang_code, cacheable=False, mute_mic=True)
        finally:
            self._finish_command()

    def do_brightness(self, frame: np.ndarray) -> None:
        try:
            b = estimate_brightness(frame)
            if b < 70:
                self.speaker.say("Lighting is low. Try moving to a brighter place.")
            elif b > 200:
                self.speaker.say("The page looks overexposed. Reduce direct light.")
            else:
                self.speaker.say("Lighting looks okay for reading.")
        finally:
            self._finish_command()

    # ── main loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        if not self.setup():
            self.teardown()
            return

        assert self.cap is not None

        steady_start:     Optional[float] = None
        capture_warned:   bool  = False
        capture_warn_time: float = 0.0
        auto_captured:    bool  = False
        app_ready_flushed: bool = False
        APP_READY_GRACE_SEC: float = 8.0

        try:
            while True:
                action: Optional[str] = None

                if self.captured_frame is None:
                    ret, frame = self.cap.read()
                    if not ret or frame is None:
                        frame = self._reconnect_camera()
                        if frame is None: break

                    if AUTO_CAPTURE_ENABLED and APP_READY_EVENT.is_set():

                        if not app_ready_flushed:
                            app_ready_flushed  = True
                            steady_start       = None
                            auto_captured      = False
                            capture_warned     = False
                            capture_warn_time  = 0.0
                            self.camera.reset_motion()
                            log_info("Auto-capture armed.")

                        app_ready_time = getattr(self, "_app_ready_time", None)
                        in_grace = (app_ready_time is None
                                    or (time.time() - app_ready_time) < APP_READY_GRACE_SEC)

                        # ── FIX 4: Manual command cooldown ────────────────
                        # After user says "back" or "photo", suppress auto-capture
                        # for MANUAL_CMD_COOLDOWN_SEC so user can say next command
                        MANUAL_CMD_COOLDOWN_SEC = 8.0
                        manual_cmd_time = getattr(self, "_manual_command_time", 0.0)
                        in_manual_cooldown = (time.time() - manual_cmd_time) < MANUAL_CMD_COOLDOWN_SEC

                        if in_grace or in_manual_cooldown:
                            self.camera.reset_motion()

                        elif SPEECH_ACTIVE_EVENT.is_set():
                            if not capture_warned:
                                steady_start      = None
                                capture_warn_time = 0.0
                            self.camera.reset_motion()

                        elif (not COMMAND_ACTIVE_EVENT.is_set()
                                and not self.training.is_running
                                and not self.story.is_active):
                            
                            motion = self.camera.motion_score(frame)
                            if motion < MOTION_THRESHOLD:
                                if steady_start is None:
                                    steady_start   = time.time()
                                    capture_warned = False

                                elapsed = time.time() - steady_start

                                if elapsed >= 5.0 and not capture_warned and not auto_captured:
                                    capture_warned    = True
                                    capture_warn_time = time.time()
                                    log_info("Auto-capture warning fired.")
                                    self.speaker.say(
                                        "Page detected. Hold still. Capturing in 3 seconds.",
                                        mute_mic=True)

                                elif (capture_warned and not auto_captured
                                        and not SPEECH_ACTIVE_EVENT.is_set()
                                        and time.time() - capture_warn_time >= 3.0):
                                    auto_captured = True
                                    COMMAND_ACTIVE_EVENT.set()
                                    log_info("Auto-capturing.")
                                    self.do_capture(frame)
                            else:
                                steady_start      = None
                                auto_captured     = False
                                capture_warned    = False
                                capture_warn_time = 0.0
                else:
                    frame = self.captured_frame

                active_frame = frame

                if SHOW_CAMERA_WINDOW:
                    if not self.ui.running: break

                    if self.last_bbox:
                        display_frame = active_frame.copy()
                        x1, y1, x2, y2 = self.last_bbox
                        h, w = display_frame.shape[:2]
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(w-1, x2), min(h-1, y2)
                        if x2 > x1 and y2 > y1:
                            cv2.rectangle(display_frame, (x1,y1), (x2,y2), (0,255,0), 2)
                    else:
                        display_frame = active_frame

                    if self.captured_frame is not None:
                        cv2.putText(display_frame, "CAPTURED", (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2, cv2.LINE_AA)
                    self.ui.update_frame(display_frame)
                else:
                    time.sleep(0.01)

                action = self._drain_last_command()
                if not action:
                    try:
                        action = self.key_queue.get_nowait()
                        # ✅ hardreset and skip never set COMMAND_ACTIVE_EVENT — they always fire
                        if action not in ("hardreset", "skip"):
                            # ✅ Block keyboard during training or story — only hardreset/skip pass
                            if self.training.is_running or self.story.is_active:
                                log_warn(f"Key '{action}' blocked — training/story active.")
                                action = None
                            else:
                                COMMAND_ACTIVE_EVENT.set()
                                if self.sfx: self.sfx.play("processing")
                    except queue.Empty:
                        pass

                if not action:
                    time.sleep(0.01)
                    continue

                # ── HARD RESET: Spacebar — always wins, no matter what is running ──
                if action == "hardreset":
                    self.do_hard_reset()
                    steady_start      = None
                    auto_captured     = False
                    capture_warned    = False
                    capture_warn_time = 0.0
                    continue

                # ── SKIP: voice "stop/skip" — runs in background so it can clear events ──
                if action == "skip":
                    steady_start      = None
                    auto_captured     = False
                    capture_warned    = False
                    capture_warn_time = 0.0
                    threading.Thread(target=self.do_hard_reset, daemon=True).start()
                    continue

                # ── All other commands — single handler, no duplicates ──
                if action in ("speak", "summarize", "retry", "training", "help", "exit"):
                    self.speaker.interrupt()

                if action == "training":
                    self.do_training()
                elif action == "help":
                    self.do_help()
                elif action == "repeat":
                    self.do_repeat()
                elif action == "brightness":
                    target = self.captured_frame if self.captured_frame is not None else active_frame
                    self.do_brightness(target)
                elif action == "capture" and self.captured_frame is None:
                    self.do_capture(frame)
                elif action == "capture" and self.captured_frame is not None:
                    # Already captured — ignore silently
                    COMMAND_ACTIVE_EVENT.clear()
                elif action == "retry" and self.captured_frame is not None:
                    steady_start      = None
                    auto_captured     = False
                    capture_warned    = False
                    capture_warn_time = 0.0
                    self.do_retry()
                elif action == "retry" and self.captured_frame is None:
                    # Nothing to retry
                    self.speaker.say("No image captured yet.")
                    COMMAND_ACTIVE_EVENT.clear()
                elif action == "speak":
                    if self.captured_frame is None:
                        self.speaker.say("No image captured yet. Please say Photo first.")
                        COMMAND_ACTIVE_EVENT.clear()
                    else:
                        self.do_speak(active_frame)
                elif action == "summarize":
                    self.do_summarize()
                elif action == "audiobook":
                    if self.story.is_active:
                        self.story.stop()
                        time.sleep(0.2)
                    self.speaker.interrupt()
                    self.story.start("audiobook")
                elif action == "story":
                    if self.story.is_active:
                        self.story.stop()
                        time.sleep(0.2)
                    self.speaker.interrupt()
                    self.do_short_story()
                elif action == "exit":
                    if self.sfx: self.sfx.play("goodbye")
                    self.speaker.say("Exiting. Goodbye.", interrupt=True)
                    time.sleep(3)
                    self._finish_command(say_ready=False)
                    break
                else:
                    log_warn(f"Command '{action}' not valid right now. Unlocking mic.")
                    self._finish_command(say_ready=False)
        finally:
            self.teardown()


# =============================================================================
#                                    ENTRY
# =============================================================================

def main() -> None:
    # Keeps the Splash Screen responsive and spinning while libraries load in the background
    while not LIBRARIES_LOADED.is_set():
        try:
            ROOT.update()
        except Exception:
            pass
        time.sleep(0.05)

    # Now that the heavy lifting is done, proceed instantly
    cfg_json = os.environ.get("SMART_READER_CFG", "")
    if cfg_json:
        try:
            cfg = json.loads(cfg_json)
            global SHOW_CAMERA_WINDOW
            if isinstance(cfg.get("show_window"), bool):
                SHOW_CAMERA_WINDOW = cfg["show_window"]
        except Exception:
            pass
    app = SmartReaderApp()

    import __main__
    __main__.app_instance = app

    app.run()


if __name__ == "__main__":
    main()