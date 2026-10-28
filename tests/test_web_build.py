"""The committed page build must match its sources.

umbra/web/ is checked in because the documented install builds from source
and a user must never need Node (ADR 0001; the frontend plan, KTD3). The cost
of committing a build is that it can fall behind: someone edits web/src,
forgets `bun run --cwd web build`, and every test here stays green while
users get the old page. web/scripts/stamp.mjs writes a hash of the sources
into the build; this recomputes it the same way and compares.
"""

import hashlib
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


def _walk(d):
    out = []
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        out += _walk(p) if os.path.isdir(p) else [p]
    return out


def source_hash() -> str:
    files = [*_walk(os.path.join(WEB, "src")),
             os.path.join(WEB, "index.html"),
             os.path.join(WEB, "vite.config.ts"),
             os.path.join(WEB, "package.json"),
             os.path.join(ROOT, "umbra/schema/api.schema.json")]
    h = hashlib.sha256()
    for f in files:
        h.update(os.path.relpath(f, ROOT).replace(os.sep, "/").encode())
        h.update(b"\0")
        with open(f, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


class WebBuildTest(unittest.TestCase):
    def test_build_is_present(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "umbra/web/index.html")))

    def test_build_matches_sources(self):
        with open(os.path.join(ROOT, "umbra/web/SOURCE_HASH")) as f:
            stamped = f.read().strip()
        self.assertEqual(stamped, source_hash(),
                         "umbra/web/ is older than web/src — run "
                         "`bun run --cwd web build` and commit the result")


if __name__ == "__main__":
    unittest.main()
