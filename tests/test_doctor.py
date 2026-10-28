"""Which interpreter a scheduled job runs, and which file to grant.

Three answers that used to be one, and each was wrong for someone:

    job_python()      what `schedule install` writes: the interpreter running
                      umbra now, unresolved — a venv's bin/python is a symlink,
                      and resolving it leaves the venv (`No module named umbra`,
                      or an older umbra installed in the base interpreter)
    launchd_python()  what the installed job runs, read from its plist — not
                      the interpreter doctor happens to be running under (hit
                      on 2026-08-15 from a conda shell)
    tcc_binary()      the real file behind it, which is what Full Disk Access
                      checks
"""

import os
import plistlib
import tempfile
import unittest
from unittest import mock

from umbra import __main__ as m


class JobPythonTest(unittest.TestCase):
    def test_install_writes_the_running_interpreter_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(d, "python3.13")
            open(real, "w").close()
            venv_py = os.path.join(d, "venv-python")
            os.symlink(real, venv_py)
            with mock.patch("sys.executable", venv_py):
                self.assertEqual(m.job_python(), venv_py)

    def test_doctor_reads_the_installed_job_not_the_current_interpreter(self):
        with tempfile.TemporaryDirectory() as home:
            agents = os.path.join(home, "Library", "LaunchAgents")
            os.makedirs(agents)
            with open(os.path.join(agents, "com.umbra.sync.plist"), "wb") as f:
                plistlib.dump({"ProgramArguments": ["/opt/venv/bin/python", "-u"]}, f)
            with mock.patch.dict(os.environ, {"HOME": home}), \
                 mock.patch("sys.executable", "/Users/x/opt/miniconda3/bin/python3"):
                self.assertEqual(m.launchd_python(), "/opt/venv/bin/python")

    def test_no_job_installed_falls_back_to_what_install_would_write(self):
        with tempfile.TemporaryDirectory() as home, \
             mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(m.launchd_python(), m.job_python())

    def test_tcc_binary_resolves_the_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(d, "python3.13")
            open(real, "w").close()
            link = os.path.join(d, "python")
            os.symlink(real, link)
            with mock.patch.object(m, "launchd_python", lambda: link):
                self.assertEqual(m.tcc_binary(), os.path.realpath(real))


if __name__ == "__main__":
    unittest.main()
