import re

import pytest

from station_player import qr

URL = "http://192.168.1.20:8766/r/abcdef0123456789abcdef"

# qr.matrix(URL): version 4-M, cross-checked module-for-module against
# Nayuki's qrcodegen and decoded with zxing-cpp when this module was written.
URL_MATRIX = [
    "#######..##.....#..#.####.#######",
    "#.....#........#..#.....#.#.....#",
    "#.###.#.#.###.#......#.#..#.###.#",
    "#.###.#.##..#..#..##..#.#.#.###.#",
    "#.###.#.###...###...#..#..#.###.#",
    "#.....#.##.#.#...#.#.#....#.....#",
    "#######.#.#.#.#.#.#.#.#.#.#######",
    "........##....#.#..###..#........",
    "#.#####...#..##..####.....#####..",
    "..#.#....##..#..##.#.#.#####.####",
    "##....####.#.#####.......#..#.#..",
    "#..###..#.####.#.###.##.##..###.#",
    "####..#.##.##.###..##.#.#...##.#.",
    "######..##...#.#..#....#.###..###",
    "....###....#######..#.#....#..##.",
    "####.#.#.##.#.##..#..#..##....#..",
    "#.#..##....#####.##.#..#...###..#",
    "..#..#...##.....#..#.#######.##.#",
    "#....##...#..#.##.#..##.##..#.##.",
    "###.#...#..####.#....#.#..#.####.",
    "##.##.#..####.....#...##....##.#.",
    "####.#..######.##...##.#####..#.#",
    "#.#.####.#.#..#..#.#.#..#.#.####.",
    "#..###.#...#.##.#.#####.##.#..##.",
    "#....##...#.#....####..#######...",
    "........#######..###.#..#...#.#.#",
    "#######...#.#.##.#...####.#.#.##.",
    "#.....#.##.##.#..###.##.#...#.#..",
    "#.###.#.####.#.##.....#.######.#.",
    "#.###.#.##.#...#.##........##.###",
    "#.###.#.##....####....#.####.....",
    "#.....#....##..#...###.#.#.##.#..",
    "#######.##.#.#####..#...######.#.",
]

# Byte-mode capacity at level M for versions 1-10.
CAPACITY_M = [14, 26, 42, 62, 84, 106, 122, 152, 180, 213]

SAMPLES = [
    "", "a", URL, "https://example.com/" + "x" * 130,
    "Tëst Ünïcode — 日本語 🎵 radio", "é" * 75, "z" * 213, "q" * 600,
]


def as_text(m):
    return ["".join("#" if d else "." for d in row) for row in m]


def bits(m, coords):
    return int("".join("1" if m[r][c] else "0" for r, c in coords), 2)


def format_info(m):
    """(EC level bits, mask) from each of the two format-info copies."""
    size = len(m)
    first = [(8, c) for c in (0, 1, 2, 3, 4, 5, 7, 8)] + [(r, 8) for r in (7, 5, 4, 3, 2, 1, 0)]
    second = [(r, 8) for r in range(size - 1, size - 8, -1)] + [(8, c) for c in range(size - 8, size)]
    out = []
    for coords in (first, second):
        word = bits(m, coords) ^ 0x5412
        data = word >> 10
        assert word == qr._format_bits(data & 7) ^ 0x5412  # BCH checks out
        out.append((data >> 3, data & 7))
    return out


def test_known_good_matrix():
    assert as_text(qr.matrix(URL)) == URL_MATRIX


@pytest.mark.parametrize("version, cap", list(enumerate(CAPACITY_M, start=1)))
def test_smallest_version_that_fits(version, cap):
    assert len(qr.matrix("x" * cap)) == 17 + 4 * version
    assert len(qr.matrix("x" * (cap + 1))) == 17 + 4 * (version + 1)


def test_counts_utf8_bytes():
    assert len(qr.matrix("é" * 7)) == 21  # 14 bytes: still version 1
    assert len(qr.matrix("é" * 8)) == 25


def test_too_long():
    assert len(qr.matrix("x" * 2331)) == 177  # version 40
    with pytest.raises(ValueError):
        qr.matrix("x" * 2332)


@pytest.mark.parametrize("text", SAMPLES)
def test_structure(text):
    m = qr.matrix(text)
    size = len(m)
    version = (size - 17) // 4
    assert size == 17 + 4 * version and all(len(row) == size for row in m)
    # Finder patterns with their light separators.
    finder = ["#######", "#.....#", "#.###.#", "#.###.#", "#.###.#", "#.....#", "#######"]
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        assert [row[c0:c0 + 7] for row in as_text(m)[r0:r0 + 7]] == finder
    for i in range(8):
        assert not m[7][i] and not m[i][7] and not m[7][size - 1 - i] and not m[size - 8][i]
    # Timing patterns and the always-dark module.
    for i in range(8, size - 8):
        assert m[6][i] == m[i][6] == (i % 2 == 0)
    assert m[size - 8][8]
    # Both format copies agree: level M (00) and some mask.
    (level, mask), second = format_info(m)
    assert level == 0b00 and 0 <= mask < 8 and second == (level, mask)
    # Version info (both copies) from version 7.
    if version >= 7:
        top = [(r, c) for r in range(5, -1, -1) for c in range(size - 9, size - 12, -1)]
        left = [(c, r) for r, c in top]
        assert bits(m, top) == bits(m, left) == qr._version_bits(version)


def test_version_info_known_value():
    assert qr._version_bits(7) == 0x07C94
    assert qr._version_bits(10) == 0x0A4D3


def test_format_info_known_values():
    # Level M, masks 0-7, after masking with 0x5412 (ISO/IEC 18004 Table C.1).
    expected = ["101010000010010", "101000100100101", "101111001111100", "101101101001011",
                "100010111111001", "100000011001110", "100111110010111", "100101010100000"]
    assert [f"{qr._format_bits(k):015b}" for k in range(8)] == expected


def test_reed_solomon():
    # ISO/IEC 18004 Annex I example: "01234567" as version 1-M.
    data = bytes([0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11,
                  0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11])
    assert qr._rs_ecc(data, 10) == [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55]


def test_best_mask_has_lowest_penalty():
    data = URL.encode()
    scores = [qr._penalty(qr._encode(data, 4, k)) for k in range(8)]
    assert qr.matrix(URL) == qr._encode(data, 4, scores.index(min(scores)))


def test_svg():
    out = qr.svg(URL)
    m = qr.matrix(URL)
    size = len(m) + 8
    assert out.startswith("<svg ") and out.endswith("</svg>")
    assert f'viewBox="0 0 {size} {size}"' in out
    assert 'shape-rendering="crispEdges"' in out
    assert "style" not in out
    assert out.count("<path") == 1 and out.count("<rect") == 1
    # The path is exactly the dark modules, offset by the border.
    cells = {(int(y) - 4, int(x) - 4) for x, y in re.findall(r"M(\d+),(\d+)", out)}
    assert cells == {(r, c) for r, row in enumerate(m) for c, d in enumerate(row) if d}
    assert 'viewBox="0 0 33 33"' in qr.svg(URL, border=0)
