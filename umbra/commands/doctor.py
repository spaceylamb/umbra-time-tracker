"""`umbra doctor` and `umbra schedule`: which Python the scheduled job runs,
what it is allowed to read, and the launchd / systemd jobs themselves."""

import os
import sqlite3
import sys

from .. import ops


# The bundle launchd actually runs for the collector (see collector._tcc_target).
# Only a framework build (python.org, Homebrew) has one; it sits in the base
# installation, not in a venv.
PY_APP = os.path.join(sys.base_prefix, "Resources", "Python.app")


def job_python() -> str:
    """The interpreter `umbra schedule install` writes into the job: the one
    running umbra now, **unresolved**.

    Unresolved is the point. A uv / pipx / venv install's bin/python is a
    symlink to a base interpreter, and Python finds its environment by looking
    beside the path it was started from. Resolve it and the job starts the bare
    base interpreter: `No module named umbra` on a clean machine, or — worse —
    an older umbra installed there, running quietly. Full Disk Access is a
    separate question with its own answer (tcc_binary()).
    """
    return sys.executable


def launchd_python() -> str:
    """The interpreter the installed job actually runs — read from its plist.

    Not sys.executable: that is **the interpreter running doctor now**. Run
    from a `(base)` conda environment it names conda's python while launchd
    runs another. A diagnostic naming the wrong thing is worse than none: you
    would grant an interpreter that plays no part, still fail, and have no idea
    why (hit on 2026-08-15). Only with no job installed does it fall back to
    the one install would write.
    """
    import plistlib
    path = os.path.expanduser(f"~/Library/LaunchAgents/{ops.LABEL_SYNC}.plist")
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)["ProgramArguments"][0]
    except (OSError, KeyError, IndexError, plistlib.InvalidFileException):
        return job_python()


def tcc_binary() -> str:
    """What to grant Full Disk Access to: the real file behind the scheduled
    interpreter. TCC checks the image executed, not a symlink to it."""
    return os.path.realpath(launchd_python())


def _designated_req(path: str) -> str:
    """A bundle's own designated requirement — to compare with the csreq TCC
    stored."""
    import subprocess
    try:
        r = subprocess.run(["codesign", "-d", "-r-", path],
                           capture_output=True, timeout=10)
        out = (r.stdout + r.stderr).decode(errors="replace")
        for line in out.splitlines():
            if line.startswith("designated =>"):
                return line[len("designated =>"):].strip()
        return out.strip().splitlines()[-1] if out.strip() else "(none)"
    except Exception as e:                                  # noqa: BLE001
        return f"(unavailable: {e})"


def _decode_csreq(blob: bytes) -> str:
    """Decode a binary requirement from TCC into words. `csreq -r- -t` reads
    stdin and prints text."""
    import subprocess
    try:
        r = subprocess.run(["csreq", "-r-", "-t"], input=blob,
                           capture_output=True, timeout=5)
        return (r.stdout or r.stderr).decode(errors="replace").strip() or "(empty)"
    except Exception as e:                                  # noqa: BLE001
        return f"(could not decode: {e})"


def cmd_doctor(_args=None):
    """Diagnose TCC permissions. Run this first when knowledgeC cannot be read."""
    import subprocess
    import sys

    from ..importers import knowledgec

    print(f"Python : {sys.executable}")
    print("Host process chain (TCC grants go to the GUI app on this chain):")
    pid = os.getpid()
    for _ in range(8):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            if not out:
                break
            ppid, comm = out.split(None, 1)
            print(f"    {comm}")
            pid = int(ppid)
            if pid <= 1:
                break
        except Exception:                               # noqa: BLE001
            break

    # The TCC grant list. System Settings shows every Python entry as "Python"
    # with the bundle id org.python.python — impossible to tell apart by eye.
    # Only the database says which was granted.
    #
    # Both services matter: the collector needs Accessibility, knowledgec needs
    # SystemPolicyAllFiles (Full Disk Access). They are **independent** records;
    # granting one is not granting the other — though the UI makes them look the
    # same.
    SERVICES = [("kTCCServiceAccessibility", "Accessibility (needed by collector)"),
                ("kTCCServiceSystemPolicyAllFiles", "Full Disk Access (needed by knowledgec)")]
    tcc = "/Library/Application Support/com.apple.TCC/TCC.db"
    for service, label in SERVICES:
        print(f"\n{label} grant list:")
        try:
            con = sqlite3.connect(f"file:{tcc}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT client, auth_value, csreq FROM access "
                "WHERE service=? ORDER BY auth_value DESC", (service,)).fetchall()
            con.close()
        except sqlite3.OperationalError as e:
            print(f"    cannot read TCC.db ({e}) — this step itself needs Full Disk Access.")
            continue
        hits = [r for r in rows if "python" in (r[0] or "").lower()]
        if not rows:
            print("    (empty)")
        for client, auth, csreq in rows:
            mark = "✓ allowed" if auth == 2 else ("✗ denied" if auth == 0 else f"? auth={auth}")
            print(f"    {mark}  {client}")
            # auth_value=2 only means "allowed"; TCC also checks the csreq — the
            # code requirement the record was bound to. A mismatch is still denied,
            # with no sign of it in the UI.
            if "python" in (client or "").lower() and csreq:
                print(f"        csreq: {_decode_csreq(csreq)}")
        if not hits:
            print("    ⚠️ no python on the list — this permission is not granted to Python")
        # "Not in the list" and "in the list but denied" are told apart: for the
        # latter, adding entries does nothing — an explicit denial wins, and that
        # entry has to be changed or removed first.
        denied = [c for c, a, _ in rows if a == 0 and c == tcc_binary()]
        if denied:
            print(f"    ⛔ the scheduled job's binary is EXPLICITLY denied: {denied[0]}")
            print("       Not a missing entry — auth_value=0. Adding others won't help;")
            print("       flip this one on in System Settings, or remove it with − and rerun.")

    # The control: the binary the scheduled job actually runs. TCC checks **the
    # image executed**, and when python3 runs an ordinary script that image is
    # bin/python3.13, not Python.app — granting only Python.app still leaves
    # ordinary scripts denied.
    print("\n    The binary the scheduled job actually runs (find it in the FDA list):")
    print(f"        {tcc_binary()}")
    cur = os.path.realpath(sys.executable)
    if cur != tcc_binary():
        # The interpreter you are in is not the one scheduled jobs use — so the
        # thing to grant is different too.
        print("    ⚠️ doctor is running under a different interpreter — don't grant the wrong one:")
        print(f"        {cur}")
    if os.path.isdir(PY_APP):
        print("    The bundle the collector (PyObjC) reports, and its designated requirement:")
        print(f"        {PY_APP}")
        print(f"        {_designated_req(PY_APP)}")

    print("\nknowledgeC.db :", end=" ")
    try:
        all_streams = knowledgec.streams()
        rows = knowledgec.collect()
    except knowledgec.PermissionDenied as e:
        print("✗ unreadable")
        print(f"    {e}")
        print("\n  How to fix:")
        print("    1. System Settings → Privacy & Security → Full Disk Access")
        print("    2. Add the terminal app you actually use (the one in the chain above),")
        print("       not Terminal.app — TCC grants per executable; the wrong app does nothing")
        print("    3. Fully quit and reopen that app afterwards (Cmd-Q, not closing the window);")
        print("       a running process never picks up newly granted permissions")
        return
    print(f"✓ readable, {len(all_streams)} streams")
    print(f"\n{'stream':<38} {'rows':>8}   range")
    print("-" * 72)
    for name, n, lo, hi in all_streams[:30]:
        print(f"{(name or '(null)'):<38} {n:>8}   {lo} → {hi}")

    if not rows:
        print("\n⚠️ the foreground-app streams (/app/inFocus etc.) have no data.")
        print("   Look through the list above for a stream that records app usage.")
        return
    import datetime as _dt
    fmt = "%Y-%m-%d"
    lo = _dt.datetime.fromtimestamp(min(r[1] for r in rows) / 1000).strftime(fmt)
    hi = _dt.datetime.fromtimestamp(max(r[1] for r in rows) / 1000).strftime(fmt)
    print(f"\n✓ {len(rows)} foreground-app records, {lo} → {hi}")
    print("  run: python3 -m umbra import --only knowledgec")


