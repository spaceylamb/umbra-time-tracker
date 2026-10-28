"""Model providers: a model name maps to one call(prompt) -> text function.

Switching models is a config change, not a code change. Two built-in kinds
cover nearly everyone:

    gemini   the native generativelanguage API (today's default, works with
             zero configuration)
    openai   any OpenAI-compatible /chat/completions — one base_url covers
             OpenAI, Ollama, vLLM, llama.cpp and Fireworks alike.
             "Use an open model" reduces to: run a server, set a URL.
    anthropic  Claude's Messages API

gemini*, gpt* and claude* work with nothing but their key (GEMINI_API_KEY,
OPENAI_API_KEY, ANTHROPIC_API_KEY).

Example (~/.config/umbra/config.toml):

    [providers.ollama]
    kind = "openai"
    base_url = "http://localhost:11434/v1"
    models = ["llama", "qwen"]          # matched by model-name prefix
    # api_key_env = "OPENAI_API_KEY"    # omit for a local server

Match order is user config first, built-in defaults last — so pointing
gemini* at your own proxy is a matter of declaring models = ["gemini"].

## Why routing is by model-name prefix rather than an explicit provider field

The inferences and day_events tables record the *model name*; that name is the
model's identity in this system. Letting one name be both the identity and the
routing key makes "the record says A ran this, but it was actually routed to B"
impossible to express.
"""

import os
import urllib.error
import urllib.parse
import urllib.request

from . import config, net

_GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
               "{model}:generateContent?key={key}")

_OPENAI_URL = "https://api.openai.com/v1"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2026-10-21"

# Built-in defaults: each big provider's own models work with nothing but its
# key. gemini* was the behaviour before this abstraction; gpt* and claude*
# joined when fact-checking needed a model that searches, and those three are
# the ones that do.
_BUILTIN = [("gemini", {"kind": "gemini"}),
            ("gpt", {"kind": "openai", "base_url": _OPENAI_URL,
                     "api_key_env": "OPENAI_API_KEY"}),
            ("claude", {"kind": "anthropic"})]

#: The most searches one lookup may run. A fact is one to three searches; the
#: cap is there so a confused model cannot turn one claim into twenty bills.
MAX_SEARCHES = 3


def _need_key(env: str) -> None:
    """Missing key: say so once, when the caller asks for a model — not once
    per window from inside every call, which turned one missing line in the
    env file into forty identical errors a day."""
    if not os.environ.get(env):
        from . import ops
        raise RuntimeError(f"{env} is not set — add it to {ops.env_file()} "
                           "(KEY=value), or pick a model that needs no key")


def _gemini(model: str, spec: dict):
    _need_key(spec.get("api_key_env") or "GEMINI_API_KEY")

    def call(prompt: str) -> str:
        key = os.environ.get(spec.get("api_key_env") or "GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set — cannot run inference")
        d = net.post_json(_GEMINI_URL.format(model=model, key=key),
                          {"contents": [{"parts": [{"text": prompt}]}]})
        return d["candidates"][0]["content"]["parts"][0]["text"]
    return call


def _openai(model: str, spec: dict):
    url = spec["base_url"].rstrip("/") + "/chat/completions"
    if spec.get("api_key_env"):
        _need_key(spec["api_key_env"])

    def call(prompt: str) -> str:
        headers = {}
        env = spec.get("api_key_env")
        if env:
            key = os.environ.get(env)
            if not key:
                raise RuntimeError(
                    f"provider sets api_key_env = {env!r} but it is not in the environment")
            headers["Authorization"] = f"Bearer {key}"
        d = net.post_json(url, {"model": model,
                                "messages": [{"role": "user",
                                              "content": prompt}]},
                          headers=headers or None)
        return d["choices"][0]["message"]["content"]
    return call


