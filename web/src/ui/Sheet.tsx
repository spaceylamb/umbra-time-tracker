// The phone's bottom sheet: what the desktop
// shows beside the page, the phone slides up over it.
//
// It enters with the iOS drawer curve and leaves the same way; a flick down
// on the grabber dismisses it (distance or velocity — a quick flick is
// enough, nobody should have to drag it the whole way), and so do the scrim
// and Escape. While it is open the page behind does not scroll.
import { useEffect, useRef, useState, type ReactNode } from "react";

const EASE = "cubic-bezier(0.32, 0.72, 0, 1)";

export function Sheet({ label, onClose, children }: {
  label: string; onClose: () => void; children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; t: number } | null>(null);
  const [dy, setDy] = useState(0);
  const [leaving, setLeaving] = useState(false);

  const close = () => setLeaving(true);

  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", k);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", k);
      document.body.style.overflow = prev;
    };
  }, []);

  const dragging = drag.current != null;
  return (
    <div className="fixed inset-0 z-30" role="dialog" aria-modal="true" aria-label={label}>
      <div onClick={close}
        className={`absolute inset-0 bg-scrim transition-opacity duration-300 starting:opacity-0
          ${leaving ? "opacity-0" : ""}`} />
      <div ref={panel}
        onTransitionEnd={(e) => { if (leaving && e.target === panel.current) onClose(); }}
        className="absolute inset-x-0 bottom-0 flex max-h-[calc(100dvh-96px)] flex-col overflow-clip rounded-t-2xl bg-surface
          starting:translate-y-full"
        style={{
          transform: leaving ? "translateY(100%)" : dy ? `translateY(${dy}px)` : undefined,
          transition: dragging ? "none" : `transform 360ms ${EASE}, translate 360ms ${EASE}`,
        }}>
        <div aria-hidden
          className="flex h-5 shrink-0 touch-none items-center justify-center pt-2"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            drag.current = { y: e.clientY, t: Date.now() };
            setDy(0);
          }}
          onPointerMove={(e) => {
            if (!drag.current) return;
            const d = e.clientY - drag.current.y;
            // Up past the top is allowed, with friction; down follows the finger.
            setDy(d < 0 ? -Math.sqrt(-d) * 2 : d);
          }}
          onPointerUp={(e) => {
            if (!drag.current) return;
            const d = e.clientY - drag.current.y;
            const v = d / Math.max(1, Date.now() - drag.current.t);
            drag.current = null;
            if (d > 120 || v > 0.11) close();
            else setDy(0);
          }}>
          <i className="h-[5px] w-9 rounded-full bg-border-strong" />
        </div>
        <div className="min-h-0 overflow-y-auto overscroll-contain">{children}</div>
      </div>
    </div>
  );
}
