[app]

# ---- Identity ----
title = KiraVault
package.name = kiravault
package.domain = com.kiravault
version = 1.0.0

# ---- Source ----
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,ttf
source.exclude_exts = spec,log,md,txt,bak,pyc
source.exclude_dirs = tests,bin,venv,.venv,.buildozer,__pycache__,backups,.git,.github,.idea,__MACOSX
source.exclude_patterns = license,LICENSE,*.bak,*.log,*.pyc,.DS_Store

# ---- Look and feel ----
icon.filename = %(source.dir)s/my_icon.png
orientation = portrait
fullscreen = 0

# ---- Python requirements ----
# python3, kivy, pyjnius, plyer (notifications), android (permissions)
# sqlite3 is required: db.py and workdb.py use it
requirements = python3,kivy==2.3.0,pyjnius,plyer,android,sqlite3

# ---- Android target ----
android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.archs = arm64-v8a
android.accept_sdk_license = True

# ---- Permissions ----
# POST_NOTIFICATIONS: bill/commitment reminders (Android 13+)
# WRITE/READ_EXTERNAL_STORAGE: backups and CSV/JSON export on older Android
# (Android 10+ backups go through MediaStore into Downloads/KiraVault)
android.permissions = POST_NOTIFICATIONS, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE

# ---- Packaging ----
android.allow_backup = True
android.debug_artifact = apk

[buildozer]
log_level = 2
warn_on_root = 1