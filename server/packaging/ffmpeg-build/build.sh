#!/usr/bin/env bash
# Build Station Server's own ffmpeg from the source fetch.sh got.
#
#   server/packaging/ffmpeg-build/build.sh SOURCE_DIR OUT_DIR
#
# Runs on a Mac (for that Mac's architecture, back to macOS 11), on
# Windows in an MSYS2 MINGW64 shell (one ffmpeg.exe, nothing else to
# install), and on Linux. OUT_DIR gets ffmpeg (or ffmpeg.exe) and
# LICENSES.txt, the licences of FFmpeg and LAME.
#
# It's FFmpeg with LAME for MP3, and only what Station Server asks of
# it: every decoder, demuxer and filter (any song, any live source),
# but only the encoders and outputs it uses (MP3 to broadcast, raw PCM
# to mix, WAV and lavfi for its tests), files and pipes, no network and
# no other libraries. Both are LGPL, and so is the result.
set -euo pipefail

src=$(cd "${1:?usage: build.sh SOURCE_DIR OUT_DIR}" && pwd)
mkdir -p "${2:?usage: build.sh SOURCE_DIR OUT_DIR}"
out=$(cd "$2" && pwd)
work=$(mktemp -d)
prefix="$work/prefix"
jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)

ffmpeg_src=$(ls "$src"/ffmpeg-*.tar.xz)
lame_src=$(ls "$src"/lame-*.tar.gz)

cflags="-O2"
ldflags=""
extra=()
exe=ffmpeg
case "$(uname -s)" in
  Darwin)
    # The oldest macOS Station Studio and Player run on.
    export MACOSX_DEPLOYMENT_TARGET=11.0
    cflags="$cflags -mmacosx-version-min=11.0"
    ldflags="-mmacosx-version-min=11.0"
    extra=(--cc=clang --enable-pthreads)
    ;;
  MINGW*|MSYS*)
    # One self-contained ffmpeg.exe: no libgcc or winpthread DLLs beside it.
    ldflags="-static"
    extra=(--enable-w32threads)
    exe=ffmpeg.exe
    ;;
  *)
    extra=(--enable-pthreads)
    ;;
esac

echo "--- LAME"
tar -xzf "$lame_src" -C "$work"
(
  cd "$work"/lame-*/
  # Newer compilers: LAME's configure trips over C23, and its code has
  # old-style declarations (as Homebrew builds it).
  ac_cv_prog_cc_c23=no CFLAGS="$cflags -Wno-implicit-function-declaration" LDFLAGS="$ldflags" \
    ./configure --prefix="$prefix" --disable-shared --enable-static \
      --disable-frontend --disable-gtktest --disable-dependency-tracking
  make -j"$jobs"
  make install
)

echo "--- FFmpeg"
tar -xJf "$ffmpeg_src" -C "$work"
(
  cd "$work"/ffmpeg-*/
  # --disable-autodetect: nothing from the build machine (system
  # frameworks, other libraries) ends up in it unasked; threads are
  # asked for above.
  ./configure --prefix="$prefix" \
    --extra-cflags="$cflags -I$prefix/include" --extra-ldflags="$ldflags -L$prefix/lib" \
    --pkg-config-flags=--static \
    --disable-shared --enable-static \
    --disable-autodetect --disable-doc --disable-debug --disable-network \
    --disable-programs --enable-ffmpeg \
    --enable-libmp3lame \
    --disable-encoders --enable-encoder=libmp3lame,pcm_s16le \
    --disable-muxers --enable-muxer=mp3,pcm_s16le,wav,null \
    --disable-devices --enable-indev=lavfi \
    --disable-hwaccels \
    --disable-protocols --enable-protocol=file,pipe \
    "${extra[@]}"
  make -j"$jobs"
  cp "$exe" "$out/$exe"
  strip "$out/$exe"
  {
    echo "FFmpeg: https://ffmpeg.org — GNU Lesser General Public License, version 2.1 or later"
    echo "========================================================================================"
    echo
    cat COPYING.LGPLv2.1
  } > "$work/ffmpeg-licence.txt"
)
{
  cat "$work/ffmpeg-licence.txt"
  echo
  echo "LAME: https://lame.sourceforge.io — GNU Library General Public License"
  echo "========================================================================"
  echo
  cat "$work"/lame-*/COPYING
} > "$out/LICENSES.txt"

# What Station Server needs, there. (Read whole first: grep -q stops
# reading at a match, and ffmpeg's SIGPIPE would fail the pipe.)
version=$("$out/$exe" -hide_banner -version)
echo "${version%%$'\n'*}"
encoders=$("$out/$exe" -hide_banner -encoders)
grep -q ' libmp3lame ' <<<"$encoders" || { echo "no MP3 encoder" >&2; exit 1; }
filters=$("$out/$exe" -hide_banner -filters)
for f in silencedetect ebur128 volume concat aresample; do
  grep -q " $f " <<<"$filters" || { echo "no $f filter" >&2; exit 1; }
done
rm -rf "$work"
