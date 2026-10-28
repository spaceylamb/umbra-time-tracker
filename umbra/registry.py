"""Which module owns a given source name, downstream of collection.

Collecting a source is only half of connecting it. Four other places decide
whether it is ever seen again — whether the model reads it, whether the rule
layer can classify it, whether it gets a row on the page, and whether its
durations mean anything. Each of those used to hold its own table keyed by
source name, so a source not listed in all four was silently half-present:
importing fine, counted in `umbra stats`, green in the freshness report, and
invisible to the judgement.

This module is the single lookup those four now share. A source declares what it
is; the consumers ask here rather than each keeping a list.

## What a source may declare

    def render(event) -> str | None
        The one line the model reads. None means this record has no position in
        time (a daily total, a score, a gap) and must not appear at all.

    def classify(event) -> str | None
        The taxonomy category this event evidences, for the offline rule layer.
        None is a legitimate answer — "somebody was at the keyboard" is evidence
        without being an activity.

    LANES = [{"key": ..., "kind": ..., "origin": (short, long)}, ...]
        Rows on the page. A list because one source can carry several kinds of
        time that must not be drawn together: WHOOP sleep and WHOOP workouts are
        one source and two lanes, and merging them would draw "lay down for 8
        hours" and "trained for 40 minutes" as the same thing.

    DURATION = "trusted" | "point" | {source: ...}
        Whether dur_ms means attention. Default is "point". chrome's
        visit_duration is tab lifetime and reaches hours for a background tab
        (one real record: 17,942s), so claiming it is attention blows the
        timeline apart. A module answering for more than one source name (see
        streams.py) declares a dict, because the answer differs per source.

    STALE_DAYS, STALE_HINT
        This source should have something every normal day: this many quiet
        days mean it has stopped, and the hint is where to look first. Read by
        the staleness plugin; leave out for a source that is quiet for days for
        no reason.

Every one of these is optional. A source that declares none still imports, and
still shows its raw records in the drill-down — it simply does not participate in
the layers above. That is the honest default: absent is better than guessed.

## Resolution order

User sources win over built-ins of the same name, exactly as in
sources.discover(): dropping your own chrome.py into the config directory
replaces the built-in, and it should replace all of it, not only collect().
"""

import threading

_CACHE: dict | None = None
_LOCK = threading.Lock()


def _builtin() -> dict:
    """Stored source name -> the module that owns it.

    Keyed by SOURCE rather than by module name: knowledgec writes rows whose
    source is "app", and a lookup by module name would never find them.
    """
    from .importers import ALL
    out = {getattr(mod, "SOURCE", name): mod for name, mod in ALL.items()}
    # live and idle are written continuously by the resident collector, which
    # has no collect() and cannot be imported off macOS (it pulls in PyObjC).
    # streams.py describes those rows without being able to produce them.
    from . import streams
    out["live"] = streams
    out["idle"] = streams
    # Your own corrections are written by you (the page, `umbra correct`), never
    # collected; correct.py describes them the same way.
    from . import correct
    out[correct.SOURCE] = correct
    return out


