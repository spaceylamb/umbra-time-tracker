// Fluid hover (fluidfunctionalism.com/docs/fluid-hover): one highlight per
// list that glides to whichever item is nearest the pointer, instead of every
// row lighting and unlighting on its own. No dead zones — the gaps between
// rows belong to the nearest row — and one element, one transform per move.
//
// Entering the list places the highlight under the pointer and fades it in
// (no travel from wherever it last was); leaving fades it out. Travel is a
// CSS transition, so a fast sweep retargets mid-flight rather than queueing.
// Mouse only: a touch has no hover, and a tap must not leave a highlight.
// Reduced motion keeps the fade and drops the travel.
import { useRef, type PointerEvent as RPointerEvent } from "react";

// The fast tier (theme.css), as in fluid functionalism's own hover.
const T = "var(--dur-fast) var(--ease-spring)";
const TRAVEL = `transform ${T}, width ${T}, height ${T}`;

export function useFluidHover<T extends HTMLElement>() {
  const box = useRef<T>(null);
  const hl = useRef<HTMLDivElement>(null);
  const on = useRef<HTMLElement | null>(null);

  const place = (el: HTMLElement, travel: boolean) => {
    const h = hl.current, b = box.current;
    if (!h || !b) return;
    const r = el.getBoundingClientRect(), br = b.getBoundingClientRect();
    const still = !travel || matchMedia("(prefers-reduced-motion: reduce)").matches;
    h.style.transition = still ? `opacity ${T}` : `${TRAVEL}, opacity ${T}`;
    h.style.transform = `translate(${r.left - br.left + b.scrollLeft}px, ${r.top - br.top + b.scrollTop}px)`;
    h.style.width = `${r.width}px`;
    h.style.height = `${r.height}px`;
    h.style.opacity = "1";
    on.current = el;
  };

  const onPointerMove = (e: RPointerEvent) => {
    if (e.pointerType !== "mouse" || !box.current) return;
    let best: HTMLElement | null = null, d = Infinity;
    for (const el of box.current.querySelectorAll<HTMLElement>("[data-fluid]")) {
      const r = el.getBoundingClientRect();
      const dy = e.clientY < r.top ? r.top - e.clientY : e.clientY > r.bottom ? e.clientY - r.bottom : 0;
      const dx = e.clientX < r.left ? r.left - e.clientX : e.clientX > r.right ? e.clientX - r.right : 0;
      const dist = dy * 4 + dx;                 // lists: vertical distance decides
      if (dist < d) { d = dist; best = el; }
    }
    if (best && best !== on.current) place(best, on.current != null);
  };

  const onPointerLeave = () => {
    const h = hl.current;
    if (h) { h.style.transition = "opacity var(--dur-fast-out) ease-out"; h.style.opacity = "0"; }
    on.current = null;
  };

  /** Call first inside the list; the list needs `relative`, items `relative`.
   * A plain element, not a component, so a re-render never remounts it. */
  const highlight = (className = "rounded-[6px] bg-hover") => (
    <div ref={hl} aria-hidden className={`pointer-events-none absolute top-0 left-0 opacity-0 ${className}`} />
  );

  return { ref: box, handlers: { onPointerMove, onPointerLeave }, highlight };
}
