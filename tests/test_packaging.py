"""Packaging metadata — keeping "no dependencies" and "one line with uv or
pipx" true.

Installing (aligned with the README's quick start):
    uv tool install git+https://github.com/spaceylamb/umbra-time-tracker   # or
    pipx install git+https://github.com/spaceylamb/umbra-time-tracker
The command is then `umbra`; no more "find the right python3".

This tests the metadata itself; "it really runs once installed" is checked in
CI with a fresh venv (pip install . && umbra --help), too slow for unit tests.
"""

import os
import tomllib
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


class MetadataTest(unittest.TestCase):
    def test_pyproject_exists_and_parses(self):
        self.assertIn("project", load())

    def test_python_floor_matches_the_code(self):
        """The code uses tomllib, new in 3.11; a lower floor installs and then
        fails."""
        self.assertEqual(load()["project"]["requires-python"], ">=3.11")

    def test_the_core_has_no_dependencies(self):
        """"Runs on the standard library" is a promise on the README's first
        screen; no dependency may slip in."""
        self.assertEqual(load()["project"].get("dependencies", []), [])

    def test_the_collector_is_an_optional_dependency(self):
        """Only the resident collector needs pyobjc; in core it would shut Linux
        users out."""
        extras = load()["project"]["optional-dependencies"]
        self.assertTrue(any("pyobjc" in d for d in extras["collector"]))

    def test_the_entry_point_names_a_real_function(self):
        target = load()["project"]["scripts"]["umbra"]
        mod_name, func = target.split(":")
        import importlib
        mod = importlib.import_module(mod_name)
        self.assertTrue(callable(getattr(mod, func)))

    def test_the_version_has_one_source(self):
        """pyproject reads umbra.__version__ — two hand-written copies drift."""
        self.assertIn("version", load()["project"]["dynamic"])
        import umbra
        self.assertRegex(umbra.__version__, r"^\d+\.\d+\.\d+")

    def test_every_non_py_file_read_at_run_time_is_in_package_data(self):
        """A missing package-data entry is green in every local test and breaks
        only for someone who installed. api.schema.json is umbra serve's
        contract and is read at run time."""
        import glob
        globs = load()["tool"]["setuptools"]["package-data"]["umbra"]
        pkg = os.path.join(ROOT, "umbra")
        for rel in ("schema/api.schema.json", "web/index.html"):
            self.assertTrue(os.path.exists(os.path.join(pkg, rel)), rel)
            self.assertTrue(any(os.path.join(pkg, rel) in
                                glob.glob(os.path.join(pkg, g)) for g in globs),
                            f"{rel} is not covered by package-data")

    def test_the_license_is_apache(self):
        self.assertIn("Apache", str(load()["project"]["license"]))


if __name__ == "__main__":
    unittest.main()
