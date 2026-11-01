"""station-studio — open-source playlist generator for hobbyist radio stations.

This top-level package is the wheel's CLI entry point (§16.13). The
heavy lifting lives in sibling top-level packages: `api` (FastAPI
routers) and `engine` (the playlist generator). `station_studio.cli`
orchestrates both behind the `station-studio` console script.
"""

__version__ = "1.15.0"