def specs(refresh: bool = False) -> dict:
    """Source name -> module. Cached; call with refresh=True after adding one.

    The cache matters: render() is called once per event, tens of thousands of
    times a day, and sources.discover() reads the filesystem. It is built under
    a lock because the first inference of a day runs four windows concurrently.
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    with _LOCK:
        if _CACHE is not None and not refresh:
            return _CACHE
        out = _builtin()
        try:
            from . import sources
            found, _errors = sources.discover()
            for name, mod in found.items():
                out[getattr(mod, "SOURCE", name)] = mod
        except Exception:                                   # noqa: BLE001
            # A broken user source must not take down rendering for every other
            # source. discover() already records the error for `umbra sources`
            # to report, which is where it belongs — this path only has to keep
            # the built-ins working.
            pass
        _CACHE = out
        return out


def reload() -> None:
    """Drop the cache. For tests, and for anything that adds a source or plugin
    at runtime."""
    global _CACHE, _PLUGINS
    _CACHE = None
    _PLUGINS = None


# ── Plugins: Analyzers and Sinks ──────────────────────────────
#
# Discovered separately from sources and cached separately, because a broken
# plugin must not be able to take source rendering down with it. Rendering runs
# once per event, tens of thousands of times a day; an exception there would not
# break one plugin, it would empty the digest for every source on every day.

_PLUGINS: dict | None = None


def plugins(refresh: bool = False) -> tuple[dict, dict]:
    """(name -> module, name -> why it failed to load).

    Failures are returned rather than raised, on the same rule
    sources.discover() follows: one unimportable file must not stop the others,
    and the error must stay visible, because "why didn't my plugin run" needs an
    answer.

    Bundled and user plugins are found the same way and loaded differently, and
    the difference is not cosmetic. A bundled plugin is a real member of this
    package and is imported as one, so its `from ... import` reaches the rest of
    umbra normally. A user plugin is a loose file outside any package and has to
    be loaded by path. Loading a bundled plugin by path instead would namespace
    it outside umbra and break every relative import it makes.
    """
    global _PLUGINS
    if _PLUGINS is not None and not refresh:
        return _PLUGINS
    with _LOCK:
        if _PLUGINS is not None and not refresh:
            return _PLUGINS
        found, errors = {}, {}
        for d, bundled in _plugin_dirs():
            for name, path in _candidates(d):
                try:
                    found[name] = (_import_bundled(name, path) if bundled
                                   else _import_file(name, path))
                except Exception as e:                      # noqa: BLE001
                    errors[name] = f"{type(e).__name__}: {e}"
        _PLUGINS = (found, errors)
        return _PLUGINS


def _candidates(d: str) -> list[tuple[str, str]]:
    """(name, path) for everything in one directory that looks like a plugin.

    A plugin is one .py file, or a directory with an __init__.py. Both, because
    the shape follows the size: a sink is often twenty lines and wants a file,
    while a lane with a prompt, a parser and its own filters wants somewhere to
    put them.
    """
    import os
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.startswith("_") or fn.startswith("."):
            continue
        path = os.path.join(d, fn)
        if os.path.isdir(path):
            init = os.path.join(path, "__init__.py")
            if os.path.exists(init):
                out.append((fn, init))
        elif fn.endswith(".py"):
            out.append((fn[:-3], path))
    return out


def _import_bundled(name: str, path: str):
    """Import a bundled plugin as the package member it is, when it is one.

    The package import is what makes a bundled plugin's `from ... import` reach
    the rest of umbra; loaded by path it would be namespaced outside the package
    and every relative import would fail.

    The fallback is not a silent catch of arbitrary failure — it fires only when
    there is no such module under umbra.plugins, which means the bundled
    directory is not the real one (a test pointing it elsewhere). A plugin that
    exists and fails to import still raises, and still lands in errors.
    """
    import importlib
    import importlib.util
    full = f"umbra.plugins.{name}"
    try:
        if importlib.util.find_spec(full) is not None:
            return importlib.import_module(full)
    except (ImportError, AttributeError, ValueError):
        pass
    return _import_file(name, path)


def _import_file(name: str, path: str):
    from . import sources
    return sources.load_file(path, name=name)


def _plugin_dirs() -> list[tuple[str, bool]]:
    """[(directory, is_bundled)], in increasing order of precedence.

    Bundled first, the user's own last, so a user file of the same name wins —
    the same rule a user source follows over a built-in. Whoever wrote the file
    in their own directory meant it.
    """
    from . import config, plugins
    return [(plugins.DIR, True), (config.PLUGINS_DIR, False)]


def enabled(name: str, conf: dict | None = None) -> bool:
    """Is this plugin switched on? Being installed is not being enabled.

    Each plugin says its own default (ENABLED_BY_DEFAULT). One that says
    nothing is off: an unknown plugin running by default is a decision made on
    the user's behalf. The bundled ones that can send are off, so installing
    Umbra never starts writing into somebody's inbox before they have seen a
    single result.
    """
    from . import config
    conf = config.load() if conf is None else conf
    spec = ((conf.get("plugins") or {}).get(name)) or {}
    if "enabled" in spec:
        return bool(spec["enabled"])
    mod = plugins()[0].get(name)
    # Before plugins existed, the scheduled round ran tasks and claims
    # extraction for everyone, and their sections in config.toml only decided
    # whether anything was *sent*. Someone with such a section (LEGACY_SECTION)
    # is running that lane today; upgrading must not quietly stop it. An
    # explicit [plugins.<name>] enabled always wins.
    if getattr(mod, "LEGACY_SECTION", False) and conf.get(name):
        return True
    return bool(getattr(mod, "ENABLED_BY_DEFAULT", False))


def analyzers(only_enabled: bool = False, conf: dict | None = None) -> dict:
    """The plugins that declared themselves Analyzers.

    A plugin with no INPUT is not one, and that is not an error — a Sink-only
    plugin is a legitimate thing to write.
    """
    found, _errors = plugins()
    out = {n: m for n, m in found.items() if getattr(m, "INPUT", None)}
    if only_enabled:
        out = {n: m for n, m in out.items() if enabled(n, conf)}
    return out


def has_round(mod) -> bool:
    """Does this plugin take part in a round? An Analyzer does (INPUT), and so
    does a plugin that only declares `sync(con, days, log)` — one that checks
    something every round and reads no layer of the timeline. A plugin with
    neither is a Sink, used when another one names it."""
    return bool(getattr(mod, "INPUT", None)) or callable(getattr(mod, "sync", None))


def rounds(only_enabled: bool = False, conf: dict | None = None) -> dict:
    """The plugins a round runs: every Analyzer, and every plugin with a round
    of its own (has_round). These are the ones with a switch."""
    found, _errors = plugins()
    out = {n: m for n, m in found.items() if has_round(m)}
    if only_enabled:
        out = {n: m for n, m in out.items() if enabled(n, conf)}
    return out


def spec(source: str):
    """The module owning this source name, or None if nothing claims it."""
    return specs().get(source)


def hook(source: str, name: str):
    """A source's optional declaration, or None.

    None is returned for both "no such source" and "that source declares
    nothing here", because the consumers treat them identically: neither can
    contribute, and both must be absent rather than invented.
    """
    mod = spec(source)
    fn = getattr(mod, name, None) if mod is not None else None
    return fn if callable(fn) else None


def attr(source: str, name: str, default=None):
    """A source's optional declared value (LANES, DURATION), or default."""
    mod = spec(source)
    return getattr(mod, name, default) if mod is not None else default


