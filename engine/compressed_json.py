"""A JSON column stored compressed: `PlaylistVersion.slots`.

A week's trace slots are mostly the candidates each rule passed over
(`excluded`), so they're big and repetitive: on Rebirth Radio one
version is about 16.7 MB as JSON, and about 0.5 MB compressed. Every
generation, rollback and just-this-week change (engine.week_changes)
adds a version, so this is most of the database's size.

Stored as a JSON string `"z1:<base64 of zlib-compressed JSON>"`, so the
column is still JSON in the schema (no migration of its type) and rows
written before this type stay readable as they are: a value that isn't
such a string is returned unchanged. The migration that introduced it
(5b7c9e1f3a20) unpacks every row on downgrade, for older code.
"""
from __future__ import annotations

import base64
import json
import zlib

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

PREFIX = "z1:"
#: zlib level 6: ~34x smaller than the JSON on Rebirth Radio, ~50 ms to
#: pack and ~7 ms to unpack a 16 MB week.
LEVEL = 6


def pack(value) -> str:
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return PREFIX + base64.b64encode(zlib.compress(raw, LEVEL)).decode("ascii")


def unpack(value):
    """`value` as stored (a packed string, or JSON written before) → the
    JSON value."""
    if isinstance(value, str) and value.startswith(PREFIX):
        return json.loads(zlib.decompress(base64.b64decode(value[len(PREFIX):])))
    return value


class CompressedJSON(TypeDecorator):
    """JSON stored compressed (see the module docstring). None is SQL
    NULL, as `JSON(none_as_null=True)`."""
    impl = JSON(none_as_null=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return pack(value)

    def process_result_value(self, value, dialect):
        return unpack(value)
