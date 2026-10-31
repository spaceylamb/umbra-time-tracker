// The public demo (python -m umbra.devseed --site): three made-up weeks,
// read-only, with a clock that stands still at the moment the demo was built.
// Ages are computed when the page is read (design.md, principle 5) — which,
// for days that were never real, would only ever say "stopped".
// Read on every call, not once at load, so tests can set the flag.
type Demo = { now: number; link: string };

export const demo = (): Demo | undefined =>
  (globalThis as { __UMBRA_STATIC__?: { demo?: Demo } }).__UMBRA_STATIC__?.demo;

export const clock = () => demo()?.now ?? Date.now();
