"""Chrome history importer.

Source: ~/Library/Application Support/Google/Chrome/<Profile>/History
An SQLite database with two tables:
  urls   (id, url, title)
  visits (id, url -> urls.id, visit_time, visit_duration, from_visit)

visit_time is WebKit epoch: microseconds since 1601-01-01.
To a Unix timestamp: visit_time / 1e6 - 11644473600

⚠️ Chrome locks the database while it runs; copy it first, with its -wal, or
   the most recent visits are missing.
⚠️ visit_duration is how long the tab lived, not attention — a background tab
   accumulates hours (17,942 s has been seen). Never read it as focus time.
"""

import glob
import os
import shutil
import sqlite3
import tempfile

from .. import privacy

PLATFORM = "darwin"          # the History path is macOS's
# Something every normal day: this many days with nothing new means it has
# stopped, and STALE_HINT is where to look first (plugins/staleness.py).
STALE_DAYS = 4
STALE_HINT = ('Chrome history on this Mac. A few days away from the computer is normal; '
              'otherwise check that Chrome still uses the same profile.')

WEBKIT_OFFSET = 11644473600
CHROME_DIR = os.path.expanduser("~/Library/Application Support/Google/Chrome")

# Chrome's VisitSource enum. Only 1 means "browsed on this machine".
# ⚠️ Chrome **writes visit_source rows only for visits that were not browsed
#    here** — local browsing is not in that table at all. So "no matching row"
#    means local browsing, not "unknown".
SOURCE_BROWSED = 1


def _originator_col(con: sqlite3.Connection) -> str:
    """The expression for the sync-source column; an empty string on older
    Chrome versions that lack it.

    Selecting a column that does not exist raises OperationalError and the
    whole chrome source is lost — giving up the main data for a nice-to-have
    field is the worst trade there is.
    """
    cols = {c[1] for c in con.execute("PRAGMA table_info(visits)")}
    return ("v.originator_cache_guid" if "originator_cache_guid" in cols else "''")


def _snapshot(src: str, tmpdir: str) -> str:
    """Copy History and its wal/shm into a temp directory, out of Chrome's lock."""
    dst = os.path.join(tmpdir, os.path.basename(os.path.dirname(src)) + ".db")
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, dst + suffix)
    return dst


def collect(since_ms: int | None = None) -> list[tuple]:
    # since_ms is accepted and ignored: this source reads a local file, and a
    # re-read takes milliseconds — there is no API quota to save. It stays so
    # every importer has the same signature and one loop can pass it by keyword.
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for hist in glob.glob(os.path.join(CHROME_DIR, "*", "History")):
            profile = os.path.basename(os.path.dirname(hist))
            if profile in ("System Profile", "Guest Profile"):
                continue
            try:
                snap = _snapshot(hist, tmp)
                con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
            except Exception as e:                      # noqa: BLE001
                print(f"  ! {profile}: {e}")
                continue

            for vid, vt, dur, url, title, guid, vsrc in con.execute(f"""
                SELECT v.id, v.visit_time, v.visit_duration, u.url, u.title,
                       COALESCE({_originator_col(con)}, ''), s.source
                FROM visits v JOIN urls u ON u.id = v.url
                LEFT JOIN visit_source s ON s.id = v.id
            """):
                cleaned = privacy.apply_url(url or "", title or "")
                if cleaned is None:
                    continue
                url, title = cleaned
                ts_ms = int((vt / 1e6 - WEBKIT_OFFSET) * 1000)
                if ts_ms <= 0:
                    continue
                # visit_duration is **how long the tab lived**, not attention (see
                # the module docstring), so it does not go into dur_ms — that field
                # means "how long this lasted", and a tab left open overnight for
                # 936 minutes is not that. It goes in the payload, where it is
                # occasionally useful (it shows a tab stayed open). Measured
                # 2026-08-16: one such record could cover a whole night
                # and push WHOOP's sleep out of 99 five-minute slots.
                payload = {"url": url, "title": title, "profile": profile}
                if dur:
                    payload["tab_s"] = int(dur / 1e6)
                # Not produced on this machine: Chrome syncs in what you searched
                # on your phone too. The key is written only when true — measured,
                # 40 of 3,776 visits were synced, and a False on every other one
                # would be wasted space.
                if guid or (vsrc is not None and vsrc != SOURCE_BROWSED):
                    payload["synced"] = True
                rows.append((
                    f"chrome:{profile}:{vid}", ts_ms, 0,
                    "chrome", "visit", payload,
                ))
            con.close()
    return rows


# ── How this source appears downstream ────────────────────────

LANES = [{"key": "chrome", "kind": None,
          "origin": ("Chrome History", "local SQLite")}]

# visit_duration is tab lifetime, not attention. Background tabs accumulate for
# hours (one real record: 17,942s), so it stays "point".
DURATION = "point"


def render(e) -> str | None:
    t = e.get("title") or e.get("url")
    return f"▢ viewing/open: {t}" if t else None


def classify(e) -> str | None:
    return "browse" if (e.get("title") or e.get("url")) else None
