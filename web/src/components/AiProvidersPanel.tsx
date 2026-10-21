import { useCallback, useEffect, useRef, useState } from "react";
import { PlugZap, ScanText } from "lucide-react";

import {
  AiApiError,
  getAiConsentPreview,
  getAiRequestState,
  getAiSettings,
  saveAiSettings,
  startAiProviderTest,
} from "../api/ai";
import type { AiConsentPreview, AiProviderCard, AiProviderName, AiSettingsView } from "../api/ai";
import { useAuth } from "../auth/useAuth";

const providerLabels: Record<AiProviderName, string> = {
  vllm: "Qwen · company vLLM server",
  gemini: "Google Gemini",
};
const terminalStates = new Set(["succeeded", "failed", "uncertain", "skipped", "released"]);
const POLL_MS = 1000;
const POLL_LIMIT = 60;

function stateLabel(card: AiProviderCard): string {
  if (card.connection.state === "connected") return "Connected";
  if (card.connection.state === "error") return `Error${card.connection.reason_code ? ` · ${card.connection.reason_code}` : ""}`;
  return "Not connected";
}

function when(value: string | null): string {
  if (!value) return "never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function testOutcome(provider: AiProviderName, state: string, code: string | null): string {
  const name = providerLabels[provider];
  if (state === "succeeded") return `${name}: the test call finished. The card shows whether it passed.`;
  if (state === "skipped") return `${name}: the test was not sent${code ? ` (${code})` : ""}.`;
  if (state === "uncertain") return `${name}: the outcome is uncertain; acknowledge it on the Home AI Assist card before testing again.`;
  if (state === "released") return `${name}: the test was released without a call.`;
  return `${name}: the test failed${code ? ` (${code})` : ""}.`;
}

/**
 * Settings → AI providers: both provider cards with a connection test, the page-reader choice
 * (with the consent text for the new destination) and which provider runs text assist.
 * Nothing here accepts an AI value; suggestions still need a person on the document page.
 */
