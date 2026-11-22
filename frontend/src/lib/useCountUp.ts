import { useEffect, useRef, useState } from "react";

/** Eases from the previous value to `target` over `ms`. Jumps at once if motion is reduced. */
export function useCountUp(target: number | undefined, ms = 700): number | undefined {
  const [shown, setShown] = useState<number | undefined>(target);
  const from = useRef<number | undefined>(target);
  useEffect(() => {
    if (target === undefined) return;
    const start = from.current ?? target;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced || start === target) {
      from.current = target;
      setShown(target);
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / ms);
      const eased = 1 - (1 - p) ** 3;
      const v = start + (target - start) * eased;
      from.current = v;
      setShown(v);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return shown;
}
