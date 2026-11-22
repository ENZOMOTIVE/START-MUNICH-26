import { useEffect, useRef, useState } from "react";

/**
 * Categorical series colours, in fixed order of use. These are CSS variables from
 * styles.css, so they flip with dark mode. Assign by entity, never by rank.
 */
export const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
] as const;

/** Colour for the n-th category; anything past six folds into a neutral "other" tint. */
export function seriesColor(index: number): string {
  return SERIES[index] ?? "var(--tint-active)";
}

/** Default number formatter used by all charts. */
export function defaultFormat(value: number): string {
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 }).format(value);
}

/**
 * Measures an element's content width so SVG charts can draw in real pixels
 * (viewBox = measured size, rendered at width 100%): text never scales down on phones.
 */
export function useWidth<T extends HTMLElement>(fallback = 600) {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const w = Math.round(el.getBoundingClientRect().width);
      if (w > 0) setWidth(w);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}
