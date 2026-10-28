// Theme = which set of semantic tokens is live, plus which of the two
// palettes the category colours come from. Both follow one attribute.
import { useEffect, useState } from "react";
import type { Meta } from "./api/types";

export type Theme = "light" | "dark";

const system = (): Theme =>
  matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";

/** ?theme=light|dark wins (screenshots, comparing themes); then
 * [ui] theme from config.toml, set in Settings › General; then the system. */
function pick(meta: Meta | undefined): Theme {
  const q = new URLSearchParams(location.search).get("theme");
  if (q === "light" || q === "dark") return q;
  if (meta?.theme === "light" || meta?.theme === "dark") return meta.theme;
  return system();
}

export function useTheme(meta: Meta | undefined) {
  const [theme, setTheme] = useState<Theme>(() => pick(meta));
  useEffect(() => setTheme(pick(meta)), [meta?.theme]); // eslint-disable-line
  useEffect(() => {
    if (meta?.theme !== "system" && meta) return;
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const on = () => setTheme(pick(meta));
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [meta]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    if (!meta) return;
    const root = document.documentElement.style;
    for (const [cat, hex] of Object.entries(meta.palette[theme])) {
      if (hex) root.setProperty(`--cat-${cat}`, hex);
    }
  }, [meta, theme]);
  return [theme, setTheme] as const;
}
