# station-appkit

What [Station Studio](https://github.com/raby/station-studio), Station
Player and Station Server share as desktop apps, so each is written once:

- **The new-version check** (`station_appkit.updates`): asks GitHub, at
  most once a day, for the app's newest release (`studio-v*`,
  `player-v*`, `server-v*`). Only release names are fetched.
- **The self-updater** (`station_appkit.selfupdate`): Update now in the
  downloaded Mac and Windows apps. The new version's installer is used
  only when its size and SHA-256 match what GitHub publishes for that
  file, and only from the project's own releases.
- **The app window** (`station_appkit.window`): a Chrome, Edge or Brave
  app window with its own profile, or a browser tab; and on the Mac
  (`station_appkit.macwindow`, needs PyObjC) the downloaded app's own
  WebKit window, with its menus, Dock icon and downloads.

- **Start at login** (`station_appkit.login`): a LaunchAgent on the Mac,
  the Run key on Windows, an autostart entry on Linux; never from the
  disk image, and an entry left by another copy brought up to date.
- **The log file** (`station_appkit.applog`): beside the app's data, kept
  to a few megabytes.

Each app describes itself once (`station_appkit.App`: its name, release
tag, port and so on) and passes that in. Standard library only.

It isn't meant for use outside these apps; it's published so they can be
installed with pip or pipx. MIT licensed.
