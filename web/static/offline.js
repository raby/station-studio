// Shown (by sw.js) when the window opens while Station Studio isn't
// running. Asks the computer to start it, through the station-studio:
// link the downloaded app registers, then opens the page once it's up.
const status = document.getElementById('offline-status');
const START = 'station-studio://start';

function start() {
  location.href = START;
}
document.getElementById('offline-start').addEventListener('click', start);

let tries = 0;
async function poll() {
  try {
    const res = await fetch('/api/stations', { cache: 'no-store' });
    // The address the window was opened on (a Dock menu item, say).
    if (res.ok) {
      location.replace(location.pathname + location.search);
      return;
    }
  } catch {
    /* not up yet */
  }
  tries += 1;
  if (tries === 15) {
    status.textContent =
      "Station Studio isn't running. Click Start, or open Station Studio from your Applications " +
      'folder or Start menu. This window will open it as soon as it\'s running.';
  }
  setTimeout(poll, 1000);
}

// After the page is up, so the browser's "Open Station Studio?" question
// doesn't hold it up.
addEventListener('load', () => setTimeout(start, 200));
poll();
