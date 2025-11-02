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
import os

from vosk import Model, KaldiRecognizer
from jnius import autoclass, cast

# Set landscape-friendly dimensions
Window.size = (800, 480)

q = queue.Queue()
model = Model(lang="en-us")
rec = KaldiRecognizer(model, 16000)

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

    def toggle_recording(self, instance):
        if not self.recording:
            self.label.text = "Listening..."
            self.button.text = "Stop Recording"
            self.recording = True
            threading.Thread(target=self.record_audio).start()
        else:
            self.recording = False
            self.label.text = "Processing..."
            self.button.text = "Start Recording"
            Clock.schedule_once(lambda dt: self.launch_notes(), 1)

    def record_audio(self):
        def callback(indata, frames, time, status):
            if status:
                print(status)
            q.put(bytes(indata))

        with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype='int16',
                               channels=1, callback=callback):
            while self.recording:
                data = q.get()
                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    self.transcript += result.get("text", "") + " "

    def launch_notes(self):
        self.label.text = "Transcribed:\n" + self.transcript.strip()
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        Intent = autoclass('android.content.Intent')
        String = autoclass('java.lang.String')

        intent = Intent(Intent.ACTION_SEND)
        intent.setType("text/plain")
        intent.putExtra(Intent.EXTRA_TEXT, String(self.transcript.strip()))
        chooser = Intent.createChooser(intent, String("Send to Notes"))
        currentActivity = cast('android.app.Activity', PythonActivity.mActivity)
        currentActivity.startActivity(chooser)

class VoiceNoteApp(App):
    def build(self):
        return VoiceNoteLayout()

if __name__ == '__main__':
    VoiceNoteApp().run()
