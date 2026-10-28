"""What you keep doing on a rhythm — and what of it could stop needing you.

Once a month, one card: things counted from sixteen weeks of your trail that
repeat on a steady rhythm (a page, a sign-in, a kind of mail, an instruction to
an agent, a place), each with its evidence, and a model's one sentence on
whether it still needs you. Only what does not goes on the card, grouped by
what can be done: unsubscribe, make it come to you, put it on a schedule, make
the sign-in last longer.

    detect   records -> candidates (counted; fold, recur, burst, project,
             offline, behavior underneath)          no model
    judge    candidates -> "does this need you"     the one model call
    card     suggestions -> one card a period       through a sink

`umbra review` prints the whole count with no model and no sink; it is the way
to see what the plugin would work from before switching it on.
"""

import datetime as dt

from ... import analyzer, config
from . import card, detect, judge

TITLE = "Things to automate"
WHAT = "A monthly list of things you keep doing by hand that could run on their own."
ORDER = 5
SEND_ONLY = True              # the monthly list is the whole point: on means sending it
TABLES = judge.TABLES

PLUGIN = "review"


def _conf() -> dict:
    return config.load()


def _today() -> str:
    return str(dt.date.today())


def _spec(conf: dict) -> dict:
    return ((conf.get("plugins") or {}).get(PLUGIN)) or {}


def _weeks(conf: dict) -> int:
    return int(_spec(conf).get("weeks") or detect.WINDOW_WEEKS)


def blocked(conf: dict | None = None) -> str | None:
    """Why this cannot run, or None. The card is the point; a review nobody is
    sent is a table nobody reads. (`umbra review` works regardless.)"""
    from ... import providers, sink
    conf = _conf() if conf is None else conf
    name = _spec(conf).get("sink")
    if not name:
        return ('Needs somewhere to send the monthly list: name a sink in config.toml '
                '([plugins.review] sink = "butler", a command or a webhook).')
    if sink.answers(name, conf) is None:
        return f'The sink "{name}" is not set up — add [sinks.{name}] to config.toml.'
    model = analyzer.model_for(PLUGIN, conf)
    st = providers.status(model, conf)
    if st.get("key_set") is False:
        return f"Needs a key for {model}: add {st.get('key_env')} to the key file."
    return None


def _state(con, k: str):
    row = con.execute("SELECT v FROM review_state WHERE k=?", (k,)).fetchone()
    return row[0] if row else None


def _set_state(con, k: str, v: str) -> None:
    with con:
        con.execute("INSERT OR REPLACE INTO review_state (k, v) VALUES (?, ?)", (k, v))


def sync(con, days, log=print):
    """One round. Counting sixteen weeks is not free, so it runs at most once a
    day, and not at all once this month's card has gone."""
    from ... import sink as S
    conf = _conf()
    why = blocked(conf)
    if why:
        log(f"  review: not running — {why}")
        return
    judge.ensure(con)
    today = _today()
    period = card.period_of(today)
    if card.sent(con, period) or _state(con, "last_day") == today:
        return
    _set_state(con, "last_day", today)
    weeks = detect.history_weeks(con)
    if weeks < detect.MIN_WEEKS:
        log(f"  review: waiting — {weeks:.1f} weeks of history; a rhythm needs "
            f"{detect.MIN_WEEKS} to show")
        return
    try:
        cands = detect.candidates(con, weeks=_weeks(conf), conf=conf)
        judge.run(con, cands, period, analyzer.model_for(PLUGIN, conf), log=log)
    except Exception as e:                                  # noqa: BLE001
        log(f"  ✗ review: {e}")
        return
    card.send(con, S.enabled_for(PLUGIN, conf), today, log=log)


# ── The command line (umbra review) ──────────────────────────

CLI_NAME = "review"
CLI_HELP = "what you keep doing on a rhythm: the count, the model's verdicts, the monthly card"


def cli_args(p) -> None:
    p.add_argument("action", nargs="?", default="count",
                   choices=["count", "judge", "preview", "send", "verdict", "hits"])
    p.add_argument("rest", nargs="*", help="verdict: <id> useful|already|not_worth|wrong [note]")
    p.add_argument("--weeks", type=int, default=None, help="window (default 16)")


def cli(args):
    from ... import db
    from ... import sink as S
    conf = _conf()
    con = db.connect()
    try:
        judge.ensure(con)
        weeks = args.weeks or _weeks(conf)
        if args.action == "count":
            print(detect.render(con, weeks=weeks, conf=conf))
            print("\n  (umbra review judge asks the model which of these still need you)")
        elif args.action == "judge":
            got = judge.run(con, detect.candidates(con, weeks=weeks, conf=conf),
                            card.period_of(_today()), analyzer.model_for(PLUGIN, conf),
                            log=print)
            for r in got:
                mark = "you " if r["needs_you"] else "    "
                print(f"  #{r['id']:<4} {mark} [{r['lever'] or '-'}] ×{r['n']} {r['title']}")
            if judge.omitted(con):
                print(f"\n  {judge.omitted(con)} asked about and not answered by the model")
        elif args.action == "preview":
            prev = card.preview(con, _today())
            if not prev:
                print("  nothing for a card yet (umbra review judge first)")
            else:
                print(f"  {prev['item']['title']}\n\n{prev['item']['body']}")
                print("\n  Nothing was sent. To send: umbra review send")
        elif args.action == "send":
            name = S.enabled_for(PLUGIN, conf)
            if not name:
                raise SystemExit('  ✗ no sink: [plugins.review] sink = "butler" (or a command, a webhook)')
            if not card.send(con, name, _today(), log=print):
                print("  nothing sent (already sent this month, or nothing ready)")
        elif args.action == "verdict":
            if len(args.rest) < 2:
                raise SystemExit("  usage: umbra review verdict <id> "
                                 "useful|already|not_worth|wrong [note]")
            judge.verdict(con, int(args.rest[0]), args.rest[1], " ".join(args.rest[2:]))
            print(f"  #{args.rest[0]}: {args.rest[1]}")
        elif args.action == "hits":
            got = judge.hit_rate(con)
            print("  " + (" · ".join(f"{k} {v}" for k, v in sorted(got.items()))
                          or "no verdicts yet"))
    finally:
        con.close()
