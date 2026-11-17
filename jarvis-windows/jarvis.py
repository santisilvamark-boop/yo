"""Jarvis: asistente de voz para Windows.

Dices "Hey Jarvis", le hablas, y Jarvis responde con voz y puede usar tu
computadora (ratón, teclado, pantalla) para hacer lo que le pidas.

Piezas:
  - Palabra de activación: openWakeWord (modelo "hey_jarvis"), local.
  - Voz a texto: faster-whisper, local.
  - Cerebro: Claude (API de Anthropic) con la herramienta de control del ordenador.
  - Texto a voz: voces neuronales de Microsoft (edge-tts); si falla, la voz de Windows.

Atajos globales:
  Ctrl+Alt+J  -> hablar con Jarvis sin decir "Hey Jarvis"
  Ctrl+Alt+S  -> detener lo que esté haciendo Jarvis
  Llevar el ratón a la esquina superior izquierda también lo detiene.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext

import numpy as np

# Antes de importar pyautogui/mss: trabajar en píxeles reales aunque Windows
# tenga el escalado al 125 % o 150 %. Si no, los clics caen en el sitio equivocado.
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import anthropic  # noqa: E402
import mss  # noqa: E402
import pyautogui  # noqa: E402
import pyperclip  # noqa: E402
import sounddevice as sd  # noqa: E402
from PIL import Image  # noqa: E402

pyautogui.FAILSAFE = True   # ratón a la esquina superior izquierda = parar
pyautogui.PAUSE = 0.05

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "modelo": "claude-opus-5-5",
    # "low" responde más rápido; "medium" o "high" piensa más en tareas difíciles.
    "esfuerzo": "low",
    # true = modo rápido de Claude (más rápido, cuesta el doble por token).
    "modo_rapido": False,
    "voz": "es-MX-DaliaNeural",
    "modelo_whisper": "small",
    "idioma": "es",
    "sensibilidad_activacion": 0.5,
    "max_pasos_por_orden": 40,
    # Lado largo de las capturas que se envían a Claude (más pequeño = más rápido y barato).
    "ancho_captura": 1366,
    # Pedir permiso en pantalla antes de ejecutar comandos de PowerShell.
    "confirmar_comandos": True,
}

SYSTEM_PROMPT = """Eres Jarvis, el asistente personal por voz de {nombre}, en su computadora con Windows.
Hablas siempre en español, con frases cortas y naturales, porque tus respuestas se leen en voz alta:
nada de listas, tablas, markdown, emojis ni enlaces largos.

Puedes ver la pantalla y usar el ratón y el teclado con las herramientas de computadora, y ejecutar
comandos de PowerShell con la herramienta ejecutar_powershell (por ejemplo para abrir programas:
"Start-Process outlook", "Start-Process ms-teams:", "Start-Process https://mail.google.com").

Cómo trabajar:
- Para tareas en la computadora, toma una captura primero, actúa, y comprueba con otra captura que salió bien.
- Prefiere atajos de teclado y abrir programas con PowerShell antes que buscar iconos con el ratón.
- Si una tarea es larga, no la narres paso a paso; al terminar, di en una o dos frases qué hiciste.
- Antes de algo irreversible o que hable en nombre de {nombre} (enviar un correo o un mensaje de Teams,
  publicar, comprar, borrar archivos, aceptar o cancelar reuniones), detente y pregunta en voz alta
  "¿Lo envío?" o similar, resumiendo qué vas a hacer. Solo continúa cuando {nombre} lo confirme.
