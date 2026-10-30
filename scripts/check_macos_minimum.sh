#!/usr/bin/env bash
# Fail if anything in a built Mac app needs a newer macOS than we support.
#
#   scripts/check_macos_minimum.sh "build/dist/Station Studio.app" [11.0]
#
# Without a version, the app's own LSMinimumSystemVersion (what it tells
# macOS it runs on) is the limit, so the promise and the check can't drift
# apart. Each app says what it needs (Studio and Player macOS 11, Server
# macOS 12 for its ffmpeg), but each
# executable and library carries its own minimum (LC_BUILD_VERSION
# minos, or LC_VERSION_MIN_MACOSX on older binaries), set by whatever
# built it: the runner's SDK, a wheel, a downloaded ffmpeg. Building on
# a newer runner can quietly raise it, and the app then won't start on
# older Macs. This reads every Mach-O file in the bundle with otool and
# lists any whose minimum is above the one given.
set -euo pipefail

app="${1:?usage: check_macos_minimum.sh APP_BUNDLE [MIN_VERSION]}"
min="${2:-$(plutil -extract LSMinimumSystemVersion raw "$app/Contents/Info.plist")}"

# "11.0" → 110000, "10.13" → 101300, for comparing.
as_number() {
  local IFS=.
  local -a v=($1)
  printf '%d%02d%02d\n' "${v[0]:-0}" "${v[1]:-0}" "${v[2]:-0}"
}
limit=$(as_number "$min")

checked=0
too_new=0
while IFS= read -r -d '' f; do
  file -b "$f" | grep -q 'Mach-O' || continue
  checked=$((checked + 1))
  # Every architecture slice's minimum (a universal binary has several).
  mins=$(otool -l "$f" 2>/dev/null | awk '
    /cmd LC_BUILD_VERSION/ { want = "minos" }
    /cmd LC_VERSION_MIN_MACOSX/ { want = "version" }
    want != "" && $1 == want { print $2; want = "" }
  ')
  for m in $mins; do
    if [ "$(as_number "$m")" -gt "$limit" ]; then
      echo "needs macOS $m: ${f#"$app"/}"
      # On GitHub Actions, also as an annotation on the run.
      [ -n "${GITHUB_ACTIONS:-}" ] && echo "::error title=Needs macOS $m::$(basename "$app")/${f#"$app"/}"
      too_new=$((too_new + 1))
    fi
  done
done < <(find "$app" -type f -print0)

echo "checked $checked Mach-O files in $(basename "$app") against macOS $min"
if [ "$too_new" -gt 0 ]; then
  echo "$too_new would stop the app starting on macOS $min" >&2
  [ -n "${GITHUB_ACTIONS:-}" ] && echo "::error title=macOS $min check::$too_new files in $(basename "$app") need a newer macOS"
  exit 1
fi
