"""
modules/constants.py — Shared constants for TCC modules and handlers.

Single source of truth for Android app → package name mappings.
Imported by modules/phone.py (ADB mode) and referenced by agent/handlers/android.py (Termux mode).
"""

# Map common app names → Android package names (case-insensitive lookup)
APP_PACKAGES: dict = {
    "chrome":       "com.android.chrome",
    "youtube":      "com.google.android.youtube",
    "spotify":      "com.spotify.music",
    "whatsapp":     "com.whatsapp",
    "instagram":    "com.instagram.android",
    "twitter":      "com.twitter.android",
    "camera":       "com.android.camera2",
    "photos":       "com.google.android.apps.photos",
    "maps":         "com.google.android.apps.maps",
    "gmail":        "com.google.android.gm",
    "settings":     "com.android.settings",
    "calculator":   "com.android.calculator2",
    "files":        "com.google.android.apps.nbu.files",
    "clock":        "com.google.android.deskclock",
    "contacts":     "com.android.contacts",
    "messages":     "com.google.android.apps.messaging",
    "phone":        "com.google.android.dialer",
    "play":         "com.android.vending",
    "drive":        "com.google.android.apps.docs",
    "netflix":      "com.netflix.mediaclient",
    "telegram":     "org.telegram.messenger",
    "discord":      "com.discord",
}
