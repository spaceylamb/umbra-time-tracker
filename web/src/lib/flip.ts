// Shared-element morphs (FLIP) for the pinned header. The original and its
// pinned copy are one thing at two sizes, so the hand-off is instant — one
// becomes invisible in the same frame the other appears — and the one that
// appears starts at the other's place and size and moves/zooms into its own.
// Both directions: scrolling down the copy morphs out of the original;
// scrolling back up the original morphs out of the copy. Same motion either
// way, because it is the same object going back.
//
// This is movement on screen, so it eases in-and-out of its travel (the iOS
// drawer curve) over a little longer than the slow tier: a 40px bar
// flattening to 24px in under 300ms reads as a snap, not a morph.
// WAAPI on transform only — off the main thread, interruptible. Skipped when
// the source is off screen (a jump has nothing to morph from) and under
// reduced motion.
import { useLayoutEffect, useRef } from "react";

type Get = () => Element | null | undefined;
export type Pair = [original: Get, copy: Get, mode?: "uniform" | "stretch"];

export const MORPH_MS = 340;

// Navigating to a view (or another day) restores or resets the scroll, and
// the pinned steps flip with it. That is not the reader scrolling; nothing
// should morph or slide for it. The router stamps every navigation here.
let navAt = -Infinity;
export const markNavigation = () => {
  navAt = performance.now();
  // A morph still running from before the navigation must not outlive it:
  // finish them, so nothing is left mid-flight (translated off its place).
  document.querySelectorAll("[data-flip],[data-flip-to]").forEach((el) =>
    el.getAnimations().forEach((a) => a.finish()));
};
export const settling = () => performance.now() - navAt < 500;
export const MORPH_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";

function morph(from: Element, to: Element, mode: "uniform" | "stretch") {
  const f = from.getBoundingClientRect(), t = to.getBoundingClientRect();
  if (!t.width || !t.height || !f.width || f.bottom < -60 || f.top > innerHeight) return;
  const sy = f.height / t.height;
  const sx = mode === "uniform" ? sy : f.width / t.width;
  to.getAnimations().forEach((a) => a.cancel());
  to.animate(
    [{ transformOrigin: "0 0", transform: `translate(${f.left - t.left}px, ${f.top - t.top}px) scale(${sx}, ${sy})` },
     { transformOrigin: "0 0", transform: "none" }],
    { duration: MORPH_MS, easing: MORPH_EASE, fill: "none" },
  );
}

/** active: the copy is showing. On the way in each copy morphs out of its
 * original; on the way out each original morphs out of its copy. */
export function useFlip(active: boolean, pairs: () => Pair[]) {
  const was = useRef(active);
  useLayoutEffect(() => {
    if (active === was.current) return;
    was.current = active;
    if (settling() || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (const [orig, copy, mode = "uniform"] of pairs()) {
      const o = orig(), c = copy();
      if (!o || !c) continue;
      if (active) morph(o, c, mode); else morph(c, o, mode);
    }
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps
}
