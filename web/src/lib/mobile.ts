// One breakpoint: below it the page is the phone layout, not
// the desktop one squeezed. Components that differ in structure branch on
// this; ones that only differ in size use Tailwind's sm: instead.
import { useSyncExternalStore } from "react";

const Q = "(max-width: 639px)";

const subscribe = (cb: () => void) => {
  const m = matchMedia(Q);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
};

export const useMobile = () => useSyncExternalStore(subscribe, () => matchMedia(Q).matches);
