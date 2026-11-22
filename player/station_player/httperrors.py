"""An HTTP error from urllib is a response too: it holds its connection
open until it's closed. Code that catches one and carries on (most fetches
here: a 404 just means "not there") would leave that to the garbage
collector, which Python 3.13+ warns about as a leak.

`closing_errors` wraps an urlopen-like function so the errors it raises
come out already closed. Their code and reason are still there; their
body isn't, so code that wants to read one opens without it.
"""
from __future__ import annotations

import urllib.error
from typing import Callable


def closing_errors(open_: Callable) -> Callable:
    def opened(*args, **kwargs):
        try:
            return open_(*args, **kwargs)
        except urllib.error.HTTPError as exc:
            if exc.fp is not None:  # without a body, there's nothing to close
                exc.close()
            raise
    return opened
