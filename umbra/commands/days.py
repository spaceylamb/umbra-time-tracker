"""Looking at and correcting one day: `coverage`, `show`, `blocks`, `goals`, `correct`."""

import sys

from .. import analyze
from .. import db
from .. import inference as INF
from ..blocks import CAT_NAME


def cmd_coverage(args):
    print(f"{'day':<12} {'A content':>9} {'B container':>9} {'off-screen':>9}"
          f" {'C no data':>9} {'away':>8} {'suspect':>8}   {'events':>7}")
    print("-" * 88)
    for day in args.days:
        c = analyze.coverage(day)
        print(f"{c['day']:<12} {c['A']:>7}min {c['B']:>7}min {c['off']:>7}min"
              f" {c['C']:>7}min {c['away']:>6}min {c['ambiguous']:>6}min"
              f"   {c['events']:>7}")
    print("\nA = content-level evidence; we can say what was being done")
    print("B = only which app/domain; not what was actually happening")
    print("off-screen = WHOOP sleep/workouts: away from the computer but accounted for")
    print("C = no events at all")
    print("away = collector idle detection confirmed nobody present (excluded from the denominator)")
    print(f"suspect = ≥{analyze.STALE_FOCUS_MIN}min parked on one app; "
          "deep focus and absence look the same — this column shrinks once the "
          "collector runs")


def cmd_show(args):
    """Rule-based segments for one day. Offline, no model call.

    This used to make a second model call of its own, through a prompt and a
    hardcoded Gemini endpoint that lived in analyze.py — a duplicate of the
    inference path with a category list that had drifted out of step with
    taxonomy.py. That path is gone; `umbra infer <day>` is the model pass, and
    it is the only one.
    """
    segs = analyze.build(args.day)
    if not segs:
        print(f"no data for {args.day}")
        return
    print(f"=== {args.day} rule-based segments: {len(segs)} ===")
    print(analyze.render(segs))
    print(f"\nThis is the offline rule-based view. For the model's "
          f"reconstruction: umbra infer {args.day}")


