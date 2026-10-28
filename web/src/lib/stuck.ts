// Whether something has scrolled past a line: a 1px sentinel (render it with
// className="h-px -mb-px") counts as past once its top is above `top`.
//
// Read synchronously on every scroll event (useSyncExternalStore), not with
// an IntersectionObserver or a rAF: an observer only reports crossings of its
// own box, so a fling or a jump past the line from below the fold never
// fires; a rAF-then-render lags a frame or two, and in a fast scroll that is
// long enough to see content pass under the pinned header before the step
// that covers it arrives. `lead` fires that many pixels early on the way in
// (and on time on the way out) to absorb whatever lag is left.
import { useRef, useSyncExternalStore } from "react";

const subscribe = (cb: () => void) => {
  addEventListener("scroll", cb, { passive: true });
  addEventListener("resize", cb);
  return () => { removeEventListener("scroll", cb); removeEventListener("resize", cb); };
};

export function useStuck<T extends HTMLElement>(top = 0, lead = 0) {
  const sentinel = useRef<T>(null);
  const past = useRef(false);
  const read = () => {
    const el = sentinel.current;
    if (!el) return past.current;
    past.current = el.getBoundingClientRect().top < (past.current ? top : top + lead);
    return past.current;
  };
  const stuck = useSyncExternalStore(subscribe, read, () => false);
  return [sentinel, stuck] as const;
}