- No escribas contraseñas ni datos de pago, y no inicies sesión en cuentas por tu cuenta.
- En juegos en línea no automatices partidas: suele estar prohibido y pueden banear la cuenta.
- Si algo falla o no puedes hacerlo, dilo claramente en una frase.
"""

POWERSHELL_TOOL = {
    "name": "ejecutar_powershell",
    "description": (
        "Ejecuta un comando de PowerShell en la computadora del usuario y devuelve su salida. "
        "Úsalo para abrir programas o páginas web (Start-Process), consultar la hora, archivos, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "comando": {"type": "string", "description": "Comando de PowerShell a ejecutar."}
        },
        "required": ["comando"],
        "additionalProperties": False,
    },
    "strict": True,
}

# Nombres de tecla que usa Claude (estilo xdotool) -> nombres de pyautogui.
KEY_MAP = {
    "return": "enter", "enter": "enter", "kp_enter": "enter",
    "escape": "esc", "esc": "esc",
    "backspace": "backspace", "delete": "delete", "del": "delete",
    "tab": "tab", "space": "space",
    "page_down": "pagedown", "pagedown": "pagedown", "next": "pagedown",
    "page_up": "pageup", "pageup": "pageup", "prior": "pageup",
    "home": "home", "end": "end", "insert": "insert",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "super": "win", "super_l": "win", "win": "win", "windows": "win", "cmd": "win", "meta": "win",
    "ctrl": "ctrl", "control": "ctrl", "control_l": "ctrl", "control_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "print": "printscreen", "caps_lock": "capslock", "menu": "apps",
    "minus": "-", "plus": "+", "equal": "=", "comma": ",", "period": ".", "slash": "/",
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"config.json no es válido, uso valores por defecto: {exc}")
    else:
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def to_pyautogui_keys(combo: str) -> list[str]:
    keys = []
    for part in combo.replace(" ", "").split("+"):
        if not part:
            continue
        low = part.lower()
        keys.append(KEY_MAP.get(low, low))
    return keys


class StopRequested(Exception):
    pass


# --------------------------------------------------------------------------- pantalla y acciones


class Computer:
    """Ejecuta las acciones de la herramienta de computadora de Claude con pyautogui."""

    def __init__(self, target_long_edge: int):
        with mss.mss() as sct:
            mon = sct.monitors[1]  # pantalla principal
        self.monitor = mon
        self.width, self.height = mon["width"], mon["height"]
        self.scale = min(1.0, target_long_edge / max(self.width, self.height))

    # Claude trabaja en el espacio de píxeles de la captura reducida.
    def to_screen(self, xy) -> tuple[int, int]:
        x, y = xy
        return (
            self.monitor["left"] + int(round(x / self.scale)),
            self.monitor["top"] + int(round(y / self.scale)),
        )

    def _grab(self) -> Image.Image:
        with mss.mss() as sct:
            raw = sct.grab(self.monitor)
        return Image.frombytes("RGB", raw.size, raw.rgb)

    @staticmethod
    def _png_b64(img: Image.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.standard_b64encode(buf.getvalue()).decode()

    def screenshot(self) -> str:
        img = self._grab()
        if self.scale < 1.0:
            img = img.resize((int(self.width * self.scale), int(self.height * self.scale)), Image.LANCZOS)
        return self._png_b64(img)

    def zoom(self, region) -> str:
        x0, y0 = self.to_screen(region[:2])
        x1, y1 = self.to_screen(region[2:])
        img = self._grab().crop((x0 - self.monitor["left"], y0 - self.monitor["top"],
                                 x1 - self.monitor["left"], y1 - self.monitor["top"]))
        img.thumbnail((1568, 1568))  # dentro de los límites de imagen
        return self._png_b64(img)

    def _with_modifiers(self, mods: str | None, fn):
        keys = to_pyautogui_keys(mods) if mods else []
        for k in keys:
            pyautogui.keyDown(k)
        try:
            fn()
        finally:
            for k in reversed(keys):
                pyautogui.keyUp(k)

    def type_text(self, text: str):
        # Pegar desde el portapapeles: pyautogui.write no escribe ñ ni tildes.
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.15)
        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass

    def run(self, name: str, inp: dict) -> list | str:
        """Ejecuta una acción. Devuelve el contenido del tool_result."""
        coord = inp.get("coordinate")
        mods = inp.get("text") if name not in ("type", "key", "hold_key") else None

        if name == "screenshot":
            return [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": self.screenshot()}}]
        if name == "zoom":
            return [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": self.zoom(inp["region"])}}]
        if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            pos = self.to_screen(coord) if coord else pyautogui.position()
            button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
            clicks = {"double_click": 2, "triple_click": 3}.get(name, 1)
            self._with_modifiers(mods, lambda: pyautogui.click(*pos, clicks=clicks, interval=0.08, button=button))
            return "OK"
        if name == "left_click_drag":
            sx, sy = self.to_screen(inp["start_coordinate"])
            ex, ey = self.to_screen(coord)

            def drag():
                pyautogui.moveTo(sx, sy)
                pyautogui.mouseDown()
                pyautogui.moveTo(ex, ey, duration=0.4)
                pyautogui.mouseUp()
            self._with_modifiers(mods, drag)
            return "OK"
        if name == "mouse_move":
            pyautogui.moveTo(*self.to_screen(coord), duration=0.1)
            return "OK"
        if name == "left_mouse_down":
            pyautogui.mouseDown()
            return "OK"
        if name == "left_mouse_up":
            pyautogui.mouseUp()
            return "OK"
        if name == "cursor_position":
            x, y = pyautogui.position()
            return json.dumps([int((x - self.monitor["left"]) * self.scale),
                               int((y - self.monitor["top"]) * self.scale)])
        if name == "scroll":
            if coord:
                pyautogui.moveTo(*self.to_screen(coord))
            amount = int(inp.get("scroll_amount", 3))
            direction = inp.get("scroll_direction", "down")
            # En Windows, 120 unidades = una "muesca" de la rueda.
            delta = amount * 120 * (1 if direction in ("up", "left") else -1)
            extra = "shift" if direction in ("left", "right") else None
            all_mods = "+".join(m for m in (mods, extra) if m)
            self._with_modifiers(all_mods or None, lambda: pyautogui.scroll(delta))
            return "OK"
        if name == "type":
            self.type_text(inp["text"])
            return "OK"
        if name == "key":
            keys = to_pyautogui_keys(inp["text"])
            for _ in range(int(inp.get("repeat", 1))):
                pyautogui.hotkey(*keys)
            return "OK"
        if name == "hold_key":
            keys = to_pyautogui_keys(inp["text"])
            for k in keys:
                pyautogui.keyDown(k)
            time.sleep(min(float(inp.get("duration", 1)), 300))
            for k in reversed(keys):
                pyautogui.keyUp(k)
            return "OK"
        if name == "wait":
            time.sleep(min(float(inp.get("duration", 1)), 300))
            return "OK"
        raise ValueError(f"Acción no soportada: {name}")


# --------------------------------------------------------------------------- voz


class Voice:
    """Micrófono (activación + grabación), transcripción y voz de salida."""

    SAMPLE_RATE = 16000
    FRAME = 1280  # 80 ms, lo que espera openWakeWord

    def __init__(self, cfg: dict, log):
        self.cfg = cfg
        self.log = log
        self.speaking = threading.Event()
        self._whisper = None
        self._wake = None

    # --- carga perezosa de modelos (la primera vez se descargan)
    def wake_model(self):
        if self._wake is None:
            import openwakeword
            from openwakeword.model import Model
            try:
                openwakeword.utils.download_models(["hey_jarvis"])
            except Exception:
                pass
            self._wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        return self._wake

    def whisper(self):
        if self._whisper is None:
            from faster_whisper import WhisperModel
            self.log("Cargando el reconocimiento de voz (la primera vez se descarga)...")
            self._whisper = WhisperModel(self.cfg["modelo_whisper"], device="cpu", compute_type="int8")
        return self._whisper

    # --- grabar hasta que el usuario se calle
    def record_command(self, stream: sd.InputStream, max_seconds=20.0, silence_seconds=1.2) -> np.ndarray:
        frames, noise, silent_for, heard_voice = [], [], 0.0, False
        start = time.time()
        frame_s = self.FRAME / self.SAMPLE_RATE
        while time.time() - start < max_seconds:
            data, _ = stream.read(self.FRAME)
            chunk = data[:, 0].copy()
            frames.append(chunk)
            rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
            if len(noise) < 4:            # los primeros 320 ms sirven para medir el ruido
                noise.append(rms)
                continue
            threshold = max(np.median(noise) * 2.5, 300.0)
            if rms > threshold:
                heard_voice, silent_for = True, 0.0
            else:
                silent_for += frame_s
                if heard_voice and silent_for >= silence_seconds:
                    break
                if not heard_voice and time.time() - start > 6:
                    break  # no dijo nada
        return np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < self.SAMPLE_RATE // 2:
            return ""
        segments, _ = self.whisper().transcribe(
            audio.astype(np.float32) / 32768.0, language=self.cfg["idioma"], vad_filter=True, beam_size=1
        )
        return " ".join(s.text.strip() for s in segments).strip()

    # --- hablar
    def say(self, text: str):
        if not text:
            return
        self.speaking.set()
        try:
            try:
                self._say_edge(text)
            except Exception as exc:
                self.log(f"(Voz neuronal no disponible, uso la de Windows: {exc})")
                self._say_windows(text)
        finally:
            time.sleep(0.3)
            self.speaking.clear()

    def _say_edge(self, text: str):
        import edge_tts
        import miniaudio

        async def synth() -> bytes:
            out = bytearray()
            async for chunk in edge_tts.Communicate(text, self.cfg["voz"]).stream():
                if chunk["type"] == "audio":
                    out.extend(chunk["data"])
            return bytes(out)

        mp3 = asyncio.run(synth())
        dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
        pcm = np.frombuffer(dec.samples, dtype=np.int16)
        sd.play(pcm, 24000)
        sd.wait()

    @staticmethod
    def _say_windows(text: str):
        import pyttsx3
        engine = pyttsx3.init()
        for v in engine.getProperty("voices"):
            if "spanish" in v.name.lower() or "es" in str(getattr(v, "languages", "")).lower():
                engine.setProperty("voice", v.id)
                break
        engine.say(text)
        engine.runAndWait()


# --------------------------------------------------------------------------- cerebro (Claude)


class Brain:
    def __init__(self, cfg: dict, computer: Computer, log, confirm, stop_event: threading.Event):
        self.cfg = cfg
        self.computer = computer
        self.log = log
        self.confirm = confirm
        self.stop_event = stop_event
        self.client = anthropic.Anthropic()  # lee ANTHROPIC_API_KEY
        self.history: list[dict] = []        # conversación reciente, solo texto
        self.system = SYSTEM_PROMPT.format(nombre=os.environ.get("JARVIS_NOMBRE_USUARIO", "el usuario"))
        self.tools = [
            {"type": "computer_toolset_20260801", "cache_control": {"type": "ephemeral"}},
            POWERSHELL_TOOL,
        ]

    def _powershell(self, command: str) -> tuple[str, bool]:
        if self.cfg.get("confirmar_comandos", True) and not self.confirm(command):
            return "El usuario no permitió ejecutar este comando.", True
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True, text=True, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out = (res.stdout + res.stderr).strip()[-4000:] or "(sin salida)"
            return out, res.returncode != 0
        except subprocess.TimeoutExpired:
            return "El comando tardó más de 60 segundos y se canceló.", True

    def _run_tools(self, content) -> list[dict]:
        results, computer_failed = [], False
        for block in content:
            if block.type != "tool_use":
                continue
            if self.stop_event.is_set():
                raise StopRequested()
            toolset = getattr(block, "toolset_name", None)
            if toolset == "computer":
                if computer_failed:
                    results.append({"type": "tool_result", "tool_use_id": block.id, "toolset_name": "computer",
                                    "is_error": True,
                                    "content": "Not executed: an earlier computer action in this turn failed."})
                    continue
                try:
                    out = self.computer.run(block.name, dict(block.input))
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "toolset_name": "computer", "content": out})
                except pyautogui.FailSafeException:
                    raise StopRequested()
                except Exception as exc:
                    computer_failed = True
                    results.append({"type": "tool_result", "tool_use_id": block.id, "toolset_name": "computer",
                                    "is_error": True, "content": f"Error: {exc}"})
            elif block.name == "ejecutar_powershell":
                cmd = block.input.get("comando", "") if isinstance(block.input, dict) else ""
                self.log(f"PowerShell: {cmd}")
                out, is_err = self._powershell(cmd)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_err})
            else:
                results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                "content": f"Herramienta desconocida: {block.name}"})
        return results

    def ask(self, user_text: str) -> str:
        recent = "\n".join(f"{who}: {txt}" for who, txt in self.history[-8:])
        prompt = (f"Conversación reciente:\n{recent}\n\n" if recent else "") + f"El usuario dice: {user_text}"
        messages: list = [{"role": "user", "content": prompt}]
        final_text = ""

        for step in range(int(self.cfg["max_pasos_por_orden"])):
            if self.stop_event.is_set():
                raise StopRequested()
            kwargs = dict(
                model=self.cfg["modelo"],
                max_tokens=16000,
                system=self.system,
                tools=self.tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.cfg["esfuerzo"]},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                # Caché automático: cada paso reenvía las capturas anteriores; así cuestan ~10 veces menos.
                cache_control={"type": "ephemeral"},
            )
            if self.cfg.get("modo_rapido"):
                kwargs["speed"] = "fast"
            with self.client.beta.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()

            messages.append({"role": "assistant", "content": response.content})
            texts = [b.text for b in response.content if b.type == "text" and b.text.strip()]
            if texts:
                final_text = " ".join(texts)

            if response.stop_reason == "refusal":
                final_text = "Lo siento, eso no lo puedo hacer."
                break
            if response.stop_reason != "tool_use":
                break
            n = sum(1 for b in response.content if b.type == "tool_use")
            self.log(f"Paso {step + 1}: {n} acción(es) en la computadora...")
            messages.append({"role": "user", "content": self._run_tools(response.content)})
        else:
            final_text = final_text or "Llevo muchos pasos y no he terminado. ¿Quieres que siga?"

        self.history.append(("Usuario", user_text))
        self.history.append(("Jarvis", final_text))
        return final_text


# --------------------------------------------------------------------------- ventana


class JarvisApp:
    def __init__(self):
        self.cfg = load_config()
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = threading.Event()
        self.manual_trigger = threading.Event()

        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.geometry("420x520")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#0f172a")

        self.status = tk.Label(self.root, text="Iniciando...", font=("Segoe UI", 14, "bold"),
                               fg="#38bdf8", bg="#0f172a")
        self.status.pack(pady=(14, 6))
        tk.Label(self.root, text='Di "Hey Jarvis" o pulsa Ctrl+Alt+J', font=("Segoe UI", 10),
                 fg="#94a3b8", bg="#0f172a").pack()

        self.logbox = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, font=("Segoe UI", 10),
                                                bg="#1e293b", fg="#e2e8f0", relief=tk.FLAT, height=18)
        self.logbox.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        self.logbox.configure(state=tk.DISABLED)

        row = tk.Frame(self.root, bg="#0f172a")
        row.pack(fill=tk.X, padx=12, pady=(0, 12))
        self.entry = tk.Entry(row, font=("Segoe UI", 11))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", lambda _e: self.send_typed())
        tk.Button(row, text="🎤", command=self.manual_trigger.set).pack(side=tk.LEFT, padx=4)
        tk.Button(row, text="Detener", command=self.stop).pack(side=tk.LEFT)

        try:
            import keyboard
            keyboard.add_hotkey("ctrl+alt+j", self.manual_trigger.set)
            keyboard.add_hotkey("ctrl+alt+s", self.stop)
        except Exception as exc:
            self.log(f"(Atajos globales no disponibles: {exc})")

        self.voice = Voice(self.cfg, self.log)
        self.computer = Computer(int(self.cfg["ancho_captura"]))
        self.brain = Brain(self.cfg, self.computer, self.log, self.confirm_command, self.stop_event)

        self.root.after(100, self._drain_events)
        threading.Thread(target=self._listen_loop, daemon=True).start()

    # --- utilidades de interfaz (seguras desde hilos)
    def log(self, text: str):
        self.events.put(("log", text))

    def set_status(self, text: str):
        self.events.put(("status", text))

    def _drain_events(self):
        while not self.events.empty():
            kind, payload = self.events.get()
            if kind == "log":
                self.logbox.configure(state=tk.NORMAL)
                self.logbox.insert(tk.END, payload + "\n\n")
                self.logbox.see(tk.END)
                self.logbox.configure(state=tk.DISABLED)
            elif kind == "status":
                self.status.configure(text=payload)
            elif kind == "confirm":
                command, box = payload
                box["ok"] = messagebox.askyesno(
                    "Jarvis quiere ejecutar un comando", f"¿Permitir este comando de PowerShell?\n\n{command}",
                    parent=self.root)
                box["done"].set()
        self.root.after(100, self._drain_events)

    def confirm_command(self, command: str) -> bool:
        box = {"ok": False, "done": threading.Event()}
        self.events.put(("confirm", (command, box)))
        box["done"].wait()
        return box["ok"]

    def stop(self):
        if self.busy.is_set():
            self.stop_event.set()
            self.set_status("Deteniendo...")

    def send_typed(self):
        text = self.entry.get().strip()
        if text and not self.busy.is_set():
            self.entry.delete(0, tk.END)
            threading.Thread(target=self.handle, args=(text,), daemon=True).start()

    # --- una orden completa
    def handle(self, text: str):
        self.busy.set()
        self.stop_event.clear()
        self.log(f"Tú: {text}")
        self.set_status("Pensando...")
        try:
            answer = self.brain.ask(text)
        except StopRequested:
            answer = "Vale, me detengo."
        except anthropic.AuthenticationError:
            answer = "Mi clave de Anthropic no es válida. Revisa la configuración."
        except anthropic.RateLimitError:
            answer = "Estoy recibiendo demasiadas peticiones. Inténtalo en un momento."
        except anthropic.APIConnectionError:
            answer = "No tengo conexión a internet ahora mismo."
        except anthropic.APIStatusError as exc:
            answer = "Hubo un error con el servicio de Claude."
            self.log(f"(Error de la API: {exc.status_code} {exc.message})")
        except Exception as exc:
            answer = "Algo salió mal."
            self.log(f"(Error: {exc!r})")
        self.log(f"Jarvis: {answer}")
        self.set_status("Hablando...")
        self.voice.say(answer)
        self.set_status("Esperando \"Hey Jarvis\"")
        self.busy.clear()

    # --- bucle del micrófono
    def _listen_loop(self):
        try:
            wake = self.voice.wake_model()
            self.voice.whisper()
        except Exception as exc:
            self.log(f"No pude cargar la voz: {exc!r}. Puedes escribirme abajo.")
            self.set_status("Solo texto")
            return

        threshold = float(self.cfg["sensibilidad_activacion"])
        try:
            stream = sd.InputStream(samplerate=Voice.SAMPLE_RATE, channels=1, dtype="int16",
                                    blocksize=Voice.FRAME)
            stream.start()
        except Exception as exc:
            self.log(f"No encuentro el micrófono ({exc}). Puedes escribirme abajo.")
            self.set_status("Solo texto (sin micrófono)")
            return
        self.set_status("Esperando \"Hey Jarvis\"")
        self.log("Listo. Di \"Hey Jarvis\" y luego lo que necesites.")
        with stream:
            while True:
                data, _ = stream.read(Voice.FRAME)
                if self.busy.is_set() or self.voice.speaking.is_set():
                    wake.reset()
                    continue
                score = max(wake.predict(data[:, 0]).values() or [0.0])
                if score < threshold and not self.manual_trigger.is_set():
                    continue
                self.manual_trigger.clear()
                wake.reset()
                self.set_status("Escuchando...")
                self._beep()
                audio = self.voice.record_command(stream)
                self.set_status("Entendiendo...")
                text = self.voice.transcribe(audio)
                if not text:
                    self.set_status("Esperando \"Hey Jarvis\"")
                    continue
                threading.Thread(target=self.handle, args=(text,), daemon=True).start()

    @staticmethod
    def _beep():
        if sys.platform == "win32":
            try:
                import winsound
                winsound.Beep(880, 120)
            except Exception:
                pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        tk.Tk().withdraw()
        messagebox.showerror("Jarvis", "Falta la clave de Anthropic (ANTHROPIC_API_KEY).\n"
                                       "Vuelve a ejecutar el instalador.")
        sys.exit(1)
    JarvisApp().run()