def _anthropic_headers(spec: dict) -> dict:
    env = spec.get("api_key_env") or "ANTHROPIC_API_KEY"
    key = os.environ.get(env)
    if not key:
        raise RuntimeError(f"{env} is not set — cannot call Claude")
    return {"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION}


def _anthropic(model: str, spec: dict):
    _need_key(spec.get("api_key_env") or "ANTHROPIC_API_KEY")
    url = spec.get("base_url") or _ANTHROPIC_URL

    def call(prompt: str) -> str:
        d = net.post_json(url, {"model": model, "max_tokens": 16000,
                                "messages": [{"role": "user", "content": prompt}]},
                          headers=_anthropic_headers(spec))
        return "".join(b.get("text", "") for b in d.get("content") or []
                       if b.get("type") == "text")
    return call


_KINDS = {"gemini": _gemini, "openai": _openai, "anthropic": _anthropic}


def _spec(model: str, conf: dict) -> dict | None:
    """The provider entry a model name routes to: user config first, built-in
    defaults last. None when nothing matches."""
    table = [(p, spec)
             for spec in (conf.get("providers") or {}).values()
             for p in spec.get("models", [])]
    for prefix, spec in table + _BUILTIN:
        if model.startswith(prefix):
            return spec
    return None


def resolve(model: str, conf: dict | None = None):
    """Model name -> call(prompt). Unknown names raise something actionable."""
    conf = config.load() if conf is None else conf
    spec = _spec(model, conf)
    if spec is not None:
        make = _KINDS.get(spec.get("kind"))
        if make is None:
            raise RuntimeError(
                f"unknown provider kind {spec.get('kind')!r}; "
                f"available: {', '.join(sorted(_KINDS))}")
        return make(model, spec)
    raise RuntimeError(
        f"no provider matches {model!r}. Add to ~/.config/umbra/config.toml:\n"
        "  [providers.somename]\n"
        '  kind = "openai"          # or "gemini", "anthropic"\n'
        '  base_url = "http://localhost:11434/v1"\n'
        f'  models = ["{model.split("-")[0]}"]   # matched by model-name prefix')


def status(model: str, conf: dict | None = None) -> dict:
    """What the page needs to know about the model, **without the key**.

    key_set is None for a provider that needs no key (a local server): there
    is nothing to be missing, and "no key" must not read as a problem there.
    """
    conf = config.load() if conf is None else conf
    spec = _spec(model, conf)
    if spec is not None:
        kind = spec.get("kind")
        env = spec.get("api_key_env") or {"gemini": "GEMINI_API_KEY",
                                          "anthropic": "ANTHROPIC_API_KEY"}.get(kind)
        from . import ops
        return {"name": model, "kind": kind or "?",
                "key_env": env, "env_file": ops.env_file(),
                "key_set": bool(os.environ.get(env)) if env else None,
                "local": kind == "openai" and not env}
    return {"name": model, "kind": None, "key_env": None, "env_file": None,
            "key_set": None, "local": False}


# ── Looking things up ─────────────────────────────────────────
#
# lookup(model) -> ask(prompt) -> {"text", "sources", "searches"}
#
# **Sources come from the provider's own record of what it searched, never
# from the answer text.** A model can write any URL into a sentence; grounding
# metadata, citations and search results list only pages the search returned.
# Each provider keeps that record somewhere different, so each has its own
# reader below; the shapes were taken from real calls on 2026-10-24.

def _can_search(spec: dict) -> bool:
    """Whether a provider entry can run a web search. OpenAI-compatible servers
    other than OpenAI's own (Ollama, vLLM, ...) have no search tool; `search =
    true` on the entry says one does (a proxy in front of OpenAI, say)."""
    kind = spec.get("kind")
    if kind in ("gemini", "anthropic"):
        return True
    if kind == "openai":
        if "search" in spec:
            return bool(spec["search"])
        host = urllib.parse.urlsplit(spec.get("base_url") or "").hostname or ""
        return host == "api.openai.com"
    return False


def _clean_url(url: str) -> str:
    """Drop tracking parameters (OpenAI appends utm_source=openai)."""
    parts = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                    urllib.parse.urlencode(q), parts.fragment))


def _sources(pairs) -> list[dict]:
    """[(url, title)] -> [{"url", "title"}], cleaned, first occurrence kept."""
    out, seen = [], set()
    for url, title in pairs:
        if not url:
            continue
        url = _clean_url(url)
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": title or ""})
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _open_no_redirect(url: str):
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=net.ssl_context()))
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": net.USER_AGENT})
    try:
        with opener.open(req, timeout=15) as r:
            return r.headers.get("Location")
    except urllib.error.HTTPError as e:      # a 30x lands here once not followed
        return e.headers.get("Location")


def _unredirect(url: str) -> str:
    """Where a redirect link points, without fetching the page. Gemini's
    grounding links are redirects that stop working after a while, and a source
    opened next week has to be the page itself. Anything that goes wrong keeps
    the link as it was: a working-for-now link beats none."""
    try:
        return _open_no_redirect(url) or url
    except Exception:                                     # noqa: BLE001
        return url


def _gemini_lookup(model: str, spec: dict):
    env = spec.get("api_key_env") or "GEMINI_API_KEY"
    _need_key(env)

    def ask(prompt: str) -> dict:
        d = net.post_json(_GEMINI_URL.format(model=model, key=os.environ.get(env)),
                          {"contents": [{"parts": [{"text": prompt}]}],
                           "tools": [{"google_search": {}}]})
        c = (d.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts") or []
                       if not p.get("thought"))
        g = c.get("groundingMetadata") or {}
        webs = [ch.get("web") or {} for ch in g.get("groundingChunks") or []]
        return {"text": text,
                "sources": _sources((_unredirect(w.get("uri", "")), w.get("title"))
                                    for w in webs if w.get("uri")),
                "searches": len(g.get("webSearchQueries") or [])}
    return ask


