import { createContext } from "react";

export type PeriodContextValue = { period: string; setPeriod: (value: string) => void };

export const PeriodContext = createContext<PeriodContextValue | null>(null);
