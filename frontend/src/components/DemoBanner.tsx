import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getMeta } from "../api/client";

export function DemoBanner() {
  const [mode, setMode] = useState("");

  useEffect(() => {
    let cancelled = false;
    // The mode cannot change while the page is open: ask once.
    getMeta()
      .then((meta) => !cancelled && setMode(meta.app_mode))
      .catch(() => !cancelled && setMode(""));
    return () => {
      cancelled = true;
    };
  }, []);

  if (mode !== "demo" && mode !== "sandbox") {
    return null;
  }

  return (
    <div className="mode-ribbon" role="status">
      <div className="mode-ribbon-inner">
        <span className="dot" aria-hidden="true" />
        {mode === "sandbox" ? (
          <>
            <span>Sandbox · simulated data · no live actions</span>
            <Link to="/sandbox">Open sandbox console</Link>
          </>
        ) : (
          <span>Demo mode · simulated outcomes only · nothing is sent</span>
        )}
      </div>
    </div>
  );
}
