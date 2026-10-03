import { useMemo, useState, type ReactNode } from "react";
import { PeriodContext, type PeriodContextValue } from "./periodContextValue";

function currentPeriod(): string {
  const today = new Date();
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
}

export function PeriodProvider({ children }: { children: ReactNode }) {
  const [period, setPeriodState] = useState(() => {
    const saved = window.localStorage.getItem("reconbench-period");
    return saved && /^\d{4}-\d{2}$/.test(saved) ? saved : currentPeriod();
  });
  const value = useMemo<PeriodContextValue>(() => ({
    period,
    setPeriod: (next) => {
      if (!/^\d{4}-\d{2}$/.test(next)) return;
      window.localStorage.setItem("reconbench-period", next);
      setPeriodState(next);
    },
  }), [period]);
  return <PeriodContext.Provider value={value}>{children}</PeriodContext.Provider>;
}
