import {
  Activity,
  ArrowDownToLine,
  ChevronDown,
  CircleHelp,
  FileInput,
  GitMerge,
  House,
  Settings,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { listRecentReconciliationRuns } from "../api/reconciliation";
import { useAuth } from "../auth/useAuth";
import { PeriodProvider } from "../contexts/PeriodContext";
import { usePeriod } from "../contexts/usePeriod";
import { CapabilityNavLink } from "./CapabilityNavLink";
import { ErrorBoundary } from "./ErrorBoundary";
import type { Capability } from "../auth/session";

const mainNavigation = [
  { to: "/home", label: "Home", icon: House },
  { to: "/ingestion", label: "Ingestion", icon: FileInput },
  { to: "/mapping", label: "Mapping", icon: GitMerge },
  { to: "/reconciliation", label: "Reconciliation", icon: ArrowDownToLine },
];

export function AppShell() {
  return <PeriodProvider><AppShellContent /></PeriodProvider>;
}

function AppShellContent() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { period, setPeriod } = usePeriod();
  const [activeRuns, setActiveRuns] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      void listRecentReconciliationRuns().then((runs) => {
        if (!cancelled) setActiveRuns(runs.filter((run) => run.status === "queued" || run.status === "running").map((run) => run.id));
      }).catch(() => undefined);
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  if (!user) return null;

  const handleSignOut = async () => {
    try {
      await signOut();
    } catch {
      // Session state is cleared even when the server cannot be reached.
    } finally {
      navigate("/login", { replace: true });
    }
  };

  return (
    <div className="app-frame">
      <aside className="sidebar" aria-label="Workspace sidebar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">R</div>
          <div>
            <div className="brand-name">ReconBench</div>
            <div className="brand-caption">Finance operations</div>
          </div>
        </div>

        <div className="sidebar-section-label">WORKSPACE</div>
        <nav className="main-nav" aria-label="Main navigation">
          {mainNavigation.map(({ to, label, icon: Icon }) => (
            <NavLink className={({ isActive }) => `nav-link${isActive ? " is-active" : ""}`} key={to} to={to}>
              <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-spacer" />
        <div className="sidebar-footer">
          <div className="environment-pill"><span className="environment-dot" /> Local workspace</div>
          <CapabilityNavLink
            to="/settings"
            label="Settings"
            capability="settings.change"
            capabilities={user.capabilities as Capability[]}
            icon={Settings}
          />
          <button className="help-link" type="button">
            <CircleHelp size={17} strokeWidth={1.8} aria-hidden="true" />
            <span>Help and guidance</span>
          </button>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="period-picker" aria-label="Current reconciliation period">
            <span className="period-label">Period</span>
            <input className="period-button" type="month" aria-label="Select invoice date period" value={period} onChange={(event) => setPeriod(event.currentTarget.value)} />
          </div>
          <div className="topbar-right">
            <details className="job-status-menu">
              <summary className="job-status"><Activity size={15} aria-hidden="true" /> {activeRuns.length ? `${activeRuns.length} run${activeRuns.length === 1 ? "" : "s"} active` : "No active runs"}</summary>
              <div className="job-status-popover">
                {activeRuns.length ? activeRuns.map((runId) => <NavLink key={runId} to={`/reconciliation?run_id=${encodeURIComponent(runId)}`}>Reconciliation run {runId.slice(0, 8)}</NavLink>) : <span>No queued or running reconciliation jobs.</span>}
              </div>
            </details>
            <details className="user-menu">
              <summary aria-label={`${user.name}, ${user.role}`}>
                <span className="avatar" aria-hidden="true">{user.name.trim().slice(0, 2).toUpperCase()}</span>
                <span className="user-menu-label">
                  <strong>{user.name}</strong>
                  <small>{user.role.replaceAll("_", " ")}</small>
                </span>
                <ChevronDown size={14} aria-hidden="true" />
              </summary>
              <div className="user-menu-popover">
                <strong>{user.name}</strong>
                <span>{user.role.replaceAll("_", " ")}</span>
                <button className="button user-menu-signout" type="button" onClick={() => void handleSignOut()}>
                  Sign out
                </button>
              </div>
            </details>
          </div>
        </header>
        <main className="page-content"><ErrorBoundary resetKey={location.pathname}><Outlet /></ErrorBoundary></main>
      </div>
    </div>
  );
}
