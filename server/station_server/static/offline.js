"use strict";
// Shown (by sw.js) when the window opens while Station Server isn't
// running. Asks the computer to start it, through the station-server:
// link the downloaded app registers, then opens the page once it's up.
const status = document.getElementById("offline-status");
const START = "station-server://start";

function start() { location.href = START; }
document.getElementById("offline-start").addEventListener("click", start);

let tries = 0;
async function poll() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    // Keep ?view=… from the Dock menu, so that page opens once it's up.
    if (res.ok) { location.replace("/" + location.search); return; }
  } catch { /* not up yet */ }
  tries += 1;
  if (tries === 15) {
    status.textContent = "Station Server isn't running. Click Start, or open Station Server " +
      "from your Applications folder or Start menu. This window will open it as soon as it's running.";
  }
  setTimeout(poll, 1000);
}

// After the page is up, so the browser's "Open Station Server?" question
// doesn't hold it up.
addEventListener("load", () => setTimeout(start, 200));
poll();