def cmd_schedule(args):
    """Generate and load the scheduled jobs (launchd on macOS; elsewhere, print
    configuration to copy)."""
    py = job_python()
    jobs = [(ops.LABEL_SYNC, ["-m", "umbra", "sync"],
             ops.interval_min() * 60)]
    if ops.split_voice():
        jobs.append((ops.LABEL_VOICE, ["-m", "umbra", "voice"],
                     ops.voice_interval_min() * 60))
    if args.collector:
        jobs.append((ops.LABEL_COLLECTOR, ["-m", "umbra", "collect"], None))

    if sys.platform != "darwin":
        svc, timer = ops.systemd_unit(["-m", "umbra", "sync"],
                                      sys.executable, ops.repo_dir())
        print("# ~/.config/systemd/user/umbra-sync.service")
        print(svc)
        print("# ~/.config/systemd/user/umbra-sync.timer")
        print(timer)
        print("# systemctl --user enable --now umbra-sync.timer")
        return

    import subprocess
    agents = os.path.expanduser("~/Library/LaunchAgents")
    uid = os.getuid()
    for label, argv, every in jobs:
        path = os.path.join(agents, label + ".plist")
        if args.action == "uninstall":
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                           capture_output=True)
            if os.path.exists(path):
                os.remove(path)
            print(f"  uninstalled {label}")
            continue
        if args.action == "status":
            r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                               capture_output=True, text=True)
            state = "loaded" if r.returncode == 0 else "not loaded"
            print(f"  {label}: {state}")
            continue
        if args.action == "logs":
            short = label.rsplit(".", 1)[-1]
            for suffix in (".log", ".err.log"):
                print(f"  tail -f {ops.LOG_DIR}/{short}{suffix}")
            continue
        # install
        os.makedirs(agents, exist_ok=True)
        os.makedirs(ops.LOG_DIR, exist_ok=True)
        with open(path, "w") as f:
            # Freeze the current PATH into it: wrangler / netlify / aws in a
            # [deploy] command find node/python again through their shebangs,
            # and launchd provides only /usr/bin:/bin:/usr/sbin:/sbin. See ops.plist.
            f.write(ops.plist(label, argv, every, py,
                              ops.repo_dir(), ops.LOG_DIR,
                              path=os.environ.get("PATH")))
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                       capture_output=True)          # unload the old one first; idempotent
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", path],
                           capture_output=True, text=True)
        ok = "✓" if r.returncode == 0 else f"✗ {r.stderr.strip()}"
        print(f"  {ok} {label} → {path}"
              + (f" (every {every // 60} min)" if every else " (resident)"))
    # Warn if the old wrapper chain is still loaded — running both would do
    # everything twice (harmless, being idempotent, but the logs get confusing).
    if args.action == "install":
        legacy = [l for l in os.listdir(agents)
                  if ("shadow" in l or "umbra" in l) and l.endswith(".plist")
                  and not l.startswith("com.umbra.")]
        for l in legacy:
            print(f"  ⚠ legacy job still loaded: {l} — once the new one works, "
                  f"launchctl bootout gui/{uid}/{l[:-6]} and delete the file")
