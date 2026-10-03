import { NavLink } from "react-router";
import type { LucideIcon } from "lucide-react";

import type { Capability } from "../auth/session";

interface CapabilityNavLinkProps {
  to: string;
  label: string;
  capability: Capability;
  capabilities: readonly Capability[];
  icon: LucideIcon;
}

export function CapabilityNavLink({
  to,
  label,
  capability,
  capabilities,
  icon: Icon,
}: CapabilityNavLinkProps) {
  const allowed = capabilities.includes(capability);
  if (!allowed) {
    return (
      <div className="capability-nav-item">
        <button
          className="nav-link settings-link capability-disabled"
          type="button"
          disabled
          aria-describedby={`capability-reason-${capability}`}
        >
          <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
          <span>{label}</span>
        </button>
        <span className="capability-reason" id={`capability-reason-${capability}`}>
          Manager permission required
        </span>
      </div>
    );
  }

  return (
    <NavLink
      className={({ isActive }) => `nav-link settings-link${isActive ? " is-active" : ""}`}
      to={to}
    >
      <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
      <span>{label}</span>
    </NavLink>
  );
}