def classify(e) -> str | None:
    """The taxonomy category one event evidences, per its own source.

    None for a source that declares nothing, and None is also a legitimate
    declared answer: idle and live are evidence that somebody was or was not at
    the machine, which is not an activity anybody spent a slot doing.
    """
    fn = hook(e.get("source"), "classify")
    return fn(e) if fn is not None else None


def label(cat: str, e) -> str | None:
    """A source's own short label for one of its events, or None to fall back.

    Optional on purpose. A source that declares a category but no label gets the
    generic label for that category, which is the honest default — better a
    plain "Workout" than a source inventing detail it does not have.
    """
    fn = hook(e.get("source"), "label")
    return fn(cat, e) if fn is not None else None


def lanes() -> list[tuple]:
    """Every declared lane, as (key, source, kind), in registration order.

    Ordering is the page's vertical layout and is part of the design: the
    off-screen group is drawn above the dashed rule because it answers a
    different question from the screen sources. Sources keep the order they
    appear in ALL, and each source keeps the order of its own LANES.
    """
    out = []
    for source, mod in specs().items():
        for lane in getattr(mod, "LANES", ()) or ():
            out.append((lane["key"], source, lane.get("kind")))
    return out


def origins() -> dict:
    """Lane key -> (short line under the name, long line in the drill-down)."""
    out = {}
    for _source, mod in specs().items():
        for lane in getattr(mod, "LANES", ()) or ():
            if lane.get("origin"):
                out[lane["key"]] = tuple(lane["origin"])
    return out


def trusted_duration() -> set:
    """Sources whose dur_ms means attention rather than an artefact."""
    out = set()
    for source, mod in specs().items():
        d = getattr(mod, "DURATION", "point")
        if isinstance(d, dict):
            d = d.get(source, "point")
        if d == "trusted":
            out.add(source)
    return out
