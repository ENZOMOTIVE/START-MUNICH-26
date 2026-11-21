import { useEffect, useState } from "react";
import { getMeta } from "../api/client";

export function DemoBanner() {
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const meta = await getMeta();
        if (!cancelled) {
          setDemo(meta.app_mode === "demo");
        }
      } catch {
        if (!cancelled) {
          setDemo(false);
        }
      }
    };

    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 2000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!demo) {
    return null;
  }

  return (
    <div className="demo-ribbon" role="status">
      <span>Demo mode · simulated outcomes only</span>
    </div>
  );
}
