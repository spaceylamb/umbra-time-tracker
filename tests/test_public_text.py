"""What the repository says in public.

The repository is public, and its comments are read by strangers. A
measurement can stay — it is why a threshold is what it is — but not the
personal detail that came with it, nor a place someone lives. A few such
details were taken out once, tests included;
this keeps them out. They are kept here as hashes, word by word, so that the
list does not repeat what it guards against: to add one, append the
`_h` of each of its words (lowercased, as `_words` splits them).

The platform table in the README is checked against the code: a source that
declares PLATFORM = "darwin" must be listed as macOS-only, and never as
something Linux runs.
"""

import hashlib
import os
import re
import unittest

from umbra.importers import ALL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REMOVED = (
    ("a2659b7e6b802fa7", "e100fbce008c04ec", "c00062f7ec9c80e2"),   # the self-check below
    ("de19c97d557a0e8a", "428821350e969149"),
    ("69c04ffd98674f1d", "b4128df29f3726b4"),
    ("586a866f990ab55e", "10c22bcf4c768b51", "8a72266588ef612e"),
    ("c6a1427d7119cd3c", "8a72266588ef612e"),
    ("d93d9e224c621cf4",),
    ("37b84628b786bcb9", "6201111b83a0cb5b"),
    ("02c4b2a32ad8e222", "82244417f956ac7c"),
    ("9090498e9f61bc52", "a18b869b2e81c0c5", "2c195230dee8616d"),
    ("7c2c4df740f720b0", "a0af3a48cfad1fc0", "7e1ddfc85ae45a95"),
    ("3fdba35f04dc8c46", "e021d7d489652027"),
    ("809e63d5c8aa03af", "62c66a7a5dd70c31", "1d40973eccd8c0aa"),
    ("6b51d431df5d7f14", "aaa9402664f1a41f", "6f4b6612125fb3a0", "62c66a7a5dd70c31"),
    ("af81d5659d93baaf", "46a15328760d5222"),
    ("b05e244762b1e472", "0b407281768f0e83"),
    ("7c3d90003d7d645b", "28391d3bc64ec15c", "c9a8dc336964db7f"),
    ("bb668ca955632160", "ab51004e9d71a485"),
    ("8a88d2f991ad840e", "582967534d0f909d"),
    ("b17ef6d19c7a5b1e", "e021d7d489652027"),
    ("4f6c844184d2bd65", "aaa9402664f1a41f"),
    ("4f6c844184d2bd65", "41f0071e3e63eb52"),
    ("6b51d431df5d7f14", "aaa9402664f1a41f", "6f4b6612125fb3a0", "1f6fa6f69d185e60"),
    ("809e63d5c8aa03af", "62c66a7a5dd70c31"),
    ("3e5138f7dba1f26f",),
    ("1ca0421e42246cee",),
    ("bd0c85827d6bf28c",),
    ("3641b8e0c9cf1bf7",),
    ("298e4107a44dcdca",),
    ("b6a773b15dfd19fc",),
    ("5577ef63a28ebcea",),
    ("388b23d9a688dbc3",),
    ("672302f3a9ad34a9",),
    ("e798b427db62d962",),
    ("88aa9d6e73d1bb74",),
    ("a74a8e75ef144621",),
    ("ab3a3399a3b4ef56",),
    ("c6361557a9ab74ab",),
    ("90591a739625f603",),
    ("1e9024d23af18a54",),
    ("3002630061b44a4b",),
    ("39c1af8f9e82f90e",),
    ("86cbedd28b663cb1",),
)


def _public_files():
    for name in ("README.md", "PRIVACY.md", "AGENTS.md", "CONTRIBUTING.md",
                 "config.example.toml"):
        yield os.path.join(ROOT, name)
    for top in ("docs", "umbra", "scripts", "tests"):
        for dirpath, dirnames, files in os.walk(os.path.join(ROOT, top)):
            # umbra/web/ is the built page: minified, not prose.
            dirnames[:] = [d for d in dirnames
                           if os.path.join(dirpath, d) != os.path.join(ROOT, "umbra", "web")]
            for f in files:
                p = os.path.join(dirpath, f)
                if f.endswith((".py", ".md", ".toml")) and p != os.path.abspath(__file__):
                    yield p


def _flat(text: str) -> str:
    """Comments wrap mid-phrase; join the lines and drop the comment marks."""
    return re.sub(r"\s*\n\s*(?:#\s*)?", " ", text)


def _h(word: str) -> str:
    return hashlib.sha256(word.encode()).hexdigest()[:16]


def _words(text: str) -> list:
    return [w.rstrip(".,/").lower() for w in re.findall(r"\w[\w./,+]*", _flat(text))]


def _hits(text: str) -> list:
    words = _words(text)
    hashed = [_h(w) for w in words]
    first = {p[0] for p in REMOVED}
    return [" ".join(words[i:i + len(p)])
            for i, h in enumerate(hashed) if h in first
            for p in REMOVED if p[0] == h and tuple(hashed[i:i + len(p)]) == p]


class NoPersonalDetailTest(unittest.TestCase):
    def test_the_removed_details_stay_out(self):
        for path in _public_files():
            with open(path, encoding="utf-8") as f:
                hits = _hits(f.read())
            with self.subTest(os.path.relpath(path, ROOT)):
                self.assertEqual(hits, [])

    def test_the_check_finds_what_it_guards_against(self):
        """A hash list that matched nothing would pass forever."""
        self.assertEqual(_hits("an Umbra canary,\n# phrase."), ["umbra canary phrase"])


def _source(name, mod) -> str:
    return getattr(mod, "SOURCE", None) or name


def _platform_rows() -> dict:
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    m = re.search(r"^### Platforms\n(.*?)(?=^#)", readme, re.S | re.M)
    if not m:
        return {}
    rows = {}
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] in ("macOS", "Linux", "Windows"):
            rows[cells[0]] = cells[1]
    return rows


def _names(cell: str) -> set:
    return set(re.findall(r"`([a-z_]+)`", cell))


class PlatformTableTest(unittest.TestCase):
    def setUp(self):
        self.rows = _platform_rows()

    def test_the_readme_has_a_row_for_each_platform(self):
        self.assertEqual(set(self.rows), {"macOS", "Linux", "Windows"})

    def test_linux_lists_every_source_that_is_not_macos_only(self):
        expected = {_source(n, m) for n, m in ALL.items() if getattr(m, "PLATFORM", None) is None}
        self.assertEqual(_names(self.rows.get("Linux", "")), expected)

    def test_the_macos_only_sources_are_named_and_never_offered_on_linux(self):
        mac_only = {_source(n, m) for n, m in ALL.items() if getattr(m, "PLATFORM", None) == "darwin"}
        self.assertTrue(mac_only)
        self.assertTrue(mac_only <= _names(self.rows.get("macOS", "")))
        self.assertFalse(mac_only & _names(self.rows.get("Linux", "")))


if __name__ == "__main__":
    unittest.main()
