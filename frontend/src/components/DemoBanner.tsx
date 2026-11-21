import { useEffect, useState } from "react";
import { getMeta } from "../api/client";

export function DemoBanner() {
  const [mode, setMode] = useState("");

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const meta = await getMeta();
        if (!cancelled) {
          setMode(meta.app_mode);
        }
      } catch {
        if (!cancelled) {
          setMode("");
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

  if (mode === "sandbox") {
    return (
      <div className="demo-ribbon sandbox-ribbon" role="status">
        <span>Sandbox · simulated data · no live actions</span>
        <a href="/sandbox">Sandbox console</a>
        <a href="/">Sessions board</a>
      </div>
    );
  }
  if (mode !== "demo") {
    return null;
  }

  return (
    <div className="demo-ribbon" role="status">
      <span>Demo mode · simulated outcomes only</span>
    </div>
  );
}
