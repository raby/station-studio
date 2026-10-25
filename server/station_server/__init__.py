"""Station Server — broadcasts station-studio playlists to SHOUTcast or Icecast.

Reads the iTunes XML playlists station-studio exports, plays them in order
in real time, encodes the result once and sends it to a streaming server
with each track's title. No sound card is involved, so nothing else
playing on the computer reaches the broadcast.
"""

__version__ = "0.1.0"
