"""`umbra export`: the new page as a static site — what the phone reads.

Decided 2026-09-28 (ADR 0003): the phone gets a read-only export on the
person's own host, not umbra serve over the network. This writes the built
page (umbra/web/) plus every GET endpoint as a file at its own path:

    api/meta.json · api/day/<d>.json · api/day/<d>/detail.json · …/said.json
    api/day/<d>/raw.json (only for the last [viz] raw_days days)
    api/week/<w>.json · api/setup.json · api/settings.json · api/import.json

and flags index.html as static, so the client reads ./api/…json, routes in
the hash, and shows every write as something done on the Mac.

⚠️ Like the single-file page (umbra-viz.html), the export carries raw history — browsing, clipboard,
agent transcripts. It goes to a directory the caller names and nowhere else;
nothing from the data directory is copied but the JSON written here. Deploy
only behind authentication (see ops.deploy).
"""

import datetime as dt
import json
import os
import re
import shutil

from . import api, config, db, goals as G, settings

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MARK = "<!--umbra-static-->"


def _write(root: str, path: str, obj) -> int:
    full = os.path.join(root, path.lstrip("/") + ".json")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with open(full, "w", encoding="utf-8") as f:
        f.write(data)
    return len(data)


def _tilde(obj, home: str):
    """This Mac's paths, as the page shows them anyway (~/…): the export is
    read on another device, where /Users/<name>/ says nothing useful and
    names the account."""
    if isinstance(obj, str):
        return "~" + obj[len(home):] if obj == home or obj.startswith(home + os.sep) else obj
    if isinstance(obj, list):
        return [_tilde(v, home) for v in obj]
    if isinstance(obj, dict):
        return {k: _tilde(v, home) for k, v in obj.items()}
    return obj


def payloads(raw_days: int | None = None):
    """Yield (path, payload) for every GET the page can make, from one
    database read. raw_days None = config's [viz] raw_days (default 30);
    0 = every day. Endpoints that fail for a day (none of its tables have
    rows) are skipped, exactly as the server would answer 404."""
    con = db.connect()
    try:
        home = os.path.expanduser("~").rstrip(os.sep)
        meta = api.meta(con)
        yield "/api/meta", _tilde(meta, home)
        order = meta["order"]
        if raw_days is None:
            raw_days = int((config.load().get("viz") or {}).get("raw_days", 30))
        cutoff = ""
        if raw_days and order:
            last = dt.date.fromisoformat(order[-1])
            cutoff = (last - dt.timedelta(days=raw_days - 1)).isoformat()
        weeks = set()
        for d in order:
            for suffix, fn in (("", api.day), ("/detail", api.detail), ("/said", api.said)):
                try:
                    yield f"/api/day/{d}{suffix}", fn(d, con)
                except api.NotFound:
                    pass
            if d >= cutoff:
                yield f"/api/day/{d}/raw", api.raw(d, con)
            weeks.add(G.week_of(d))
        for w in sorted(weeks):
            yield f"/api/week/{w}", api.week(w, con)
        yield "/api/setup", _tilde(api.setup(), home)
        yield "/api/settings", _tilde(settings.get(), home)
        # No import runs from an export; say so rather than 404 the poll.
        yield "/api/import", {"run": None}
    finally:
        con.close()


def _index(inline: dict | None = None, demo: dict | None = None) -> str:
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
        html = f.read()
    flag = ({"inline": inline} if inline is not None else {})
    if demo is not None:
        flag["demo"] = demo
    # </script> inside the JSON would end the tag early.
    js = json.dumps(flag, ensure_ascii=False).replace("</", "<\\/")
    # After <meta charset>, never before it: a browser only honours the charset
    # if it sits in the first 1024 bytes, and file:// (where the single-file
    # page is opened) sends no charset header — with the payload in front, every
    # non-ASCII character in the data (CJK transcripts, an em dash) read as
    # Windows-1252. The bundle itself is ASCII-escaped, so only data broke.
    tag = f"{MARK}<script>window.__UMBRA_STATIC__={js}</script>"
    m = re.search(r"<meta charset=[^>]*>", html)
    if m:
        return html[:m.end()] + tag + html[m.end():]
    return html.replace("<head>", "<head>" + tag, 1)


def write(out: str, raw_days: int | None = None, ours: bool = False,
          demo: dict | None = None) -> dict:
    """Write the static site into `out` (replacing what an earlier export put
    there). ours=True: `out` is a directory Umbra owns (viz.SITE_DIR) — it
    may still hold what an older version deployed, which is safe to clear.
    demo: only devseed.site passes it — the public demo's flag, whose `now`
    (epoch ms) is where the page's clock stands still; without one, the
    moment of this export. Returns {"files": n, "bytes": n}."""
    if not os.path.exists(os.path.join(WEB, "index.html")):
        raise FileNotFoundError("the page has not been built (bun run --cwd web build)")
    # Only ever clear a directory an earlier export made — never a directory
    # someone pointed this at by mistake.
    if os.path.exists(out) and os.listdir(out):
        idx = os.path.join(out, "index.html")
        mine = False
        if os.path.exists(idx):
            with open(idx, encoding="utf-8") as f:
                mine = MARK in f.read(4096)
        if not (mine or ours):
            raise FileExistsError(f"{out} is not empty and is not an earlier export — refusing to clear it")
        shutil.rmtree(out)
    shutil.copytree(os.path.join(WEB, "assets"), os.path.join(out, "assets"))
    n = size = 0
    for path, obj in payloads(raw_days):
        if path == "/api/meta" and demo is not None and "now" not in demo:
            demo = {**demo, "now": obj["built_ms"]}
        size += _write(out, path, obj)
        n += 1
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(_index(demo=demo))
    return {"files": n, "bytes": size}


def write_single(path: str, raw_days: int | None = None) -> dict:
    """One self-contained HTML file: scripts, styles and every payload inline.
    Opens from disk with a double-click; large, because it carries every day."""
    data = dict(payloads(raw_days))
    html = _index(inline=data)
    # Inline the built assets referenced by relative path.
    import base64

    def font(m):
        with open(os.path.join(WEB, "assets", m.group(1)), "rb") as f:
            return "url(data:font/woff2;base64," + base64.b64encode(f.read()).decode() + ")"

    def css(m):
        with open(os.path.join(WEB, m.group(1)), encoding="utf-8") as f:
            text = f.read()
        # The fonts sit beside the CSS; once the CSS is inline they would
        # resolve against the HTML file instead, so they go inline too.
        return "<style>" + re.sub(r"url\(\.?/?([\w.-]+\.woff2)\)", font, text) + "</style>"
    def js(m):
        with open(os.path.join(WEB, m.group(1)), encoding="utf-8") as f:
            return f"<script type=\"module\">{f.read()}</script>"
    html = re.sub(r'<link rel="stylesheet"[^>]*href="\./(assets/[^"]+)"[^>]*>', css, html)
    html = re.sub(r'<script type="module"[^>]*src="\./(assets/[^"]+)"[^>]*></script>', js, html)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return {"files": len(data), "bytes": len(html)}
