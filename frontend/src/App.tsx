import { Link, Navigate, Route, Routes } from "react-router-dom";
import { DemoBanner } from "./components/DemoBanner";
import { ReviewPage } from "./features/review/ReviewPage";
import { SandboxConsole } from "./features/sandbox/SandboxConsole";
import { SessionDetail } from "./features/sessions/SessionDetail";
import { SessionsPage } from "./features/sessions/SessionsPage";

export default function App() {
  return (
    <div className="shell">
      <a className="skip" href="#main">
        Skip to board
      </a>
      <DemoBanner />
      <header className="masthead">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <div>
            <p className="kicker">FiveU · START Munich</p>
            <h1>Sessions board</h1>
          </div>
        </div>
        <p className="mast-meta">
          Operator CRM · simulated desk · <Link to="/review">Review queue</Link>
        </p>
      </header>
      <main id="main">
        <Routes>
          <Route path="/" element={<SessionsPage />} />
          <Route path="/sessions/:id" element={<SessionDetail />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/sandbox" element={<SandboxConsole />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
