# Ambient recordings

Ambient mode plays a real recording for a scene when `sounds.json` names
one, and makes the scene's sound itself otherwise (the fish tank has none).
The recordings here are CC0 from Freesound; `source` in `sounds.json` is
each one's page. They were loudness-matched (ffmpeg `loudnorm=I=-22`) and
made 112 kbit/s stereo MP3s, which every browser and the Mac app can play. Only free-licence
recordings go here (CC0 or similar), each listed with where it came from:

```json
{
  "waves": {
    "file": "waves.mp3",
    "title": "Waves on a shingle beach",
    "by": "Someone",
    "licence": "CC0",
    "source": "https://example.org/the-page-it-came-from"
  }
}
```

The scene keys are `waves`, `rain`, `stream`, `birds`, `night`, `fire` and
`fish`. MP3 or Ogg, one to three minutes; the player crossfades each time
round, so the loop needn't be cut perfectly.
