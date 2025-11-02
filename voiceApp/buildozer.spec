[app]
title = Voice Note
package.name = voicenote
package.domain = org.peter
source.dir = .
source.include_exts = py,kv,txt,json,zip
version = 1.0
requirements = kivy, vosk, sounddevice, pyjnius
orientation = landscape
fullscreen = 1
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.permissions = RECORD_AUDIO, WRITE_EXTERNAL_STORAGE
android.allow_backup = False
android.logcat_filters = *:S python:D
android.entrypoint = main.py

[buildozer]
log_level = 2
warn_on_root = 1
