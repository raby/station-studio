"""Minimal QR code encoder, so the UI can show a link a phone camera scans.

Byte mode (UTF-8), error correction level M, the smallest version that
fits (1-40), and the best of the 8 masks by the standard penalty rules.
Follows ISO/IEC 18004; no third-party dependencies.
"""
from __future__ import annotations

from itertools import groupby

# Per version 1..40 at level M: EC codewords per block, number of blocks.
_ECC_PER_BLOCK = (10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
                  26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28)
_NUM_BLOCKS = (1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
               17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49)
_EC_LEVEL_M = 0b00  # format-info bits for level M
MAX_VERSION = 40

Matrix = list[list[bool]]

# GF(256) with the QR polynomial x^8 + x^4 + x^3 + x^2 + 1.
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x = (_x << 1) ^ (0x11D if _x & 0x80 else 0)
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_ecc(data: bytes, n: int) -> list[int]:
    """Reed-Solomon remainder: n EC codewords for data."""
    gen = [1]  # product of (x - a^i), i < n; highest coefficient first
    for i in range(n):
        gen = [a ^ _gf_mul(b, _EXP[i]) for a, b in zip(gen + [0], [0] + gen)]
    rem = [0] * n
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for j in range(n):
            rem[j] ^= _gf_mul(gen[j + 1], factor)
    return rem


def _raw_codewords(version: int) -> int:
    """Codewords (data + EC) that fit in a symbol of this version."""
    bits = (16 * version + 128) * version + 64
    if version >= 2:
        n = version // 7 + 2
        bits -= (25 * n - 10) * n - 55
        if version >= 7:
            bits -= 36
    return bits // 8


def _data_codewords(version: int) -> int:
    v = version - 1
    return _raw_codewords(version) - _ECC_PER_BLOCK[v] * _NUM_BLOCKS[v]


