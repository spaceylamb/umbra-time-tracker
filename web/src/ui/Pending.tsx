// Waiting on the server. A blank page reads exactly like "there is nothing
// here", so after a beat it says it is loading — not at once, which would
// flash on every fast load. A failed request says what failed.
export function Pending({ error }: { error?: unknown }) {
  if (error) {
    return <p className="p-10 text-sm text-ink-muted">Could not load: {String((error as Error)?.message ?? error)}</p>;
  }
  return (
    <div className="min-h-screen">
      <p className="pending p-10 text-sm text-ink-subtle">Loading…</p>
    </div>
  );
}
