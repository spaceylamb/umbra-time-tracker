import { demo } from "../lib/demo";

/** The public demo's one line: what this page is, and where Umbra is. It
 * scrolls away with the page and stands in for the topbar's "updated …". */
export function DemoStrip() {
  const d = demo();
  if (!d) return null;
  return (
    <div className="border-b border-border">
      <p className="mx-auto flex max-w-[1440px] flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2.5 text-sm/snug text-ink-muted sm:px-10">
        <span>A demo: three made-up weeks, with the model&rsquo;s answers written by hand. Nothing here is a real person&rsquo;s, and nothing can be changed.</span>
        <a href={d.link} target="_blank" rel="noreferrer"
          className="whitespace-nowrap font-medium text-ink underline decoration-1 underline-offset-[3px] hover:text-ink-muted">
          Get Umbra ↗
        </a>
      </p>
    </div>
  );
}
