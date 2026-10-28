// Four variants, two heights, no scale on press.
//
// The press is a 1px collapse: the visible surface sits 1px inside the button
// and a same-colour 1px spread fills it back to the full bounds; pressing
// removes the spread, so the button shrinks by exactly 1px a side at any
// width (a scale warps wide buttons). Colours move in 80ms, the collapse
// comes back over 180ms.
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "tertiary" | "ghost";
type Size = "default" | "compact";

const SURFACE: Record<Variant, string> = {
  primary:
    "[--b:var(--color-ink)] group-hover:[--b:color-mix(in_oklab,var(--color-ink)_90%,var(--color-canvas))] group-active:[--b:color-mix(in_oklab,var(--color-ink)_80%,var(--color-canvas))]",
  secondary:
    "[--b:var(--color-sunken)] group-hover:[--b:var(--color-fill-hover)] group-data-[open=true]:[--b:var(--color-fill-open)]",
  tertiary: "[--b:transparent] shadow-[0_0_0_1px_var(--color-border-strong)] group-active:shadow-[inset_0_0_0_1px_var(--color-border-strong)]",
  ghost:
    "[--b:transparent] group-hover:[--b:var(--color-hover)] group-active:[--b:var(--color-active)]",
};

const TEXT: Record<Variant, string> = {
  primary: "text-canvas",
  secondary: "text-ink",
  tertiary: "text-ink",
  ghost: "text-ink-muted hover:text-ink",
};

const SIZE: Record<Size, { text: string; icon: string }> = {
  default: { text: "h-9 px-4 gap-1.5 text-[13px]", icon: "size-9" },
  compact: { text: "h-7 px-3 gap-1 text-xs", icon: "size-7" },
};

export function Button({
  variant = "secondary",
  size = "compact",
  icon = false,
  open,
  className = "",
  children,
  ...rest
}: {
  variant?: Variant;
  size?: Size;
  /** Square, icon only. Give it an aria-label. */
  icon?: boolean;
  /** Holds the pressed colour while what it opened is showing. */
  open?: boolean;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const filled = variant === "primary" || variant === "secondary";
  return (
    <button
      type="button"
      data-open={open ? "true" : undefined}
      aria-expanded={open}
      className={[
        "group relative inline-flex shrink-0 select-none items-center justify-center rounded-[6px] font-medium",
        "outline-none focus-visible:shadow-[0_0_0_2px_var(--color-canvas),0_0_0_3px_var(--color-ink)]",
        "disabled:pointer-events-none disabled:opacity-50",
        icon ? SIZE[size].icon : SIZE[size].text,
        TEXT[variant],
        "transition-colors duration-[80ms]",
        className,
      ].join(" ")}
      {...rest}
    >
      <span
        aria-hidden
        className={[
          "absolute inset-px rounded-[5px] bg-[var(--b)]",
          filled
            ? "shadow-[0_0_0_1px_var(--b)] group-active:shadow-[0_0_0_0_var(--b)]"
            : "",
          "transition-[background-color,box-shadow] duration-[180ms] ease-[var(--ease-out)] group-active:duration-[80ms]",
          SURFACE[variant],
        ].join(" ")}
      />
      <span className="relative inline-flex items-center gap-[inherit]">{children}</span>
    </button>
  );
}
