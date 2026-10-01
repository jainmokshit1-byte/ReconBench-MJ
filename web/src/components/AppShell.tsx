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
import { NavLink, Outlet } from "react-router";

const mainNavigation = [
  { to: "/home", label: "Home", icon: House },
  { to: "/ingestion", label: "Ingestion", icon: FileInput },
  { to: "/mapping", label: "Mapping", icon: GitMerge },
  { to: "/reconciliation", label: "Reconciliation", icon: ArrowDownToLine },
];

export function AppShell() {
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
          <NavLink className={({ isActive }) => `nav-link settings-link${isActive ? " is-active" : ""}`} to="/settings">
            <Settings size={18} strokeWidth={1.8} aria-hidden="true" />
            <span>Settings</span>
          </NavLink>
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
            <button className="period-button" type="button" aria-label="Select period">
              Select a period <ChevronDown size={15} aria-hidden="true" />
            </button>
          </div>
          <div className="topbar-right">
            <div className="job-status"><Activity size={15} aria-hidden="true" /> All systems idle</div>
            <div className="avatar" aria-label="Signed in user">RB</div>
          </div>
        </header>
        <main className="page-content"><Outlet /></main>
      </div>
    </div>
  );
}
