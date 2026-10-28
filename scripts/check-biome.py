#!/usr/bin/env python3
"""Is there iPhone app-usage data in ~/Library/Biome, and how much?

Must be run from **your own terminal** (it needs Full Disk Access). A process
running under launchd has no GUI app in its chain, so TCC has nothing to grant —
it cannot read this.

    python3 scripts/check-biome.py

Background: since iOS 16 / macOS 13, Apple keeps device statistics in Biome
rather than knowledgeC. Under one iCloud account the iPhone's usage data syncs
to the Mac's ~/Library/Biome/, as SEGB protobuf files (a 56-byte header ending
in ASCII "SEGB").
"""
import os
import glob

B = os.path.expanduser("~/Library/Biome")


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.0f}TB"


def main():
    if not os.path.exists(B):
        raise SystemExit(f"no {B}")
    try:
        os.listdir(B)
    except PermissionError:
        raise SystemExit(
            "unreadable — needs Full Disk Access. Grant it to this terminal app, then Cmd-Q and reopen.")

    print("=== top level ===")
    for d in sorted(os.listdir(B)):
        print(" ", d)

    print("\n=== every stream directory (by size, top 25) ===")
    dirs = []
    for root, subdirs, files in os.walk(B):
        if not files:
            continue
        size = sum(os.path.getsize(os.path.join(root, f))
                   for f in files if os.path.exists(os.path.join(root, f)))
        dirs.append((size, len(files), os.path.relpath(root, B)))
    dirs.sort(reverse=True)
    for size, nf, rel in dirs[:25]:
        print(f"  {human(size):>8}  {nf:>4} files  {rel}")

    # The exact path aw-import-screentime uses (checked 2026-08):
    #   ~/Library/Biome/streams/restricted/App.InFocus/remote/<device_id>/*
    #   ~/Library/Biome/sync/sync.db
    # remote/ = synced from other devices, local/ = this Mac. iPhone data is in remote/.
    print("\n=== App.InFocus (this is iPhone foreground use) ===")
    focus = os.path.join(B, "streams", "restricted", "App.InFocus")
    if not os.path.isdir(focus):
        print(f"  ✗ no {os.path.relpath(focus, B)}")
    else:
        for side in ("remote", "local"):
            d = os.path.join(focus, side)
            if not os.path.isdir(d):
                print(f"  ✗ no {side}/")
                continue
            devs = sorted(os.listdir(d))
            print(f"  {side}/  — {len(devs)} devices"
                  + ("   <- the iPhone should be here" if side == "remote" else "   <- this Mac"))
            for dv in devs:
                dd = os.path.join(d, dv)
                if not os.path.isdir(dd):
                    continue
                fs = [f for f in os.listdir(dd) if not f.startswith(".")]
                tot = sum(os.path.getsize(os.path.join(dd, f)) for f in fs
                          if os.path.isfile(os.path.join(dd, f)))
                mt = max((os.path.getmtime(os.path.join(dd, f)) for f in fs
                          if os.path.isfile(os.path.join(dd, f))), default=0)
                import datetime as _dt
                when = _dt.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M") if mt else "?"
                print(f"      {dv}   {len(fs)} files / {human(tot)}   last updated {when}")

    print("\n=== other app-related streams ===")
    pats = ["*InFocus*", "*Usage*", "*Launch*", "*Focus*"]
    hits = set()
    for p in pats:
        hits |= set(glob.glob(os.path.join(B, "**", p), recursive=True))
    if not hits:
        print("  (none — the iPhone's app usage has not synced here)")
    for h in sorted(hits)[:20]:
        rel = os.path.relpath(h, B)
        if os.path.isdir(h):
            fs = [f for f in os.listdir(h) if not f.startswith(".")]
            tot = sum(os.path.getsize(os.path.join(h, f)) for f in fs
                      if os.path.isfile(os.path.join(h, f)))
            print(f"  [dir]  {rel}   {len(fs)} files / {human(tot)}")
        else:
            print(f"  [file] {rel}   {human(os.path.getsize(h))}")

    # Look for a screen-state stream. With one, "app in front" could be
    # intersected with "screen on", instead of the guessed "over 30 minutes is not
    # trusted". These streams are tiny, so a by-size list always cuts them off —
    # they have to be looked for.
    print("\n=== screen / lock state streams (with one, real usage time is computable) ===")
    KEYS = ("lock", "screen", "display", "backlight", "unlock",
            "device", "wake", "sleep", "orientation", "proximity")
    hits = []
    for root, subdirs, files in os.walk(os.path.join(B, "streams")):
        rel = os.path.relpath(root, B)
        low = rel.lower()
        if any(k in low for k in KEYS) and files:
            size = sum(os.path.getsize(os.path.join(root, f))
                       for f in files if os.path.isfile(os.path.join(root, f)))
            hits.append((rel, len(files), size))
    if not hits:
        print("  (none — so the 30-minute threshold stays for now)")
    for rel, nf, size in sorted(hits):
        mark = "  <- another device (iPhone)" if "/remote/" in rel else ""
        print(f"  {human(size):>8}  {nf:>3} files  {rel}{mark}")

    # Look at one SEGB file's header, to confirm the format matches
    print("\n=== one file's header (is it SEGB?) ===")
    cand = [os.path.join(r, f) for r, _, fs in os.walk(B) for f in fs
            if "InFocus" in r][:1]
    if not cand:
        cand = [os.path.join(r, f) for r, _, fs in os.walk(B) for f in fs][:1]
    if cand:
        with open(cand[0], "rb") as fh:
            head = fh.read(64)
        print(f"  {os.path.relpath(cand[0], B)}")
        print(f"  first 8 bytes: {head[:8]!r}")
        print(f"  contains SEGB marker: {'yes' if b'SEGB' in head else 'no'}")
    else:
        print("  (no file to look at)")


if __name__ == "__main__":
    main()
