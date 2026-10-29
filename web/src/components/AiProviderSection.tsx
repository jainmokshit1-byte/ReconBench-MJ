import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { KeyRound, PlugZap, ScanText } from "lucide-react";

import {
  AiApiError,
  connectAiProvider,
  disconnectAiProvider,
  getAiConnectNotice,
  getAiConsentPreview,
  getAiRequestState,
  saveAiSettings,
  startAiProviderTests,
} from "../api/ai";
import type { AiCapabilities, AiConnectNotice, AiConsentPreview, AiKeyStorage, AiProviderCard, AiProviderName, AiSettingsView } from "../api/ai";
import { useAuth } from "../auth/useAuth";
import { useAiProviders } from "./useAiProviders";

const providerLabels: Record<AiProviderName, string> = {
  vllm: "Company server (Qwen)",
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

function yesNo(value: boolean | null): string {
  if (value === null) return "not checked";
  return value ? "yes" : "no";
}

/** The capability lines of a company-server card, in plain words. */
export function CapabilityList({ capabilities }: { capabilities: AiCapabilities }) {
  const contextWindow = capabilities.context_window;
  return (
    <ul className="ai-provider-capabilities" aria-label="What the last test showed">
      <li>Reads page images: {yesNo(capabilities.vision)}</li>
      <li>Structured answers: {yesNo(capabilities.json_schema)}</li>
      <li>Context window: {contextWindow === null ? "not checked" : `${contextWindow.toLocaleString("en-IN")} tokens`}</li>
    </ul>
  );
}

function testOutcome(provider: AiProviderName, states: string[], code: string | null): string {
  const name = providerLabels[provider];
  if (states.includes("uncertain")) return `${name}: the outcome is uncertain; acknowledge it on the Home AI Assist card before testing again.`;
  if (states.includes("skipped")) return `${name}: the test was not sent${code ? ` (${code})` : ""}.`;
  if (states.includes("released")) return `${name}: the test was released without a call.`;
  if (states.includes("failed")) return `${name}: the test failed${code ? ` (${code})` : ""}.`;
  return `${name}: the checks finished. The card shows what passed.`;
}

function problem(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback;
}

interface ConnectFormProps {
  card: AiProviderCard;
  view: AiSettingsView;
  busy: boolean;
  onConnect: (provider: AiProviderName, notice: AiConnectNotice, draft: { model: string; base_url: string; tier: "free" | "paid" | ""; key: string; storage: AiKeyStorage }) => Promise<boolean>;
}

/** Admin-only: choose Gemini (tier, model, key) or the company server (listed URL, model, optional key). */
function ConnectForm({ card, view, busy, onConnect }: ConnectFormProps) {
  const name = card.provider;
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState(card.model ?? "");
  const [baseUrl, setBaseUrl] = useState(card.base_url ?? view.vllm_endpoints_allowed[0] ?? "");
  const [tier, setTier] = useState<"free" | "paid" | "">("");
  const [key, setKey] = useState("");
  const [storage, setStorage] = useState<AiKeyStorage>("saved");
  const [notice, setNotice] = useState<AiConnectNotice | null>(null);
  const [noticeError, setNoticeError] = useState<string | null>(null);
  const [agreed, setAgreed] = useState(false);

  const ready = name === "vllm" ? model.trim() !== "" && baseUrl !== "" : tier !== "" && key !== "";
  useEffect(() => {
    if (!open || !ready) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      getAiConnectNotice(name, { ...(model ? { model } : {}), ...(name === "vllm" ? { base_url: baseUrl } : {}), ...(name === "gemini" && tier ? { tier } : {}) })
        .then((next) => { if (active) { setNotice(next); setNoticeError(null); } })
        .catch((cause: unknown) => { if (active) { setNotice(null); setNoticeError(problem(cause, "The notice could not be loaded.")); } });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [open, ready, name, model, baseUrl, tier]);

  const shown = ready ? notice : null;
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!shown || !agreed) return;
    // The key leaves component state the moment it is submitted, whatever the outcome.
    const sent = key;
    setKey("");
    const done = await onConnect(name, shown, { model, base_url: baseUrl, tier, key: sent, storage });
    if (done) { setOpen(false); setAgreed(false); setNotice(null); }
  };

  if (!open) {
    return <button className="button" type="button" disabled={busy} onClick={() => setOpen(true)}>{card.connection.state === "not_connected" && !card.model ? "Connect" : "Change connection"}</button>;
  }
  return (
    <form className="ai-provider-connect" autoComplete="off" aria-label={`Connect ${providerLabels[name]}`} onSubmit={(event) => void submit(event)}>
      {name === "vllm" ? (
        <>
          <label className="ai-card-field">
            <span>Server</span>
            <select value={baseUrl} onChange={(event) => { setBaseUrl(event.currentTarget.value); setAgreed(false); }}>
              {view.vllm_endpoints_allowed.map((url) => <option key={url} value={url}>{url}</option>)}
            </select>
            {view.vllm_endpoints_allowed.length === 0 ? <small>No company server address is allowed on this deployment.</small> : <small>Only addresses on the deployment allowlist can be chosen.</small>}
          </label>
          <label className="ai-card-field">
            <span>Model</span>
            <input value={model} onChange={(event) => { setModel(event.currentTarget.value.trim()); setAgreed(false); }} placeholder="qwen3.5:9b" spellCheck={false} autoCapitalize="off" maxLength={128} />
          </label>
        </>
      ) : (
        <>
          <label className="ai-card-field">
            <span>Key tier</span>
            <select value={tier} onChange={(event) => { setTier(event.currentTarget.value as "free" | "paid" | ""); setAgreed(false); }}>
              <option value="">Choose…</option>
              <option value="free">Free tier</option>
              <option value="paid">Paid (billed project)</option>
            </select>
          </label>
          <label className="ai-card-field">
            <span>Model (optional)</span>
            <input value={model} onChange={(event) => { setModel(event.currentTarget.value.trim()); setAgreed(false); }} placeholder="gemini-3.5-flash-lite" spellCheck={false} autoCapitalize="off" maxLength={64} />
          </label>
        </>
      )}
      <label className="ai-card-field">
        <span>{name === "gemini" ? "Gemini API key" : "Server key (only if the server needs one)"}</span>
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          data-1p-ignore="true"
          data-lpignore="true"
          value={key}
          disabled={!view.key_storage_available}
          onChange={(event) => setKey(event.currentTarget.value)}
          placeholder={card.key.set ? `Key set ${card.key.hint ?? ""}; enter a new one to replace it` : "Paste the key"}
          maxLength={256}
        />
        <small>Write-only: the key is sealed on receipt and is never shown or returned.</small>
      </label>
      {key !== "" ? (
        <div className="ai-card-options" role="radiogroup" aria-label="Where the key is kept">
          <label className={storage === "session" ? "selected" : undefined}>
            <input type="radio" name={`ai-key-storage-${name}`} value="session" checked={storage === "session"} onChange={() => setStorage("session")} />
            <span><strong>This sign-in session only</strong></span>
          </label>
          <label className={storage === "saved" ? "selected" : undefined}>
            <input type="radio" name={`ai-key-storage-${name}`} value="saved" checked={storage === "saved"} onChange={() => setStorage("saved")} />
            <span><strong>Saved for the workspace</strong></span>
          </label>
        </div>
      ) : null}
      {noticeError ? <div className="inline-error" role="alert">{noticeError}</div> : null}
      {shown ? (
        <div className="ai-reader-consent">
          <p>{shown.text}</p>
          <label><input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.currentTarget.checked)} /> I accept this notice for {providerLabels[name]}.</label>
        </div>
      ) : ready ? <p role="status">Loading the notice…</p> : null}
      <div className="dialog-actions">
        <button className="button button-subtle" type="button" onClick={() => { setOpen(false); setKey(""); setAgreed(false); }}>Cancel</button>
        <button className="button button-primary" type="submit" disabled={busy || !shown || !agreed}>Connect and test</button>
      </div>
    </form>
  );
}