def _openai_lookup(model: str, spec: dict):
    env = spec.get("api_key_env")
    if env:
        _need_key(env)
    url = spec["base_url"].rstrip("/") + "/responses"

    def ask(prompt: str) -> dict:
        headers = {"Authorization": f"Bearer {os.environ.get(env)}"} if env else None
        # tool_choice "required": asked "is this true?", gpt-6-luna answered a
        # well-known myth from memory without searching (measured 2026-10-24).
        d = net.post_json(url, {"model": model, "input": prompt,
                                "tools": [{"type": "web_search"}],
                                "tool_choice": "required",
                                "max_tool_calls": MAX_SEARCHES,
                                "include": ["web_search_call.action.sources"]},
                          headers=headers)
        out = d.get("output") or []
        texts, cites = [], []
        for it in out:
            if it.get("type") != "message":
                continue
            for c in it.get("content") or []:
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
                    cites += [(a.get("url"), a.get("title"))
                              for a in c.get("annotations") or []
                              if a.get("type") == "url_citation"]
        # Some answers cite nothing though the search ran; the pages it
        # returned are still where the answer came from.
        if not cites:
            cites = [(s.get("url"), "") for it in out if it.get("type") == "web_search_call"
                     for s in (it.get("action") or {}).get("sources") or []]
        return {"text": "".join(texts), "sources": _sources(cites),
                "searches": sum(1 for it in out if it.get("type") == "web_search_call")}
    return ask


def _anthropic_lookup(model: str, spec: dict):
    _need_key(spec.get("api_key_env") or "ANTHROPIC_API_KEY")
    url = spec.get("base_url") or _ANTHROPIC_URL
    tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}

    def ask(prompt: str) -> dict:
        messages = [{"role": "user", "content": prompt}]
        blocks, searches = [], 0
        # A long search turn can come back paused; it continues by sending the
        # paused turn back as it was. Bounded, so a turn that never settles
        # costs a few requests rather than forever.
        for _ in range(4):
            d = net.post_json(url, {"model": model, "max_tokens": 16000,
                                    "messages": messages, "tools": [tool]},
                              headers=_anthropic_headers(spec))
            content = d.get("content") or []
            blocks += content
            searches += ((d.get("usage") or {}).get("server_tool_use") or {}) \
                .get("web_search_requests", 0) or 0
            if d.get("stop_reason") != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": content}]
        # The answer is what follows the last search; text before it is the
        # model saying what it is about to look up.
        last = max((i for i, b in enumerate(blocks)
                    if b.get("type") == "web_search_tool_result"), default=-1)
        text = "".join(b.get("text", "") for b in blocks[last + 1:] if b.get("type") == "text")
        # What the answer cites first, then the rest of what the search
        # returned: a caller keeping a few should keep what the answer rests on.
        pairs = [(c.get("url"), c.get("title")) for b in blocks
                 for c in b.get("citations") or []]
        for b in blocks:
            if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
                pairs += [(r.get("url"), r.get("title")) for r in b["content"]]
        return {"text": text, "sources": _sources(pairs), "searches": searches}
    return ask


_LOOKUPS = {"gemini": _gemini_lookup, "openai": _openai_lookup,
            "anthropic": _anthropic_lookup}


def can_search(model: str, conf: dict | None = None) -> bool:
    """Whether this model can be used for lookups, for Settings to say so
    before a round fails on it."""
    conf = config.load() if conf is None else conf
    spec = _spec(model, conf)
    return spec is not None and _can_search(spec)


def lookup(model: str, conf: dict | None = None):
    """Model name -> ask(prompt) -> {"text", "sources", "searches"}: a call
    that searches the web first and says where it looked.

    Raises at once, not per claim, when the model cannot search — a local
    model behind an OpenAI-compatible server has no search tool."""
    conf = config.load() if conf is None else conf
    spec = _spec(model, conf)
    if spec is None:
        resolve(model, conf)          # raises the "how to configure it" message
    if not _can_search(spec):
        raise RuntimeError(
            f"{model} cannot search the web: its provider ({spec.get('kind')}, "
            f"{spec.get('base_url') or 'built in'}) has no search tool. Pick a "
            "gemini*, gpt* or claude* model for lookups.")
    return _LOOKUPS[spec["kind"]](model, spec)
