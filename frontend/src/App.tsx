import { Navigate, Route, Routes } from "react-router-dom";
import { DemoBanner } from "./components/DemoBanner";
import { SessionDetail } from "./features/sessions/SessionDetail";
import { SessionsPage } from "./features/sessions/SessionsPage";

export default function App() {
  return (
    <div className="shell">
      <DemoBanner />
      <header className="masthead">
        <p className="kicker">FiveU · START Munich</p>
        <h1>Sessions board</h1>
      </header>
      <Routes>
        <Route path="/" element={<SessionsPage />} />
        <Route path="/sessions/:id" element={<SessionDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
