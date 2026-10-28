// The controls Settings uses: a row, a segmented choice, a stepper, a
// switch. Each writes one config key and nothing else.
import type { ReactNode } from "react";

export function Row({ title, desc, children, first }: {
  title: string; desc?: ReactNode; children?: ReactNode; first?: boolean;
}) {
  return (
    // On a phone the control goes under the words, full width.
    <div className={`flex flex-col gap-2.5 py-[14px] sm:flex-row sm:items-center sm:gap-6 ${first ? "" : "border-t border-border"}`}>
      <div className="flex grow flex-col gap-1">
        <span className="text-sm/[18px] font-medium sm:text-[13px]/4">{title}</span>
        {desc && <span className="text-xs/4 text-ink-subtle">{desc}</span>}
      </div>
      {children && <div className="flex items-center gap-3 sm:shrink-0">{children}</div>}
    </div>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex shrink-0 rounded-[7px] bg-sunken p-[3px] in-[fieldset:disabled]:opacity-50 max-sm:w-full">
      {options.map((o) => (
        <button key={String(o.value)} type="button" role="radio" aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={`rounded-[5px] px-3 py-[5px] text-[13px]/4 font-medium max-sm:grow max-sm:py-[7px] transition-colors duration-[80ms]
            ${o.value === value ? "bg-surface text-ink shadow-[0_1px_2px_#0000001a]" : "text-ink-subtle hover:text-ink"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Stepper({ value, min, max, unit, onChange, label }: {
  value: number; min: number; max: number; unit: string; onChange: (v: number) => void; label: string;
}) {
  const btn = "flex size-7 items-center justify-center text-ink-muted hover:text-ink disabled:opacity-30";
  return (
    <div className="flex h-[30px] shrink-0 items-center rounded-[6px] in-[fieldset:disabled]:opacity-50 shadow-[0_0_0_1px_var(--color-border-strong)]" aria-label={label}>
      <button type="button" className={btn} disabled={value <= min} onClick={() => onChange(value - 1)} aria-label="Less">−</button>
      <span className="min-w-16 px-1 text-center font-mono text-xs/4">{value} {unit}</span>
      <button type="button" className={btn} disabled={value >= max} onClick={() => onChange(value + 1)} aria-label="More">+</button>
    </div>
  );
}

export function Switch({ on, onChange, label, disabled }: {
  on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean;
}) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-[18px] w-8 shrink-0 rounded-full transition-colors duration-[120ms] disabled:opacity-40
        ${on ? "bg-ink" : "bg-border-strong"}`}>
      <span className={`absolute top-[2px] size-[14px] rounded-full bg-canvas shadow-[0_1px_2px_#0003] transition-[left] duration-[160ms] ease-[var(--ease-out)]
        ${on ? "left-[16px]" : "left-[2px]"}`} />
    </button>
  );
}

export const Mono = ({ children }: { children: ReactNode }) => (
  <span className="shrink-0 font-mono text-xs/4 text-ink-muted">{children}</span>
);

export const tilde = (p: string) => p.replace(/^\/Users\/[^/]+/, "~").replace(/^\/home\/[^/]+/, "~");
