# Station Server's ffmpeg

The Mac and Windows downloads carry their own ffmpeg, built here from
[FFmpeg](https://ffmpeg.org) and [LAME](https://lame.sourceforge.io)
(for MP3), from their unchanged release archives. It has only what
Station Server asks of it:

- every decoder, demuxer and filter, so any song and any live source
  plays;
- MP3, raw PCM, WAV and null output (MP3 to broadcast, PCM to mix, the
  others for the tests);
- files and pipes, but no network and no other libraries.

FFmpeg and LAME are both LGPL, and so is this build. The app's
`ffmpeg/LICENSE.txt` has their licences and says where the source is.

| File | What it does |
| --- | --- |
| `sources.txt` | The archives it's built from, each pinned by its SHA-256. |
| `fetch.sh DIR` | Gets them into `DIR`: this repo's [`ffmpeg-source`](https://github.com/raby/station-studio/releases/tag/ffmpeg-source) release first, where they came from otherwise (checking FFmpeg's release signature). In CI, outside pull requests, it adds any it fetched to that release, so the source of every ffmpeg shipped stays in one place. |
| `build.sh SRC OUT` | Builds it: on a Mac for that Mac's architecture (back to macOS 11), on Windows in an MSYS2 UCRT64 shell (one self-contained `ffmpeg.exe`), or on Linux. A stage that fails says why, as an annotation in CI. |

`.github/workflows/server-installers.yml` runs both, caches the result
until `build.sh` or `sources.txt` changes, and runs Station Server's
tests against it before making the app.

## Moving to a newer FFmpeg or LAME

1. In `sources.txt`, change the file name and URLs, and put `-` for its
   checksum.
2. Push. The *ffmpeg source* job checks FFmpeg's signature, then
   reports the archive's SHA-256 and stops.
3. Put that checksum in `sources.txt` and push again. Each platform
   builds the new one and runs the tests against it.

LAME has no signature: check its checksum against another source (such
as Homebrew's formula) before pinning it.
