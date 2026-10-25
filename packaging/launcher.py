"""What the downloaded Mac and Windows apps run (see station-studio.spec)."""
import os
import sys

if sys.platform == "darwin":
    # The Python inside the app has no list of trusted certificates of its
    # own: use the one bundled with it, so the new-version check and
    # updates can reach GitHub over https.
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from station_studio.desktop import run

sys.exit(run())
