"""The page's script: static/js/'s parts, served joined as /app.js, with a
source map back to them."""
import json
import re

from station_player import pagescript
from test_server import request, running  # noqa: F401 (a fixture)

_B64 = {c: i for i, c in enumerate(pagescript._B64)}


def decode_vlq(text):
    values, value, shift = [], 0, 0
    for c in text:
        digit = _B64[c]
        value += (digit & 31) << shift
        if digit & 32:
            shift += 5
            continue
        values.append(-(value >> 1) if value & 1 else value >> 1)
        value, shift = 0, 0
    return values


def mapped_lines(source_map):
    """[(source path, its line)] for each line of the script, from the map."""
    out, source, line = [], 0, 0
    for segment in source_map["mappings"].split(";"):
        if not segment:
            out.append(None)
            continue
        col, d_source, d_line, d_col = decode_vlq(segment)
        assert (col, d_col) == (0, 0)
        source, line = source + d_source, line + d_line
        out.append((source_map["sources"][source], line))
    return out


def test_every_part_is_in_the_page_and_nothing_else_is():
    on_disk = {p.stem for p in pagescript.JS_DIR.glob("*.js")}
    assert on_disk == set(pagescript.PARTS)
    assert len(pagescript.PARTS) == len(set(pagescript.PARTS))


def test_parts_join_into_one_strict_script():
    texts = [(pagescript.JS_DIR / f"{name}.js").read_text(encoding="utf-8") for name in pagescript.PARTS]
    assert all(t.endswith("\n") for t in texts)
    assert texts[0].startswith('"use strict";\n')
    # Only the first line of the whole script can make it strict.
    assert not any(re.match(r'\s*(//[^\n]*\n\s*)*["\']use strict', t) for t in texts[1:])
    # Each part starts at one of the script's section headings.
    assert all(t.startswith("// ---------- ") for t in texts[1:])
    script = pagescript.script().decode("utf-8")
    assert script == "".join(texts) + "//# sourceMappingURL=/app.js.map\n"


def test_source_map_points_each_line_at_its_part():
    script = pagescript.script().decode("utf-8").split("\n")[:-1]  # the last line ends with \n
    source_map = json.loads(pagescript.source_map())
    assert source_map["version"] == 3 and source_map["file"] == "app.js"
    assert source_map["sources"] == [f"js/{name}.js" for name in pagescript.PARTS]
    lines = mapped_lines(source_map)
    assert len(lines) == len(script) - 1  # all but the sourceMappingURL line
    parts = {path: (pagescript.JS_DIR / path[3:]).read_text(encoding="utf-8").split("\n")
             for path in source_map["sources"]}
    for generated, (path, line) in zip(script, lines):
        assert parts[path][line] == generated
    # Somewhere well into the script, as a browser's error would name it.
    at = next(i for i, text in enumerate(script) if text.startswith("// ---------- boot"))
    assert lines[at] == ("js/boot.js", 0)


def test_a_part_without_a_last_newline_still_maps(tmp_path, monkeypatch):
    monkeypatch.setattr(pagescript, "PARTS", ("a", "b"))
    (tmp_path / "a.js").write_text('"use strict";\nlet a = 1;', encoding="utf-8")
    (tmp_path / "b.js").write_text("a += 1;\n", encoding="utf-8")
    assert pagescript.script(tmp_path) == b'"use strict";\nlet a = 1;\na += 1;\n//# sourceMappingURL=/app.js.map\n'
    source_map = json.loads(pagescript.source_map(tmp_path))
    assert mapped_lines(source_map) == [("js/a.js", 0), ("js/a.js", 1), ("js/b.js", 0)]


def test_vlq_round_trips():
    for value in (0, 1, -1, 15, -16, 16, 31, 32, -1000, 123456):
        assert decode_vlq(pagescript._vlq(value)) == [value]


def test_served_joined_with_its_map(running):  # noqa: F811
    _, port, _, _ = running
    resp, body = request(port, "GET", "/app.js")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert resp.getheader("Cache-Control") == "no-cache"
    assert resp.getheader("Content-Security-Policy") is None
    assert int(resp.getheader("Content-Length")) == len(pagescript.script())
    resp, body = request(port, "GET", "/app.js.map")
    assert resp.status == 200 and resp.getheader("Content-Type") == "application/json"
    assert resp.getheader("Content-Length") == str(len(pagescript.source_map()))
    resp, _ = request(port, "GET", "/js/boot.js")  # where the map sends the browser
    assert resp.status == 200 and resp.getheader("Content-Type") == "text/javascript; charset=utf-8"
    resp, _ = request(port, "HEAD", "/app.js")
    assert resp.status == 200 and int(resp.getheader("Content-Length")) == len(pagescript.script())


def test_a_missing_part_is_an_error_not_half_a_page(running, tmp_path, monkeypatch):  # noqa: F811
    _, port, _, _ = running
    monkeypatch.setattr(pagescript, "JS_DIR", tmp_path / "nothing")
    resp, body = request(port, "GET", "/app.js")
    assert resp.status == 500
    assert b"Part of the page is missing" in body
