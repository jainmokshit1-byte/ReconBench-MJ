import { useContext } from "react";
import { PeriodContext, type PeriodContextValue } from "./periodContextValue";

export function usePeriod(): PeriodContextValue {
  const value = useContext(PeriodContext);
  if (!value) throw new Error("usePeriod must be used inside PeriodProvider");
  return value;
}
