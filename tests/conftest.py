"""Shared test fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

# Put the repo root on sys.path so `import engine` works without install
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
