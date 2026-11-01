#!/usr/bin/env bash
# Get the source Station Server's ffmpeg is built from (sources.txt) into
# DIR, each checked against its pinned SHA-256.
#
#   server/packaging/ffmpeg-build/fetch.sh DIR
#
# This repo's ffmpeg-source release first; where the file came from
# otherwise (and ffmpeg's release signature is checked too). With
# KEEP=1 (CI, not for pull requests), a file that wasn't in the release
# yet is added to it, so the source of every ffmpeg we ship stays
# downloadable from one place.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
dir=${1:?usage: fetch.sh DIR}
repo=${GITHUB_REPOSITORY:-raby/station-studio}
release=ffmpeg-source
# FFmpeg's release signing key (https://ffmpeg.org/download.html).
ffmpeg_key=FCF986EA15E6E293A5644F10B4322F04D67658D8
mkdir -p "$dir"

note() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::$1 title=$2::$3"; else echo "$2: $3" >&2; fi; }

# Our copy of $1, if the release has it. 1 when it doesn't (or there's no
# release yet); anything else is tried again before giving up on it.
from_release() {
  local name=$1 code url
  for _ in 1 2 3; do
    code=$(curl -sS -o "$dir/.release.json" -w '%{http_code}' ${GH_TOKEN:+-H "Authorization: Bearer $GH_TOKEN"} \
      "https://api.github.com/repos/$repo/releases/tags/$release") || code=000
    [ "$code" = 404 ] && return 1
    if [ "$code" = 200 ]; then
      url=$(jq -r --arg n "$name" '.assets[] | select(.name == $n) | .browser_download_url' "$dir/.release.json")
      [ -z "$url" ] && return 1
      curl -fsSL --retry 3 -o "$dir/$name" "$url" && return 0
    fi
    echo "couldn't get $name from the $release release (HTTP $code); trying again" >&2
    sleep 10
  done
  return 1
}

# The file's signature is FFmpeg's own release key's.
check_signature() {
  local file=$1 url=$2 home
  home=$(mktemp -d)
  curl -fsSL --retry 3 -o "$file.asc" "$url"
  curl -fsSL --retry 3 https://ffmpeg.org/ffmpeg-devel.asc | GNUPGHOME=$home gpg --batch --quiet --import
  if ! GNUPGHOME=$home gpg --batch --status-fd 1 --verify "$file.asc" "$file" 2>/dev/null \
      | grep -Eq "^\[GNUPG:\] VALIDSIG .*$ffmpeg_key\$"; then
    note error "signature" "$(basename "$file") isn't signed with FFmpeg's release key"
    exit 1
  fi
  rm -rf "$home" "$file.asc"
}

grep -v '^#' "$here/sources.txt" | while read -r name want url sig; do
  [ -n "$name" ] || continue
  from=release
  if ! from_release "$name"; then
    from=upstream
    curl -fsSL --retry 3 -o "$dir/$name" "$url"
    [ "$sig" != - ] && check_signature "$dir/$name" "$sig"
  fi
  got=$(shasum -a 256 "$dir/$name" 2>/dev/null || sha256sum "$dir/$name")
  got=${got%% *}
  if [ "$want" = - ]; then
    note error "pin $name" "its sha256 is $got (from $from); put that in sources.txt"
    exit 1
  fi
  if [ "$got" != "$want" ]; then
    note error "checksum" "$name (from $from) isn't the pinned one: $got"
    exit 1
  fi
  note notice "$name" "sha256 $got, from $from"
  if [ "$from" = upstream ] && [ "${KEEP:-}" = 1 ]; then
    # Two runs may get here at once: one makes the release.
    gh release create "$release" --repo "$repo" --target main --prerelease --latest=false \
      --title "Source of Station Server's ffmpeg" \
      --notes "Not a Station Server release, and please don't delete it: Station Server's downloads carry an ffmpeg built from these files (server/packaging/ffmpeg-build), and the copy of its licence in the app says its source is here. FFmpeg and LAME are licensed under the GNU LGPL." \
      || gh release view "$release" --repo "$repo" >/dev/null
    gh release upload "$release" "$dir/$name" --repo "$repo" --clobber
  fi
done
rm -f "$dir/.release.json"
