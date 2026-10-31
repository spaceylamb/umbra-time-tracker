"""`umbra plugins` and the commands bundled plugins add to the CLI."""

import os
import sys

from .. import db


def _plugin(name: str):
    """A bundled lane, resolved the way a round resolves it.

    Through the registry rather than an import, so a plugin of the same name in
    your own directory is the one these commands drive — the same one the
    scheduled round runs. Importing the bundled module here would let the two
    disagree about which code is "the tasks lane".
    """
    from .. import registry
    found, errors = registry.plugins()
    if name in errors:
        raise SystemExit(f"  ✗ plugin {name!r} failed to load: {errors[name]}")
    if name not in found:
        raise SystemExit(f"  ✗ no plugin named {name!r} (umbra plugins lists them)")
    return found[name]


def cmd_plugins(args):
    """What is installed, what is on, and what is allowed to send.

    Three states, shown as three columns, because they are three decisions:
    installing a plugin, letting it analyse, and letting it put things in
    somebody else's system.
    """

    from .. import analyzer, config, plugins, registry, sink
    if args.action == "run":
        return _plugins_run(args)
    found, errors = registry.plugins()
    conf = config.load()
    if not found and not errors:
        print("  no plugins installed.")
    for name in sorted(found):
        mod = found[name]
        kinds = [k for k, ok in (("analyzer", getattr(mod, "INPUT", None)),
                                 ("check", registry.has_round(mod)
                                  and not getattr(mod, "INPUT", None)),
                                 ("sink", callable(getattr(mod, "send", None))))
                 if ok]
        where = ("bundled" if os.path.dirname(os.path.abspath(mod.__file__))
                 .startswith(os.path.abspath(plugins.DIR)) else "yours")
        is_analyzer = bool(getattr(mod, "INPUT", None))
        # A sink-only plugin is not switched on or off: it is used when an
        # analyzer names it. Showing it as "off" would read as a problem.
        on = registry.enabled(name, conf) if registry.has_round(mod) else None
        state = "—" if on is None else ("on" if on else "off")
        line = (f"  {'●' if on else '○'} {name:12s} {'/'.join(kinds) or '—':16s} "
                f"{where:8s} {state:3s}")
        if is_analyzer:
            win = getattr(mod, "WINDOW", 30)
            try:
                analyzer.resolve_input(mod.INPUT)
                analyzer.resolve_window(win)
                line += f"   reads {mod.INPUT}, window {win}"
            except analyzer.BadDeclaration as e:
                line += f"   ✗ {e}"
        to = sink.enabled_for(name, conf)
        if to:
            line += f"   sends to {to}"
        print(line.rstrip())
    for name, err in sorted(errors.items()):
        print(f"  ✗ {name:12s} failed to load: {str(err)[:120]}")


def _plugins_run(args):
    """Run one plugin now: its own round, or a given day or week."""
    import datetime as _dt

    from .. import analyzer
    if not args.name:
        raise SystemExit("  usage: umbra plugins run <name> [--day D | --week 2026-W38]")
    mod = _plugin(args.name)
    from .. import registry
    if not registry.has_round(mod):
        raise SystemExit(f"  ✗ {args.name} is a sink — there is nothing to run")
    if not getattr(mod, "INPUT", None):
        # A check with a round of its own and no timeline to read: run it.
        con = db.connect()
        try:
            mod.sync(con, [], print)
        finally:
            con.close()
        return
    con = db.connect()
    try:
        if args.day or args.week:
            if args.week:
                y, w = int(args.week[:4]), int(args.week[6:])
                mon = _dt.date.fromisocalendar(y, w, 1)
                days = [(mon + _dt.timedelta(i)).isoformat() for i in range(7)]
            else:
                days = [args.day]
            r = analyzer.run_days(con, args.name, mod, days, log=print)
            print(f"  {args.name}: {len(days)} days · asked {r['called']} · "
                  f"reused {r['reused']} · failed {r['failed']} · rows {r['rows']}")
            for day, err in r["errors"].items():
                print(f"  ⚠ {day}: {err}")
            # A window whose call failed is a question still open, not an
            # answer of nothing. Exit non-zero so whatever started this —
            # the page, a script — can tell.
            if r["errors"] or r["failed"]:
                raise SystemExit(1)
        else:
            today = str(_dt.date.today())
            analyzer.sync_plugin(con, args.name, mod, [today], log=print)
    finally:
        con.close()


def _plugin_commands(sub) -> None:
    """A command for every installed plugin that declares `cli(args)`: named
    CLI_NAME (the plugin's name otherwise), described by CLI_HELP, with its
    arguments added by `cli_args(parser)`.

    Read off the registry, so a plugin of the same name in your own directory
    replaces the command along with the lane — the command and the scheduled
    round drive the same code. A plugin cannot take a core command's name:
    `umbra sync` meaning something a plugin decided would be a surprise nobody
    could see coming. It is skipped, and said so.
    """
    from .. import registry
    found, _ = registry.plugins()
    taken = set(sub.choices)
    for name, mod in sorted(found.items()):
        fn = getattr(mod, "cli", None)
        if not callable(fn):
            continue
        cmd = getattr(mod, "CLI_NAME", None) or name
        if cmd in taken:
            print(f"  ⚠ plugin {name!r} wants the command {cmd!r}, which is taken; "
                  "it has no command", file=sys.stderr)
            continue
        taken.add(cmd)
        pc = sub.add_parser(cmd, help=getattr(mod, "CLI_HELP", None))
        if callable(getattr(mod, "cli_args", None)):
            mod.cli_args(pc)
        pc.set_defaults(func=fn)