def cmd_blocks(args):
    """A day cut into 5-minute slots, each saying what was going on.

    Runs of identical slots are merged by default — 288 raw rows are unreadable.
    --raw shows them all.
    """
    from .. import blocks as B
    con = db.connect()
    lo, hi = analyze.day_bounds(args.day, con)
    bs = B.blocks(db.load_range(con, lo, hi), lo, (hi - lo + 1) // 60000)

    if args.raw:
        for b in bs:
            cat = CAT_NAME.get(b["cat"], "—")
            print(f"{b['t']}  {cat:<7} {b['label'][:46]:<46} "
                  f"{'⚠' if b['conflict'] else ' '} {','.join(b['why'])}")
    else:
        runs, cur = [], None
        for b in bs:
            k = (b["cat"], b["label"], tuple(b["also"]))
            if cur and cur["k"] == k:
                cur["n"] += 1
                cur["cf"] |= bool(b["conflict"])
                continue
            if cur:
                runs.append(cur)
            cur = {"k": k, "n": 1, "t": b["t"], "i": b["i"],
                   "cf": bool(b["conflict"])}
        runs.append(cur)
        for r in runs:
            cat, lab, also = r["k"]
            end = (r["i"] + r["n"]) * B.SLICE_MIN
            extra = ("   + " + " / ".join(a[:30] for a in also)) if also else ""
            print(f"  {r['t']}–{end//60:02d}:{end%60:02d} {r['n']*B.SLICE_MIN:>4}min  "
                  f"[{CAT_NAME.get(cat, '(empty)')}] {lab[:40]}{extra}"
                  f"{'  ⚠' if r['cf'] else ''}")

    import collections
    c = collections.Counter(b["cat"] for b in bs)
    print(f"\n{args.day} · {B.SLICE_MIN}-minute slots, {len(bs)} total")
    for cat, n in c.most_common():
        print(f"  {CAT_NAME.get(cat, '(empty)'):<8} {n:>3} slots = {n*B.SLICE_MIN:>4}min"
              f"  {'█' * int(n/3)}")
    # Conflicts are for you to settle; blind spots only explain why a source was
    # silent. Counted apart — mixed, "⚠ how many still need a look" would be
    # permanently inflated.
    for fld, mark, name in (("conflict", "⚠", "slots in conflict"),
                            ("note", "ⓘ", "slots in a known blind spot")):
        n_blocks = sum(1 for b in bs if b[fld])
        if not n_blocks:
            continue
        print(f"\n  {mark} {name}: {n_blocks}")
        seen = collections.Counter()
        for b in bs:
            for x in b[fld]:
                seen[x] += 1
        for k, n in seen.most_common():
            print(f"      {n:>3} slots  {k}")


def cmd_goals(args):
    """This week's three most important things, and whether the time went there.

    The one place priorities are set from the terminal; the page sets them
    through `umbra serve`. Both write the same table, and saving new wording
    starts judging the week.
    """
    import datetime as dt

    from .. import db, goals as G, infer

    week = args.week or G.week_of(dt.date.today().isoformat())
    con = db.connect()

    if args.action == "set":
        texts = [t.strip() for t in (args.text or []) if t.strip()]
        # Three is a ceiling, not a suggestion. A fourth "most important thing"
        # means none of them is — the constraint is where the feature's value
        # comes from, so it does not bend.
        if len(texts) > 3:
            print(f"  ✗ at most three, you gave {len(texts)}")
            return
        if not texts:
            print('  usage: umbra goals set "first thing" "second thing" "third thing"')
            return
        G.save_goals(con, week, texts,
                     now_ms=int(dt.datetime.now().timestamp() * 1000))
        print(f"  ✓ {week}: {len(texts)} set")
        for i, t in enumerate(texts, 1):
            print(f"    {i}. {t}")
        print("\n  The next `umbra sync` credits this week's time to them.")
        return

    texts = G.load_goals(con, week)
    if not texts:
        print(f"  {week} has no priorities yet.")
        print('  umbra goals set "first thing" "second thing" "third thing"')
        return

    # The seven days: ISO week → that week's Monday
    y, w = int(week[:4]), int(week[6:])
    mon = dt.date.fromisocalendar(y, w, 1)
    days = [(mon + dt.timedelta(i)).isoformat() for i in range(7)]
    gdig = G.gdigest(texts)
    elapsed = [d for d in days if d <= dt.date.today().isoformat()]
    mins = G.totals(con, days, infer.MODEL, gdig)
    done = G.done_days(con, elapsed, infer.MODEL, gdig)

    print(f"  {week}  ({mon} – {mon + dt.timedelta(6)})")
    for i, t in enumerate(texts, 1):
        h = mins.get(i, 0) / 60          # nothing credited is 0 — "no time spent"
        mark = "✓" if h >= 10 else " "   # and "not judged yet" must not look alike
        print(f"   {mark} {i}. {t}")
        print(f"        {h:.1f} h   {h / 168 * 100:.1f}% of the week   floor 10 h")
    if len(done) < len(elapsed):
        print(f"\n  ⚠ only {len(done)} days of this week are judged — the numbers are incomplete")


def cmd_correct(args):
    """Your word for a stretch of the day: "that hour I was actually …".

        umbra correct 2026-09-12 18:00-19:00 read "reading over dinner"
        umbra correct --list 2026-09-12

    The category is required, the words are not (see correct.py): the category
    is what the page and the totals use at once; the words are what the model
    reads next round.
    """
    import datetime as _dt

    from .. import correct, infer, taxonomy
    con = db.connect()
    day = args.day or str(_dt.date.today())
    lang = infer.lang()
    if args.action == "list":
        rows = correct.load(con, day)
        if not rows:
            print(f"  {day}: nothing corrected.")
            return
        # The first-pass spans, not the calendar — see correct.disagrees.
        spans = INF.load(con, day).get(infer.MODEL) or []
        digests = INF.load_digests(con, day).get(infer.MODEL) or []
        for c in rows:
            print(f"  {correct.hm(c['a'])}–{correct.hm(c['b'])}  "
                  f"[{taxonomy.label(c['cat'], lang)}]  {c['text'] or '(category only)'}")
            if correct.read_by_model(c, digests) is False:
                print("        · the model has not re-read this since you said it (next round)")
                continue
            d = correct.disagrees(c, spans)
            # Three answers kept apart: said nothing / agrees / disagrees.
            if d is False:
                print("        ✓ the model agrees")
            elif d is None:
                print("        · the model has not said anything about this stretch yet")
            elif d and d.get("soft"):
                print(f"        · the model filed it as {taxonomy.label(d['cat'], lang)}, "
                      f"but also named what you said: {d['title']}")
            elif d:
                print(f"        ⚠ re-asked, the model still says {taxonomy.label(d['cat'], lang)}: "
                      f"{d['title']}")
        return
    if not (args.span and args.cat):
        sys.exit("usage: umbra correct <day> <18:00-19:00> <category> [\"what you were doing\"]\n"
                 f"  categories: {taxonomy.prompt_cats(lang)}")
    text = " ".join(args.text or [])
    try:
        a, b = correct.parse_span(args.span)
        correct.save(con, day, a, b, args.cat, text)
    except ValueError as e:
        sys.exit(f"  ✗ {e}")
    print(f"  ✎ {day} {args.span} → {taxonomy.label(taxonomy.norm(args.cat), lang)}"
          f"  {text or '(category only)'}")
    # Two sentences, because they happen at different times.
    print("  The page and the totals use it now.")
    print("  The next round re-asks the model about the windows it covers; yours stays as it is.")