export function AiProvidersPanel() {
  const { user, hasCapability } = useAuth();
  const canManage = hasCapability("ai.manage");
  const csrf = user?.csrf_token ?? "";
  const [settings, setSettings] = useState<{ view: AiSettingsView; etag: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [testing, setTesting] = useState<AiProviderName | null>(null);
  const [choice, setChoice] = useState<AiProviderName | null>(null);
  const [preview, setPreview] = useState<AiConsentPreview | null>(null);
  const [accepted, setAccepted] = useState(false);
  const mounted = useRef(true);

  const reload = useCallback(async () => {
    try {
      const next = await getAiSettings();
      if (mounted.current) { setSettings(next); setLoadError(null); }
    } catch (cause) {
      if (mounted.current) setLoadError(cause instanceof Error ? cause.message : "AI provider settings could not be loaded.");
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const timer = window.setTimeout(() => void reload(), 0);
    return () => { mounted.current = false; window.clearTimeout(timer); };
  }, [reload]);

  const view = settings?.view ?? null;
  const vllmCard = view?.providers.find((card) => card.provider === "vllm") ?? null;
  const reader = view && view.reader_strategy !== "off" ? view.primary_reader : null;
  const aiOn = Boolean(view && view.mode !== "rules_only" && view.model);

  useEffect(() => {
    if (!view || !choice || choice === reader || !aiOn || view.mode === "rules_only" || !view.model) return undefined;
    let active = true;
    const mode = view.mode;
    const model = view.model;
    const timer = window.setTimeout(() => {
      getAiConsentPreview(mode, model, true, view.auto_verify.field_types, {
        reader_strategy: "single",
        primary_reader: choice,
        ...(choice === "vllm" ? { vllm_model: vllmCard?.model ?? null, vllm_base_url: vllmCard?.base_url ?? null } : {}),
      })
        .then((next) => { if (active) { setPreview(next); setError(null); } })
        .catch((cause: unknown) => { if (active) { setPreview(null); setError(cause instanceof Error ? cause.message : "The consent text could not be loaded."); } });
    }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [aiOn, choice, reader, view, vllmCard]);

  const runTest = async (provider: AiProviderName) => {
    setTesting(provider);
    setError(null);
    setNotice(`${providerLabels[provider]}: test queued for the AI worker…`);
    try {
      const requestId = await startAiProviderTest(csrf, provider);
      let message = `${providerLabels[provider]}: still waiting for the AI worker; the card updates when the test finishes.`;
      for (let attempt = 0; attempt < POLL_LIMIT && mounted.current; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
        const state = await getAiRequestState(requestId);
        if (terminalStates.has(state.request_state)) {
          message = testOutcome(provider, state.request_state, state.error_code ?? state.skip_code);
          break;
        }
      }
      if (mounted.current) setNotice(message);
      await reload();
    } catch (cause) {
      if (mounted.current) { setNotice(null); setError(cause instanceof Error ? cause.message : "The connection test could not be started."); }
    } finally {
      if (mounted.current) setTesting(null);
    }
  };

  const switchReader = async () => {
    if (!settings || !choice || !preview || !accepted) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const next = await saveAiSettings(
        { reader_strategy: "single", primary_reader: choice, consent: { accept: true, fingerprint: preview.fingerprint } },
        settings.etag,
        csrf,
      );
      setSettings(next);
      setChoice(null);
      setPreview(null);
      setAccepted(false);
      setNotice(`Page reading now uses ${providerLabels[choice]}. New uploads are read by it; documents read earlier keep their stored suggestions.`);
    } catch (cause) {
      if (cause instanceof AiApiError && cause.status === 412) {
        setError("AI settings were changed by someone else. The latest settings are loaded; choose again.");
        await reload();
      } else {
        setError(cause instanceof Error ? cause.message : "The page reader could not be changed.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel settings-panel ai-providers-panel" aria-label="AI providers">
      <div className="panel-heading"><div><div className="panel-kicker">AI ASSIST</div><h2>AI providers</h2></div></div>
      <p>Both providers can be connected; one reads page images, and Gemini runs text assist. AI output is advisory: nothing changes a document, link or verdict until a person accepts it.</p>
      {loadError ? <div className="inline-error" role="alert">{loadError}</div> : null}
      {error ? <div className="inline-error" role="alert">{error}</div> : null}
      {notice ? <div className="mapping-info" role="status">{notice}</div> : null}
      {view ? (
        <>
          <div className="ai-provider-cards">
            {(["vllm", "gemini"] as const).map((name) => {
              const card = view.providers.find((item) => item.provider === name);
              const gateOff = name === "vllm" ? view.vllm_calls === "disabled" : view.external_calls !== "enabled";
              return (
                <article key={name} className="ai-provider-card" aria-label={`${providerLabels[name]} provider`}>
                  <div className="ai-provider-card-heading">
                    <PlugZap size={16} aria-hidden="true" />
                    <strong>{providerLabels[name]}</strong>
                    <span className={`ai-provider-state is-${card?.connection.state ?? "not_connected"}`}>{card ? stateLabel(card) : "Not connected"}</span>
                  </div>
                  <dl>
                    <div><dt>Model</dt><dd>{card?.model ?? "—"}</dd></div>
                    {name === "vllm" ? <div><dt>Server</dt><dd>{card?.base_url ?? "—"}</dd></div> : null}
                    <div><dt>Last checked</dt><dd>{when(card?.connection.checked_at ?? null)}</dd></div>
                    <div><dt>Used for</dt><dd>{[reader === name ? "page reading" : null, name === "gemini" && aiOn ? "text assist" : null].filter(Boolean).join(" · ") || "not in use"}</dd></div>
                  </dl>
                  {card?.data_destination ? <small>{card.data_destination}</small> : null}
                  {gateOff ? <small>Calls to this provider are disabled on this deployment.</small> : null}
                  <button className="button" type="button" disabled={!canManage || !card || gateOff || testing !== null || busy} onClick={() => void runTest(name)}>{testing === name ? "Testing…" : "Test connection"}</button>
                </article>
              );
            })}
          </div>

          <fieldset className="ai-reader-choice" disabled={!canManage || busy || !aiOn}>
            <legend><ScanText size={15} aria-hidden="true" /> Page reader</legend>
            {!aiOn ? <small>Turn AI Assist on from the Home card first.</small> : null}
            {(["vllm", "gemini"] as const).map((name) => {
              const card = view.providers.find((item) => item.provider === name);
              const connected = card?.connection.state === "connected";
              return (
                <label key={name}>
                  <input type="radio" name="ai-page-reader" value={name} checked={(choice ?? reader) === name} disabled={!connected && reader !== name} onChange={() => { setChoice(name); setPreview(null); setAccepted(false); }} />
                  {providerLabels[name]}{reader === name ? " (current)" : ""}{!connected ? " · not connected" : ""}
                </label>
              );
            })}
            {choice && choice !== reader ? (
              <div className="ai-reader-consent">
                {preview ? <p>{preview.text}</p> : <p role="status">Loading what would be sent…</p>}
                <label><input type="checkbox" checked={accepted} disabled={!preview} onChange={(event) => setAccepted(event.currentTarget.checked)} /> I agree to send page images to {providerLabels[choice]} as described above.</label>
                <div className="dialog-actions">
                  <button className="button button-subtle" type="button" onClick={() => { setChoice(null); setPreview(null); setAccepted(false); }}>Cancel</button>
                  <button className="button button-primary" type="button" disabled={!preview || !accepted || busy} onClick={() => void switchReader()}>Use {providerLabels[choice]} for page reading</button>
                </div>
              </div>
            ) : null}
          </fieldset>
          <p className="ai-provider-text-assist">Text assist (item match opinion, PO re-rank, vendor opinion): {aiOn ? `Google Gemini (${view.model ?? "—"})` : "off"}.</p>
        </>
      ) : !loadError ? <p role="status">Loading AI providers…</p> : null}
    </section>
  );
}
