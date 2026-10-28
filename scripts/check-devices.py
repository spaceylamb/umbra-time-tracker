#!/usr/bin/env python3
"""Which devices does knowledgeC actually hold data for (has the iPhone arrived)?

Must be run from **your own terminal** — a process under launchd has no GUI app
in its chain, so TCC has nothing to grant.

    python3 scripts/check-devices.py

⚠️ Do not hard-code ZSTREAMNAME. On some machines `/app/inFocus` is empty and
foreground data is in `/app/usage` (or `/app/activity`) — an earlier version
limited its "iOS bundle check" to /app/inFocus, searching an empty table, and
"found nothing" carried no information.
"""
import os
import shutil
import sqlite3
import tempfile

KDB = os.path.expanduser("~/Library/Application Support/Knowledge/knowledgeC.db")

# Only the ones we are sure of; anything else is printed as is, not guessed.
MODELS = {
    "iPhone11,8": "iPhone XR",
    "iPhone13,4": "iPhone 12 Pro Max",
    "iPad8,5": "iPad Pro 12.9″ (3rd)",
    "RealityDevice14,1": "Apple Vision Pro",
}

# Bundle prefixes that exist on iOS and not on macOS
IOS_HINTS = ("com.apple.mobile", "com.apple.MobileSMS", "com.apple.camera",
             "com.apple.Health", "com.apple.mobilecal", "com.apple.mobilemail",
             "com.apple.mobilephone", "com.apple.mobilesafari",
             "com.burbn.instagram", "com.zhiliaoapp", "com.ss.iphone",
             "com.tencent.xin", "com.atebits.Tweetie2", "com.google.ios")


def main():
    if not os.path.exists(KDB):
        raise SystemExit(f"cannot find {KDB}")
    try:
        with open(KDB, "rb") as f:
            f.read(16)
    except PermissionError:
        raise SystemExit(
            "unreadable — grant this terminal app Full Disk Access, "
            "then Cmd-Q it completely and reopen.")

    with tempfile.TemporaryDirectory() as tmp:
        for suf in ("", "-wal", "-shm"):
            if os.path.exists(KDB + suf):
                shutil.copy2(KDB + suf, os.path.join(tmp, "k.db" + suf))
        con = sqlite3.connect(os.path.join(tmp, "k.db"))
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

        # device id → model
        dev = {}
        if "ZSYNCPEER" in tables:
            cols = {r[1] for r in con.execute("PRAGMA table_info(ZSYNCPEER)")}
            if {"ZDEVICEID", "ZMODEL"} <= cols:
                for did, model in con.execute(
                        "SELECT ZDEVICEID, ZMODEL FROM ZSYNCPEER "
                        "WHERE ZMODEL IS NOT NULL"):
                    dev[did] = model

        print("=== records per stream (don't assume /app/inFocus) ===")
        rows = list(con.execute(
            "SELECT ZSTREAMNAME, count(*) FROM ZOBJECT "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 15"))
        for name, n in rows:
            print(f"  {str(name):<34} {n:>8}")

        print("\n=== streams × source device ===")
        if "ZSOURCE" in tables:
            q = ("SELECT o.ZSTREAMNAME, s.ZDEVICEID, count(*) "
                 "FROM ZOBJECT o LEFT JOIN ZSOURCE s ON o.ZSOURCE = s.Z_PK "
                 "GROUP BY 1,2 HAVING count(*) > 20 ORDER BY 3 DESC LIMIT 25")
            for stream, did, n in con.execute(q):
                if did is None:
                    who = "this Mac"
                else:
                    m = dev.get(did, "")
                    who = f"{MODELS.get(m, m) or 'unknown device'}  {did[:8]}…"
                print(f"  {str(stream):<30} {who:<34} {n:>7}")

        print("\n=== apps seen per device (top 6) ===")
        q = ("SELECT s.ZDEVICEID, o.ZVALUESTRING, count(*) FROM ZOBJECT o "
             "LEFT JOIN ZSOURCE s ON o.ZSOURCE = s.Z_PK "
             "WHERE o.ZVALUESTRING LIKE '%.%' GROUP BY 1,2 ORDER BY 1, 3 DESC")
        seen: dict = {}
        for did, bundle, n in con.execute(q):
            seen.setdefault(did, []).append((bundle, n))
        for did, items in seen.items():
            m = dev.get(did, "") if did else ""
            who = "this Mac" if did is None else f"{MODELS.get(m, m) or 'unknown'} {str(did)[:8]}…"
            ios = [b for b, _ in items
                   if any(b.startswith(h) for h in IOS_HINTS)]
            print(f"\n  ── {who} ──   {len(items)} apps in all")
            for b, n in items[:6]:
                print(f"     {b[:52]:<54} {n:>6}")
            # "Has iOS-only apps" alone cannot be checked — list the hits, so a
            # false positive (a macOS bundle sharing a prefix) is obvious at a glance
            if ios:
                print(f"     ↳ {len(ios)} matching an iOS-only prefix: {', '.join(ios[:5])}")

        con.close()


if __name__ == "__main__":
    main()
