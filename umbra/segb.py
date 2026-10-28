"""SEGB container + protobuf wire-format reader (standard library only).

From iOS 16 / macOS 13 onwards Apple moved device statistics out of knowledgeC
and into Biome, stored in SEGB format. **There is no official documentation**;
the layout below was reverse-engineered from real files on 2026-08-15:

    header 0x20 bytes   "SEGB" + record count (u32) + created (double) + ...
    data region         [protobuf][zero-pad to 4][CRC 4][0a 00 00 00]  x N
    trailing index      [offset u32][state u32][timestamp double] x N,
                        growing backwards from the end of the file

Why not the existing ccl-segb: it would drag in uv and a chain of dependencies,
while the rest of this project has none. The protobuf wire format is itself
simple (varints and length prefixes), and the subset needed fits in a hundred
lines — a better trade than taking on a dependency chain.

Why a sequential scan rather than the trailing index: the offsets in that index
do not line up for records carrying a "field 1" prefix (measured: the fourth
record is already off), and the semantics are not fully understood. A sequential
scan depends only on the physical layout between records; measured, 5,284
declared records yield 5,111 parsed (97%), and what is missed is the edge the
parser cannot confirm. Better to under-collect than to collect wrongly.

⚠️ Reading Biome needs Full Disk Access, and TCC attributes it to **the process
chain making the request**. Claude Code running under launchd has no GUI app on
its chain and can never read it; a scheduled job launched by launchd can.
"""

import os
import struct

# Apple absolute time counts from 2001-01-01
APPLE_EPOCH_S = 978_307_200

HEADER_SIZE = 0x20        # file header
RECORD_PREFIX = 8         # [CRC 4][flags 4] before each protobuf
INDEX_ENTRY = 16          # trailing index entry: [offset 4][state 4][ts 8]

# The parser's self-protection. When both the field number and the length are far
# beyond the scale of real data, what has been read is CRC or padding — that is
# not "an odd record", it is "this is not a record at all".
MAX_FIELD_NO = 40
MAX_LEN = 4096


def varint(b: bytes, i: int) -> tuple[int, int]:
    """A protobuf varint. Returns (value, new position)."""
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, i
        s += 7


def parse_message(b: bytes, i: int, end: int) -> tuple[dict, int]:
    """Parse one protobuf; returns ({field number: [values...]}, end position).

    An invalid field stops the parse where it is rather than raising — records
    are separated by zero padding and CRCs, and "cannot parse any further" is
    exactly how a record boundary is found.
    """
    out: dict[int, list] = {}
    while i < end:
        k0 = i
        try:
            key, i = varint(b, i)
        except IndexError:
            return out, k0
        fn, wt = key >> 3, key & 7
        if fn == 0 or fn > MAX_FIELD_NO or wt in (3, 4, 6, 7):
            return out, k0
        try:
            if wt == 0:
                v, i = varint(b, i)
            elif wt == 1:
                v = struct.unpack_from("<d", b, i)[0]
                i += 8
            elif wt == 2:
                ln, i = varint(b, i)
                if ln > MAX_LEN or i + ln > end:
                    return out, k0
                v = b[i:i + ln]
                i += ln
            else:                       # wt == 5
                v = struct.unpack_from("<f", b, i)[0]
                i += 4
        except (IndexError, struct.error):
            return out, k0
        out.setdefault(fn, []).append(v)
    return out, i


def apple_ms(seconds: float) -> int:
    """Apple absolute seconds -> UTC milliseconds (the unit of events.ts)."""
    return int((seconds + APPLE_EPOCH_S) * 1000)


def nonempty(rec: dict) -> bool:
    """The default test: keep anything that parsed.

    **Assume no schema whatsoever.** Every Biome stream numbers its fields
    differently — App.InFocus is f4=time f6=bundle, Media.NowPlaying is f2=time
    f15=bundle. Hardcoding one stream's test here makes every other stream yield
    nothing, and it does so **silently**: it looks exactly like "that stream has
    no data".

    Filtering noise is the importer's job — only it knows the shape of its own
    stream.
    """
    return bool(rec)


def in_apple_range(seconds) -> bool:
    """Does this timestamp look like Apple absolute seconds in 2015-2033?

    The classic symptom of a misaligned offset is a time flying off to 1904 or
    2400, which makes this a useful test for "is this stretch actually a record".
    Importers use it as needed.
    """
    return isinstance(seconds, float) and 4.4e8 < seconds < 1.05e9



def subdirs(root: str) -> list[str]:
    """List the subdirectories of root. **"Cannot read" and "nothing there"
    must stay distinguishable.**

    Missing      -> return []. This Mac may simply never have synced an iPhone,
                    which is a normal state.
    No permission -> **raise**. Returning [] when TCC denies access makes the
                    importer report "✓ collected 0", which looks identical to
                    "no new data" — while the database already holds thousands of
                    historical rows, so the freshness report still shows
                    "covered through today". With permission genuinely revoked
                    you would see weeks of "✓ added 0" while half the iPhone
                    timeline quietly stopped updating. The imports table exists
                    separately from events precisely to prevent this (see db.py).

    The error points at **the host GUI app** rather than python, because TCC
    attributes to the responsible process. This machine has been bitten by that
    once (see the Full Disk Access section of the README).
    """
    try:
        names = sorted(os.listdir(root))
    except FileNotFoundError:
        return []
    except PermissionError as e:
        raise PermissionError(
            e.errno,
            f"cannot read {root} — grant Full Disk Access to the HOST GUI app: "
            "System Settings → Privacy & Security → Full Disk Access",
        ) from e
    return [os.path.join(root, n) for n in names
            if os.path.isdir(os.path.join(root, n))]

def read(path: str, valid=nonempty) -> list[dict]:
    """Read one SEGB file and return the protobuf records parsed out of it.

    A missing, truncated or unrecognised file returns whatever was parsed rather
    than raising — a Biome file may be mid-write from an iCloud sync at any
    moment, and half a file should not take down the whole import.
    """
    try:
        with open(path, "rb") as f:
            b = f.read()
    except (OSError, PermissionError):
        return []

    if len(b) < HEADER_SIZE + 4 or b[:4] != b"SEGB":
        # There is a variant that puts the magic at offset 52 (behind a
        # filename prefix) with a different record layout. Not guessed at here;
        # left for separate handling later.
        return []

    n = struct.unpack_from("<I", b, 4)[0]
    data_end = max(0, len(b) - INDEX_ENTRY * n)

    recs, i = [], HEADER_SIZE + RECORD_PREFIX
    while i < data_end:
        rec, end = parse_message(b, i, data_end)
        if rec and valid(rec):
            recs.append(rec)
        nxt = (end + 3) // 4 * 4 + RECORD_PREFIX
        if nxt <= i:                    # no progress; avoid an infinite loop
            break
        i = nxt
    return recs


def read_dir(path: str, valid=nonempty) -> list[dict]:
    """Read every SEGB file in one stream directory.

    lock and tombstone are skipped: the first is a zero-byte lock file, the
    second records deletion markers rather than usage events.
    """
    out = []
    try:
        names = sorted(os.listdir(path))
    except (OSError, PermissionError):
        return []
    for name in names:
        if name in ("lock", "tombstone") or name.startswith("."):
            continue
        p = os.path.join(path, name)
        if os.path.isfile(p):
            out += read(p, valid)
    return out
