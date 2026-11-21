"""docker-compose.yml publishes Station Studio on this computer only.

Studio has no login: published on every address, anyone on the same
network could change or delete stations. Opening it up is a choice the
compose file explains (publish on every address and list the names in
STATION_STUDIO_ALLOWED_HOSTS), not the default. Read without a YAML
library, which only the docs tools bring.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def published_ports() -> list[str]:
    """The entries under `ports:`, comments left out."""
    block = re.search(r"^    ports:\n((?:      .*\n|\s*#.*\n)+)", COMPOSE, re.M)
    assert block, "docker-compose.yml has no ports: block"
    return re.findall(r'^\s*-\s*"([^"]+)"', block.group(1), re.M)


def test_studio_is_published_on_this_computer_only():
    assert published_ports() == ["127.0.0.1:8001:8001"]


def test_it_says_how_to_open_it_to_other_computers():
    assert '#      - "8001:8001"' in COMPOSE
    assert "#   STATION_STUDIO_ALLOWED_HOSTS:" in COMPOSE


def test_the_smoke_test_publishes_on_this_computer_only():
    makefile = (ROOT / "Makefile").read_text()
    runs = re.findall(r"docker run -d (-p \S+)", makefile)
    assert runs == ["-p 127.0.0.1:18001:8001"]
