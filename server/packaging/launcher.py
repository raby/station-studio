"""What the downloaded Mac and Windows apps run (see station-server.spec)."""
import os
import sys

if sys.platform == "darwin":
    # The Python inside the app has no list of trusted certificates of its
    # own: use the one bundled with it, so the new-version check and Update
    # now can reach GitHub over https. (Windows uses its own list.)
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from station_server.__main__ import main

sys.exit(main())
