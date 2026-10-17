"""What the downloaded Mac and Windows apps run (see station-player.spec)."""
import os
import sys

if sys.platform == "darwin":
    # The Python inside the app has no list of trusted certificates of its
    # own: use the one bundled with it, so https stations, Find stations and
    # the new-version check work.
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from station_player.__main__ import main

sys.exit(main())