/**
 * One provider component for Home and Settings: Gemini (key) and the company server (listed URL
 * and model), the server-side test result with what the server can do, and which provider reads
 * page images. Only a manager can change anything; the company server address is withheld by the
 * server from everyone else, who see "Company server". Nothing here accepts an AI value.
 */
export function AiProviderSection() {
  const { user } = useAuth();
  const csrf = user?.csrf_token ?? "";
  const { settings, loadError, reload, setSettings, mounted } = useAiProviders();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [testing, setTesting] = useState<AiProviderName | null>(null);
  const [choice, setChoice] = useState<AiProviderName | null>(null);
  const [preview, setPreview] = useState<AiConsentPreview | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState<AiProviderName | null>(null);

  const view = settings?.view ?? null;
  const canManage = view?.can_manage === true;
  const vllmCard = view?.providers.find((card) => card.provider === "vllm") ?? null;
  const reader = view && view.reader_strategy !== "off" ? view.primary_reader : null;
  const aiOn = Boolean(view && view.mode !== "rules_only" && view.model);

  useEffect(() => {
    if (!view || !canManage || !choice || choice === reader || !aiOn || view.mode === "rules_only" || !view.model) return undefined;
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
        .catch((cause: unknown) => { if (active) { setPreview(null); setError(problem(cause, "The consent text could not be loaded.")); } });
    }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [aiOn, canManage, choice, reader, view, vllmCard]);

  const onStale = async (cause: unknown, fallback: string) => {
    if (cause instanceof AiApiError && cause.status === 412) {
      setError("AI settings were changed by someone else. The latest settings are loaded; try again.");
      await reload();
    } else {
      setError(problem(cause, fallback));
    }
  };

  const waitFor = async (requestId: string): Promise<{ state: string; code: string | null } | null> => {
    for (let attempt = 0; attempt < POLL_LIMIT && mounted.current; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
      const state = await getAiRequestState(requestId);
      if (terminalStates.has(state.request_state)) return { state: state.request_state, code: state.error_code ?? state.skip_code };
    }
    return null;
  };

  const followTests = async (provider: AiProviderName, ids: string[]) => {
    const results = await Promise.all(ids.map(waitFor));
    if (!mounted.current) return;
    const done = results.flatMap((item) => (item ? [item] : []));
    setNotice(done.length < ids.length
      ? `${providerLabels[provider]}: still waiting for the AI worker; the card updates when the test finishes.`
      : testOutcome(provider, done.map((item) => item.state), done.find((item) => item.code)?.code ?? null));
    await reload();
  };

  const runTest = async (provider: AiProviderName) => {
    setTesting(provider);
    setError(null);
    setNotice(`${providerLabels[provider]}: test queued for the AI worker…`);
    try {
      const queued = await startAiProviderTests(csrf, provider);
      await followTests(provider, queued.image_request_id ? [queued.request_id, queued.image_request_id] : [queued.request_id]);
    } catch (cause) {
      if (mounted.current) { setNotice(null); setError(problem(cause, "The connection test could not be started.")); }
    } finally {
      if (mounted.current) setTesting(null);
    }
  };

  const connect: ConnectFormProps["onConnect"] = async (provider, accepted_notice, draft) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await connectAiProvider(provider, {
        ...(draft.model ? { model: draft.model } : {}),
        ...(provider === "vllm" ? { base_url: draft.base_url } : {}),
        ...(provider === "gemini" && draft.tier ? { tier: draft.tier } : {}),
        ...(draft.key ? { api_key: draft.key, key_storage: draft.storage } : {}),
        consent: { accept: true, fingerprint: accepted_notice.fingerprint },
      }, csrf);
      setSettings({ view: result.view, etag: `"${result.view.version}"` });
      setNotice(`${providerLabels[provider]}: saved. It shows Connected only after the server-side test passes.`);
      setTesting(provider);
      // The form closes now; the card updates when the worker's test finishes.
      void followTests(provider, result.image_request_id ? [result.request_id, result.image_request_id] : [result.request_id])
        .catch((cause: unknown) => { if (mounted.current) setError(problem(cause, "The connection test could not be followed.")); })
        .finally(() => { if (mounted.current) setTesting(null); });
      return true;
    } catch (cause) {
      await onStale(cause, "The provider could not be connected.");
      return false;
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const disconnect = async (provider: AiProviderName) => {
    setBusy(true);
    setError(null);
    setConfirmDisconnect(null);
    try {
      await disconnectAiProvider(provider, csrf);
      setNotice(`${providerLabels[provider]}: disconnected and its key deleted.`);
      await reload();
    } catch (cause) {
      await onStale(cause, "The provider could not be disconnected.");
    } finally {
      setBusy(false);
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
      await onStale(cause, "The page reader could not be changed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel settings-panel ai-providers-panel" aria-label="AI providers">
      <div className="panel-heading"><div><div className="panel-kicker">AI ASSIST</div><h2>AI providers</h2></div></div>
      <p>Choose Gemini or the company server. One provider reads page images; Gemini runs text assist. AI output is advisory: nothing changes a document, link or verdict until a person accepts it.</p>
      {view && !canManage ? <small className="ai-card-readonly">Only managers can change AI providers.</small> : null}
      {loadError ? <div className="inline-error" role="alert">{loadError}</div> : null}
      {error ? <div className="inline-error" role="alert">{error}</div> : null}
      {notice ? <div className="mapping-info" role="status">{notice}</div> : null}
      {view ? (
        <>
          <div className="ai-provider-cards">
            {(["vllm", "gemini"] as const).map((name) => {
              const card = view.providers.find((item) => item.provider === name);
              const gateOff = name === "vllm" ? view.vllm_calls === "disabled" : view.external_calls !== "enabled";
              const connected = card?.connection.state === "connected";
              return (
                <article key={name} className="ai-provider-card" aria-label={`${providerLabels[name]} provider`}>
                  <div className="ai-provider-card-heading">
                    <PlugZap size={16} aria-hidden="true" />
                    <strong>{providerLabels[name]}</strong>
                    <span className={`ai-provider-state is-${card?.connection.state ?? "not_connected"}`}>{card ? stateLabel(card) : "Not connected"}</span>
                  </div>
                  <dl>
                    <div><dt>Model</dt><dd>{card?.model ?? "—"}</dd></div>
                    {name === "vllm" ? <div><dt>Server</dt><dd>{canManage && card?.base_url ? card.base_url : "Company server"}</dd></div> : null}
                    <div><dt>Last checked</dt><dd>{when(card?.connection.checked_at ?? null)}</dd></div>
                    <div><dt>Used for</dt><dd>{[reader === name ? "page reading" : null, name === "gemini" && aiOn ? "text assist" : null].filter(Boolean).join(" · ") || "not in use"}</dd></div>
                  </dl>
                  {card?.key.set ? <div className="ai-card-keyline"><KeyRound size={14} aria-hidden="true" /><span>Key set {card.key.hint ?? ""}</span></div> : null}
                  {card?.capabilities ? <CapabilityList capabilities={card.capabilities} /> : null}
                  {card?.data_destination ? <small>{card.data_destination}</small> : null}
                  {gateOff ? <small>Calls to this provider are disabled on this deployment.</small> : null}
                  {canManage && card ? (
                    <div className="ai-provider-actions">
                      <button className="button" type="button" disabled={gateOff || testing !== null || busy || !(card.model || connected)} onClick={() => void runTest(name)}>{testing === name ? "Testing…" : "Test connection"}</button>
                      <ConnectForm card={card} view={view} busy={busy || testing !== null} onConnect={connect} />
                      {card.key.set || card.model ? (
                        confirmDisconnect === name ? (
                          <>
                            <button className="button button-danger" type="button" disabled={busy} onClick={() => void disconnect(name)}>Confirm disconnect</button>
                            <button className="button button-secondary" type="button" onClick={() => setConfirmDisconnect(null)}>Keep connected</button>
                          </>
                        ) : <button className="button button-secondary" type="button" disabled={busy} onClick={() => setConfirmDisconnect(name)}>Disconnect</button>
                      ) : null}
                    </div>
                  ) : null}
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
              const unproven = name === "vllm" && card?.capabilities?.can_read_pages !== true;
              return (
                <label key={name}>
                  <input type="radio" name="ai-page-reader" value={name} checked={(choice ?? reader) === name} disabled={(!connected || unproven) && reader !== name} onChange={() => { setChoice(name); setPreview(null); setAccepted(false); }} />
                  {providerLabels[name]}{reader === name ? " (current)" : ""}{!connected ? " · not connected" : unproven ? " · needs a passing image and structured-answer check" : ""}
                </label>
              );
            })}
            {canManage && choice && choice !== reader ? (
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
