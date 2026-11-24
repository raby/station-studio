# Which app do I need?

The Station suite is three apps, each installed and updated on its own.
**To listen, you only need Station Player.** The other two are for
making a radio station.

| I want to… | You need |
|---|---|
| Listen to internet radio and my own music | **Station Player** |
| Make my station's playlists, week by week, from my music | **Station Studio** |
| Put my station on air, on a SHOUTcast or Icecast server, with what's on now and next | **Station Server** (with Station Studio making its weeks) |
| Hear my station's weeks on my own computer before they go out | **Station Player** (with Station Studio) |

## The three apps

**Station Player** plays radio and music: thousands of stations to
find on a globe or by name, your music folders and playlists, alarms,
ambient scenes, and a remote on your phone. For station makers it also
plays Station Studio's weeks, records radio and keeps the play log for
PRS and PPL.
[Install Station Player](player/index.md#install-and-run) ·
[Downloads](https://github.com/raby/station-studio/releases?q=player&expanded=true)

**Station Studio** builds a station's playlists: you give it your music
as source pools and the rules (a jingle every few songs, no artist
twice within ten songs, a mix block of chosen genres), and it makes each week's
playlist, with why every song was picked.
[Install Station Studio](tutorial/index.md#1-install-2-min) ·
[Downloads](https://github.com/raby/station-studio/releases?q=studio&expanded=true)

**Station Server** puts the station on air: it plays each week in real
time to your SHOUTcast or Icecast server, takes presenters' live shows,
and publishes what's on now and next for your website and for Station
Player. It mostly runs in the background on the station's computer.
[Install Station Server](server/index.md#install) ·
[Downloads](https://github.com/raby/station-studio/releases?q=server&expanded=true)

## How they work together

- **Studio → Player:** a week Station Studio exports appears in Station
  Player by itself, on the same computer.
- **Studio → Server:** Station Studio sends each week to Station Server
  over the network, once they're linked with a link code (Studio's
  Settings › Station Server).
- **Server → Player:** Station Player shows what's on now and next on a
  station run by Station Server, from its feed, and can find its logo.

Each app's Help has **The other Station apps**, with a button to open
either of the others when it's installed on the same computer.
