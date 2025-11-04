from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.clock import Clock
from kivy.core.window import Window

import sounddevice as sd
import queue
import threading
import json
import traceback

# Defer jnius imports to runtime and guard them
try:
    from jnius import autoclass, cast
    JNI_AVAILABLE = True
except Exception:
    autoclass = None
    cast = None
    JNI_AVAILABLE = False

# Defer vosk model creation to avoid startup blocking and catch failures
try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except Exception:
    Model = None
    KaldiRecognizer = None
    VOSK_AVAILABLE = False

# Set landscape-friendly dimensions
Window.size = (800, 480)

# Shared queue for audio frames
q = queue.Queue()

# Lazy-initialized recognizer container
_recognizer = {
    "model": None,
    "rec": None,
    "ready": False
}

def ensure_recognizer():
    if not VOSK_AVAILABLE:
        return False
    if _recognizer["ready"]:
        return True
    try:
        _recognizer["model"] = Model(lang="en-us")
        _recognizer["rec"] = KaldiRecognizer(_recognizer["model"], 16000)
        _recognizer["ready"] = True
        return True
    except Exception:
        traceback.print_exc()
        _recognizer["ready"] = False
        return False

class VoiceNoteLayout(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', **kwargs)
        self.label = Label(text="Tap to record", font_size=24)
        self.button = Button(text="Start Recording", size_hint=(1, 0.2), font_size=20)
        self.button.bind(on_press=self.toggle_recording)
        self.add_widget(self.label)
        self.add_widget(self.button)

        self.recording = False
        self.transcript = ""
        self._rec_thread = None
        self._stop_event = threading.Event()

    def toggle_recording(self, instance):
        # Simple debounce
        if getattr(self, "_toggling", False):
            return
        self._toggling = True
        try:
            if not self.recording:
                # Start
                self.transcript = ""
                self.label.text = "Initializing..."
                self.button.text = "Stop Recording"
                self.recording = True
                self._stop_event.clear()

                # Ensure recognizer ready; do not block UI for long
                ok = ensure_recognizer()
                if not ok:
                    self.label.text = "Speech model unavailable"
                    self.button.text = "Start Recording"
                    self.recording = False
                    return

                # Start thread as daemon so it won't block app exit
                self._rec_thread = threading.Thread(target=self.record_audio, daemon=True)
                self._rec_thread.start()
                self.label.text = "Listening..."
            else:
                # Stop
                self.recording = False
                self._stop_event.set()
                self.label.text = "Processing..."
                self.button.text = "Start Recording"
                # Schedule intent send after a small delay so UI can update
                Clock.schedule_once(lambda dt: self.launch_notes(), 0.7)
        finally:
            # short delay to avoid re-entrancy issues
            Clock.schedule_once(lambda dt: setattr(self, "_toggling", False), 0.05)

    def record_audio(self):
        """
        Runs in a background thread. Collects raw audio frames and feeds recognizer.
        Defensive: catches exceptions, uses timeouts on queue.get, respects stop event.
        """
        try:
            rec = _recognizer.get("rec")
            if rec is None:
                print("No recognizer available")
                return

            def callback(indata, frames, time, status):
                try:
                    if status:
                        print("Sounddevice status:", status)
                    q.put(bytes(indata))
                except Exception:
                    print("Callback queue put failed")
                    traceback.print_exc()

            # Use a try/except around stream start/stop to avoid crashes
            try:
                with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype='int16',
                                       channels=1, callback=callback):
                    while not self._stop_event.is_set():
                        try:
                            # use timeout so we can check stop condition periodically
                            data = q.get(timeout=0.5)
                        except Exception:
                            continue
                        try:
                            # Guard recognizer calls
                            if _recognizer.get("rec") and _recognizer["rec"].AcceptWaveform(data):
                                try:
                                    result = json.loads(_recognizer["rec"].Result())
                                    text = result.get("text", "")
                                    if text:
                                        # Update transcript thread-safely via Clock
                                        Clock.schedule_once(lambda dt, t=text: self._append_transcript(t), 0)
                                except Exception:
                                    traceback.print_exc()
                        except Exception:
                            print("Recognizer processing failed")
                            traceback.print_exc()
            except Exception:
                print("Audio stream failed to start or crashed")
                traceback.print_exc()
        except Exception:
            print("Unexpected error in record_audio")
            traceback.print_exc()
        finally:
            # ensure we clear recording flags on exit of thread
            Clock.schedule_once(lambda dt: self._on_record_thread_exit(), 0)

    def _append_transcript(self, text):
        # Append safely on the main thread
        try:
            if text:
                self.transcript += text + " "
        except Exception:
            traceback.print_exc()

    def _on_record_thread_exit(self):
        self.recording = False
        self.button.text = "Start Recording"
        if not self.transcript.strip():
            self.label.text = "No speech detected"
        else:
            self.label.text = "Processing complete"

    def launch_notes(self):
        """
        Prepare and send the Intent safely. Uses multiple defensive checks:
        - ensure JNI available
        - ensure PythonActivity.mActivity is non-null
        - ensure transcript non-empty
        - catch and display exceptions
        """
        # quick guard
        if not self.transcript.strip():
            self.label.text = "No transcript available."
            return

        # If JNI isn't available, show transcript in-app instead of sending intent
        if not JNI_AVAILABLE:
            self.label.text = "Transcript:\n" + self.transcript.strip()
            return

        # Defer the actual intent send to allow activity to become ready
        Clock.schedule_once(self._send_intent, 0.5)

    def _send_intent(self, dt):
        try:
            if not JNI_AVAILABLE:
                print("JNI not available")
                return

            # Try to import lazily in case classpath isn't set at module import time
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            Intent = autoclass('android.content.Intent')
            String = autoclass('java.lang.String')

            # Defensive: create intent but do not assume chooser or activity present
            intent = Intent(Intent.ACTION_SEND)
            intent.setType("text/plain")
            intent.putExtra(Intent.EXTRA_TEXT, String(self.transcript.strip()))
            # createChooser might return a valid Intent even if activity is not ready
            chooser = None
            try:
                chooser = Intent.createChooser(intent, String("Send to Notes"))
            except Exception:
                # fallback to direct intent
                chooser = intent

            # Obtain current activity and validate
            currentActivity = None
            try:
                currentActivity = cast('android.app.Activity', PythonActivity.mActivity)
            except Exception:
                currentActivity = None

            if currentActivity is None:
                # Try one more time after a short delay; avoid infinite retries
                print("Activity context was null when trying to send intent")
                self.label.text = "Activity not ready; try again"
                return

            # Final attempt to start activity
            try:
                currentActivity.startActivity(chooser)
            except Exception:
                # Some OEMs may throw; show transcript as fallback
                traceback.print_exc()
                self.label.text = "Unable to open chooser; showing transcript"
                self.label.text = "Transcript:\n" + self.transcript.strip()
        except Exception:
            traceback.print_exc()
            self.label.text = "Intent failed (see log)"

class VoiceNoteApp(App):
    def build(self):
        # Ensure recognizer initialization doesn't block UI build
        if VOSK_AVAILABLE:
            # Kick off recognizer init in background so first interaction is responsive
            threading.Thread(target=ensure_recognizer, daemon=True).start()
        return VoiceNoteLayout()

    def on_stop(self):
        # Ensure background threads are signalled to stop
        try:
            # Attempt to clear queue and stop any running record threads
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:
                    break
        except Exception:
            pass

if __name__ == '__main__':
    VoiceNoteApp().run()
