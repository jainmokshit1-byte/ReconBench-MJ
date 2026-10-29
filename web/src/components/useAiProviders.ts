import { useCallback, useEffect, useRef, useState } from "react";

import { getAiSettings } from "../api/ai";
import type { AiSettingsView } from "../api/ai";

export interface AiSettingsState {
  view: AiSettingsView;
  etag: string;
}

const CHANGED_EVENT = "reconbench:ai-settings-changed";

/** Tells every AI settings view on the page (Home card, provider section) to load again. */
export function notifyAiSettingsChanged(): void {
  window.dispatchEvent(new Event(CHANGED_EVENT));
}

/** The one view model of `GET /ai/settings`, shared by the Home and Settings pages. */
export function useAiProviders() {
  const [settings, setSettingsState] = useState<AiSettingsState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const mounted = useRef(true);

  const reload = useCallback(async () => {
    try {
      const next = await getAiSettings();
      if (mounted.current) { setSettingsState(next); setLoadError(null); }
    } catch (cause) {
      if (mounted.current) setLoadError(cause instanceof Error ? cause.message : "AI provider settings could not be loaded.");
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const timer = window.setTimeout(() => void reload(), 0);
    const onChanged = () => void reload();
    window.addEventListener(CHANGED_EVENT, onChanged);
    return () => { mounted.current = false; window.clearTimeout(timer); window.removeEventListener(CHANGED_EVENT, onChanged); };
  }, [reload]);

  const setSettings = useCallback((next: AiSettingsState) => {
    setSettingsState(next);
    notifyAiSettingsChanged();
  }, []);

  return { settings, loadError, reload, setSettings, mounted };
}
