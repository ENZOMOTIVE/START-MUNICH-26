import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { DemoBanner } from "./components/DemoBanner";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { ReviewPage } from "./features/review/ReviewPage";
import { SandboxConsole } from "./features/sandbox/SandboxConsole";
import { SessionDetail } from "./features/sessions/SessionDetail";
import { SessionsPage } from "./features/sessions/SessionsPage";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/sessions", label: "Sessions", end: false },
  { to: "/review", label: "Review queue", end: false },
  { to: "/sandbox", label: "Sandbox", end: false },
];

export default function App() {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <DemoBanner />
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand" aria-label="Air desk, overview">
            <span className="brand-mark" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path
                  d="M2.5 13.5 21 6l-4.5 13-4-5.5-4.5 3 .5-5.5-6-1.5Z"
                  fill="currentColor"
                />
              </svg>
            </span>
            Air desk
          </NavLink>
          <nav className="nav" aria-label="Main">
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end}>
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main id="main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:id" element={<SessionDetail />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/sandbox" element={<SandboxConsole />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
