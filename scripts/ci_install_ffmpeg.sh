#!/usr/bin/env bash
# CI: a static ffmpeg on PATH, for Station Server's end-to-end tests.
# From BtbN/FFmpeg-Builds (as in the Windows app), not apt, whose mirror
# has hung these jobs for half an hour at a time. Needs GH_TOKEN, and
# GITHUB_PATH/RUNNER_TEMP from GitHub Actions.
set -euo pipefail
dir="$RUNNER_TEMP/ffmpeg"
mkdir -p "$dir"
# BtbN rebuilds its "latest" release now and then, and it's missing for
# a few minutes while it does: try again rather than fail.
for try in 1 2 3 4 5 6 7 8; do
  gh release download latest --repo BtbN/FFmpeg-Builds --clobber \
    --pattern 'ffmpeg-master-latest-linux64-lgpl.tar.xz' --dir "$dir" && break
  echo "BtbN's latest ffmpeg isn't there just now; trying again in 30 s"
  sleep 30
done
tar -xf "$dir/ffmpeg-master-latest-linux64-lgpl.tar.xz" -C "$dir"
echo "$dir/ffmpeg-master-latest-linux64-lgpl/bin" >> "$GITHUB_PATH"
"$dir/ffmpeg-master-latest-linux64-lgpl/bin/ffmpeg" -hide_banner -encoders | grep -q libmp3lame
