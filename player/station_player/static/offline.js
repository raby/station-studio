// Shown (by sw.js) when the window opens while the player isn't running.
// Asks the computer to start it, through the station-player: link the
// Station Player shortcut registers, then opens the player once it's up.
const status = document.getElementById("offline-status");
const START = "station-player://start";

function start() { location.href = START; }
document.getElementById("offline-start").addEventListener("click", start);

let tries = 0;
async function poll() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    // Keep ?do=… from the Dock menu, so the player does it once it's up.
    if (res.ok) { location.replace("/" + location.search); return; }
  } catch { /* not up yet */ }
  tries += 1;
  if (tries === 15) {
    status.textContent = "Station Player isn't running. Click Start, or open Station Player " +
      "from your Applications folder or Start menu. This window will open it as soon as it's running.";
  }
  setTimeout(poll, 1000);
}

// After the page is up, so the browser's "Open Station Player?" question
// doesn't hold it up.
addEventListener("load", () => setTimeout(start, 200));
poll();