def _alignment_positions(version: int) -> list[int]:
    if version == 1:
        return []
    n = version // 7 + 2
    step = (version * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    last = version * 4 + 10
    return [6] + [last - i * step for i in range(n - 2, -1, -1)]


def _codewords(data: bytes, version: int) -> list[int]:
    """Segment bits, padding, RS blocks, interleaved."""
    count_bits = 8 if version < 10 else 16
    capacity = _data_codewords(version) * 8
    bits = [0, 1, 0, 0]  # byte mode
    bits += [(len(data) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, capacity - len(bits))  # terminator
    bits += [0] * (-len(bits) % 8)
    words = bytearray(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))
    pad = 0xEC
    while len(words) < capacity // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    # Split into blocks; the later ones carry one extra data codeword.
    n_blocks, ecc_len = _NUM_BLOCKS[version - 1], _ECC_PER_BLOCK[version - 1]
    raw = _raw_codewords(version)
    n_short, short_len = n_blocks - raw % n_blocks, raw // n_blocks
    blocks, eccs, pos = [], [], 0
    for b in range(n_blocks):
        size = short_len - ecc_len + (b >= n_short)
        block = bytes(words[pos:pos + size])
        pos += size
        blocks.append(block)
        eccs.append(_rs_ecc(block, ecc_len))
    out = [b[i] for i in range(short_len - ecc_len + 1) for b in blocks if i < len(b)]
    out += [e[i] for i in range(ecc_len) for e in eccs]
    return out


def _format_bits(mask: int) -> int:
    data = (_EC_LEVEL_M << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _version_bits(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | rem


_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: r * c % 2 + r * c % 3 == 0,
    lambda r, c: (r * c % 2 + r * c % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + r * c % 3) % 2 == 0,
)


class _Symbol:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = size = version * 4 + 17
        self.dark = [[False] * size for _ in range(size)]
        self.fixed = [[False] * size for _ in range(size)]  # function modules
        self._draw_function_patterns()

    def set(self, r: int, c: int, dark: bool) -> None:
        self.dark[r][c] = dark
        self.fixed[r][c] = True

    def _draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):  # timing patterns
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for r0, c0 in ((3, 3), (3, size - 4), (size - 4, 3)):  # finders + separators
            for dr in range(-4, 5):
                for dc in range(-4, 5):
                    r, c = r0 + dr, c0 + dc
                    if 0 <= r < size and 0 <= c < size:
                        d = max(abs(dr), abs(dc))
                        self.set(r, c, d not in (2, 4))
        pos = _alignment_positions(self.version)
        for r0 in pos:
            for c0 in pos:
                if (r0, c0) in ((6, 6), (6, pos[-1]), (pos[-1], 6)):
                    continue  # would overlap a finder
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        self.set(r0 + dr, c0 + dc, max(abs(dr), abs(dc)) != 1)
        self.draw_format(0)  # reserves the area; redrawn once the mask is known
        if self.version >= 7:
            bits = _version_bits(self.version)
            for i in range(18):
                bit = bool(bits >> i & 1)
                a, b = size - 11 + i % 3, i // 3
                self.set(b, a, bit)  # top-right block
                self.set(a, b, bit)  # bottom-left block

    def draw_format(self, mask: int) -> None:
        bits, size = _format_bits(mask), self.size
        bit = [bool(bits >> i & 1) for i in range(15)]
        for i in range(6):
            self.set(i, 8, bit[i])
        self.set(7, 8, bit[6])
        self.set(8, 8, bit[7])
        self.set(8, 7, bit[8])
        for i in range(9, 15):
            self.set(8, 14 - i, bit[i])
        for i in range(8):
            self.set(8, size - 1 - i, bit[i])
        for i in range(8, 15):
            self.set(size - 15 + i, 8, bit[i])
        self.set(size - 8, 8, True)  # the always-dark module

    def draw_data(self, codewords: list[int]) -> None:
        """Zig-zag two-column strips from the bottom-right, skipping column 6."""
        size, i = self.size, 0
        total = len(codewords) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    c = right - j
                    upward = (right + 1) & 2 == 0
                    r = size - 1 - vert if upward else vert
                    if not self.fixed[r][c] and i < total:
                        self.dark[r][c] = bool(codewords[i >> 3] >> (7 - (i & 7)) & 1)
                        i += 1
            right -= 2

    def masked(self, mask: int) -> Matrix:
        fn = _MASKS[mask]
        return [[d ^ (not f and fn(r, c)) for c, (d, f) in enumerate(zip(row, frow))]
                for r, (row, frow) in enumerate(zip(self.dark, self.fixed))]


def _penalty(m: Matrix) -> int:
    size = len(m)
    score = 0
    for line in m + [list(col) for col in zip(*m)]:
        runs = [[dark, len(list(g))] for dark, g in groupby(line)]
        # Rule 1: 3 points for a run of 5 same-colour modules, +1 per extra.
        score += sum(n - 2 for _, n in runs if n >= 5)
        # Rule 3: dark-light-dark-light-dark runs in ratio 1:1:3:1:1 with a light
        # run of 4x on a side, 40 points per side. The quiet zone counts as light.
        if runs[0][0]:
            runs.insert(0, [False, 0])
        if runs[-1][0]:
            runs.append([False, 0])
        runs[0][1] += size
        runs[-1][1] += size
        lens = [n for _, n in runs]  # light runs at even indices
        for i in range(1, len(lens) - 5, 2):
            before, n, b, c, d, e, after = lens[i - 1:i + 6]
            if b == d == e == n and c == 3 * n:
                score += 40 * ((before >= 4 * n and after >= n) + (after >= 4 * n and before >= n))
    # Rule 2: 2x2 blocks of one colour.
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    # Rule 4: 10 points per 5% step the dark share lies outside 45-55%.
    total = size * size
    score += (abs(sum(map(sum, m)) * 20 - total * 10) + total - 1) // total * 10 - 10
    return score


def _encode(data: bytes, version: int | None = None, mask: int | None = None) -> Matrix:
    if version is None:
        for version in range(1, MAX_VERSION + 1):
            count_bits = 8 if version < 10 else 16
            if 4 + count_bits + 8 * len(data) <= _data_codewords(version) * 8:
                break
        else:
            raise ValueError(f"too long for a QR code ({len(data)} bytes)")
    symbol = _Symbol(version)
    symbol.draw_data(_codewords(data, version))
    candidates = range(8) if mask is None else (mask,)
    best = None
    for k in candidates:
        symbol.draw_format(k)
        m = symbol.masked(k)
        score = _penalty(m) if mask is None else 0
        if best is None or score < best[0]:
            best = (score, m)
    return best[1]


def matrix(text: str) -> Matrix:
    """QR modules for text (row-major, True = dark), without the quiet zone."""
    return _encode(text.encode("utf-8"))


def svg(text: str, border: int = 4) -> str:
    """The QR code for text as a standalone SVG, one unit per module."""
    m = matrix(text)
    size = len(m) + 2 * border
    path = "".join(f"M{c + border},{r + border}h1v1h-1z"
                   for r, row in enumerate(m) for c, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'shape-rendering="crispEdges">'
            f'<rect width="{size}" height="{size}" fill="#fff"/>'
            f'<path d="{path}" fill="#000"/></svg>')
