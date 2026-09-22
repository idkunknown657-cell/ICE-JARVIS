"""Single source of truth for the app version.

The publisher script (tools/publish_update.py) bumps this, the updater
(core/updater.py) compares it against GitHub Releases, and the HUD can show it
wherever it pleases. Keep it simple: major.minor.patch.
"""

APP_VERSION = "1.0.1"
APP_NAME    = "ICE"