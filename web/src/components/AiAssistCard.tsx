import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, KeyRound, Sparkles, X } from "lucide-react";

import {
  AI_MODEL_PATTERN,
  AiApiError,
  acknowledgeAiRequest,
  getAiConsentPreview,
  getAiRequestState,
  getAiSettings,
  getAiUsage,
  removeAiKey,
  saveAiSettings,
  startAiProviderTest,
} from "../api/ai";
import type { AiConsentPreview, AiKeyStorage, AiMode, AiSettingsUpdate, AiSettingsView, AiUsage } from "../api/ai";
import { useAuth } from "../auth/useAuth";

type Settings = { view: AiSettingsView; etag: string };

const modeOptions: ReadonlyArray<[AiMode, string, string]> = [
  ["rules_only", "Off: rules only", "Nothing is sent to an AI provider."],
  ["ai_assisted", "AI Assist", "Suggestions only where the rules are unsure."],
  ["ai_first", "AI-first", "Extraction suggestions for every PDF, plus the other hooks."],
];

const terminalStates = new Set(["succeeded", "failed", "uncertain", "skipped", "released"]);
const TEST_POLL_MS = 1000;
const TEST_POLL_LIMIT = 45;

function when(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function message(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback;
}

function limitNumber(limits: Record<string, unknown> | null, key: string): number | null {
  const value = limits?.[key];
  return typeof value === "number" ? value : null;
}

function testOutcome(state: string, code: string | null): string {
  if (state === "succeeded") return "The provider test call finished. The status above shows whether it passed.";
  if (state === "uncertain") return "The outcome is uncertain: the call may have reached Gemini and been billed. Acknowledge it below before testing again.";
  if (state === "skipped") return `The test was not sent${code ? ` (${code})` : ""}.`;
  if (state === "released") return "The test was released without a call.";
  return `The provider test failed${code ? ` (${code})` : ""}. The status above explains what to fix.`;
}

function externalCallsNotice(mode: string): string | null {
  if (mode === "disabled") return "External calls are disabled on this deployment (RB_AI_EXTERNAL_CALLS). Settings can be saved, but nothing is sent and the provider test is unavailable.";
  if (mode === "synthetic_only") return "This deployment sends only built-in synthetic test fixtures. Uploaded documents are never sent.";
  return null;
}

/** Home card: AI Assist state at a glance; managers open the full setup dialog from here. */
export function AiAssistCard() {
  const { hasCapability } = useAuth();
  const canManage = hasCapability("ai.manage");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setSettings(await getAiSettings());
      setLoadError(null);
    } catch (cause) {
      setLoadError(message(cause, "AI Assist status could not be loaded."));
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const view = settings?.view;
  const on = view !== undefined && view.mode !== "rules_only";
  return (
    <section className="panel assist-panel" aria-label="AI Assist">
      <div className="assist-mark"><Sparkles size={17} aria-hidden="true" /></div>
      <div className="panel-kicker">OPTIONAL ASSIST</div>
      <h2>AI Assist</h2>
      {loadError ? <p role="alert">{loadError}</p> : (
        <p>
          {!view ? "Loading AI Assist status…" : on
            ? `${view.mode_label}.${view.model ? ` Provider: Google Gemini (${view.model}).` : ""} AI output stays advisory until a person confirms it.`
            : "Rules-only mode is active. AI Assist can add advisory suggestions once a manager chooses a Gemini model, enters a key and consents to what is sent."}
        </p>
      )}
      {canManage ? (
        <button className="text-button" type="button" disabled={!settings} onClick={() => setOpen(true)}>Manage AI Assist <ArrowRight size={15} /></button>
      ) : <small className="ai-card-readonly">Only managers can change AI Assist.</small>}
      <div className="assist-state" role="status" aria-label="AI Assist status"><span className="state-dot" /> {view ? view.status.label : "…"}</div>
      {open && settings ? <AiAssistDialog settings={settings} onChange={setSettings} onReload={load} onClose={() => setOpen(false)} /> : null}
    </section>
  );
}

interface DialogProps {
  settings: Settings;
  onChange: (next: Settings) => void;
  onReload: () => Promise<void>;
  onClose: () => void;
}

function AiAssistDialog({ settings, onChange, onReload, onClose }: DialogProps) {
  const { user } = useAuth();
  const csrf = user?.csrf_token ?? "";
  const { view, etag } = settings;
  const [mode, setMode] = useState<AiMode>(view.mode);
  const [model, setModel] = useState(view.model ?? "");
  const [vision, setVision] = useState(view.vision.enabled);
  const [keyDraft, setKeyDraft] = useState("");
  const [storage, setStorage] = useState<AiKeyStorage>(view.key.storage ?? "session");
  const [preview, setPreview] = useState<AiConsentPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [consentChecked, setConsentChecked] = useState(false);
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [testMessage, setTestMessage] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => { mounted.current = false; window.removeEventListener("keydown", close); };
  }, [onClose]);

  const loadUsage = useCallback(async () => {
    try {
      const next = await getAiUsage();
      if (mounted.current) { setUsage(next); setUsageError(null); }
    } catch (cause) {
      if (mounted.current) setUsageError(message(cause, "Usage could not be loaded."));
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadUsage(), 0);
    return () => window.clearTimeout(timer);
  }, [loadUsage]);

  const modelValid = AI_MODEL_PATTERN.test(model);
  useEffect(() => {
    if (mode === "rules_only" || !modelValid) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      getAiConsentPreview(mode, model, vision)
        .then((next) => { if (active) { setPreview(next); setPreviewError(null); } })
        .catch((cause: unknown) => { if (active) { setPreview(null); setPreviewError(message(cause, "The consent text could not be loaded.")); } });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [mode, model, modelValid, vision]);

  const activePreview = mode !== "rules_only" && preview?.mode === mode && preview.model === model && preview.vision === vision ? preview : null;
  const consentOnRecord = activePreview !== null && view.consent.current && view.consent.required_fingerprint === activePreview.fingerprint;
  const needsConsent = mode !== "rules_only" && !consentOnRecord;
  const storageChangeNeedsKey = view.key.set && !keyDraft && view.key.storage !== null && storage !== view.key.storage;
  const dirty = mode !== view.mode || model !== (view.model ?? "") || vision !== view.vision.enabled || keyDraft !== "" || storageChangeNeedsKey;
  const saveBlocked = busy
    || (mode !== "rules_only" && !modelValid)
    || (model !== "" && !modelValid)
    || (needsConsent && (!activePreview || !consentChecked))
    || storageChangeNeedsKey
    || (keyDraft !== "" && !view.key_storage_available);

  const apply = (next: Settings, text: string) => {
    onChange(next);
    setMode(next.view.mode);
    setModel(next.view.model ?? "");
    setVision(next.view.vision.enabled);
    setStorage(next.view.key.storage ?? storage);
    setConsentChecked(false);
    setNotice(text);
  };

  const fail = async (cause: unknown, fallback: string) => {
    if (cause instanceof AiApiError && cause.status === 412) {
      setError("AI settings were changed by someone else. The latest settings are loaded; review them and try again.");
      await onReload();
      return;
    }
    setError(message(cause, fallback));
  };

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const body: AiSettingsUpdate = { mode };
    if (modelValid) { body.provider = "gemini"; body.model = model; }
    if (vision !== view.vision.enabled) body.vision = vision;
    if (keyDraft) { body.api_key = keyDraft; body.key_storage = storage; }
    else if (!view.key.set) body.key_storage = storage;
    if (needsConsent && activePreview && consentChecked) body.consent = { accept: true, fingerprint: activePreview.fingerprint };
    // The key leaves component state the moment it is submitted, whatever the outcome.
    setKeyDraft("");
    setBusy(true);
    setError(null);
    setNotice(null);
    setTestMessage(null);
    try {
      const next = await saveAiSettings(body, etag, csrf);
      apply(next, `AI settings saved as version ${next.view.version}. Status: ${next.view.status.label}.`);
    } catch (cause) {
      await fail(cause, "AI settings could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  const withdraw = async () => {
    setBusy(true);
    setError(null);
    try {
      apply(await saveAiSettings({ withdraw_consent: true }, etag, csrf), "Consent withdrawn. AI Assist is off and nothing more is sent.");
    } catch (cause) {
      await fail(cause, "Consent could not be withdrawn.");
    } finally {
      setBusy(false);
    }
  };

  const removeKey = async () => {
    setBusy(true);
    setError(null);
    setConfirmRemove(false);
    try {
      apply(await removeAiKey(etag, csrf), "Key removed. AI calls stop until a manager enters a new key.");
    } catch (cause) {
      await fail(cause, "The key could not be removed.");
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    setTestMessage("Provider test queued for the AI worker…");
    try {
      const requestId = await startAiProviderTest(csrf);
      let finished = false;
      for (let attempt = 0; attempt < TEST_POLL_LIMIT && mounted.current; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, TEST_POLL_MS));
        const state = await getAiRequestState(requestId);
        if (terminalStates.has(state.request_state)) {
          if (mounted.current) setTestMessage(testOutcome(state.request_state, state.error_code ?? state.skip_code));
          finished = true;
          break;
        }
      }
      if (!finished && mounted.current) setTestMessage("Still waiting for the AI worker. The status above updates when the test finishes.");
      await onReload();
      await loadUsage();
    } catch (cause) {
      if (mounted.current) { setTestMessage(null); setError(message(cause, "The provider test could not be started.")); }
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const acknowledge = async (requestId: string) => {
    setBusy(true);
    setError(null);
    try {
      await acknowledgeAiRequest(requestId, csrf);
      setNotice("Uncertain call acknowledged. One new attempt is allowed.");
      await loadUsage();
      await onReload();
    } catch (cause) {
      setError(message(cause, "The call could not be acknowledged."));
    } finally {
      setBusy(false);
    }
  };

  const callsNotice = externalCallsNotice(view.external_calls);
  const perDayCalls = limitNumber(view.limits, "per_day_calls");
  const perDayTokens = limitNumber(view.limits, "per_day_tokens");
  const testBlocked = busy || dirty || view.external_calls === "disabled" || !view.key.set || !view.model;

  return createPortal(
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="dialog-card ai-card-dialog" role="dialog" aria-modal="true" aria-labelledby="ai-card-title">
        <div className="dialog-heading">
          <div>
            <div className="panel-kicker">OPTIONAL ASSIST</div>
            <h2 id="ai-card-title">AI Assist settings</h2>
            <p>AI output is a suggestion or advisory opinion only. It never changes quantities, totals, links used in reconciliation or verdicts unless a person accepts it.</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close AI Assist settings" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="ai-card-status" role="status" aria-label="AI Assist status detail">
          <strong>{view.status.label}</strong>{view.status.at ? <span> · {when(view.status.at)}</span> : null}
        </div>
        {error ? <div className="inline-error" role="alert">{error}</div> : null}
        {notice ? <div className="mapping-info ai-card-message" role="status">{notice}</div> : null}
        {callsNotice ? <div className="ai-card-notice">{callsNotice}</div> : null}
        <form className="ai-card-body" autoComplete="off" onSubmit={(event) => void save(event)}>
          <fieldset className="ai-card-section">
            <legend>Mode</legend>
            <div className="ai-card-options">
              {modeOptions.map(([value, label, hint]) => (
                <label key={value} className={mode === value ? "selected" : undefined}>
                  <input type="radio" name="ai-mode" value={value} checked={mode === value} onChange={() => { setMode(value); setConsentChecked(false); }} />
                  <span><strong>{label}</strong><small>{hint}</small></span>
                </label>
              ))}
            </div>
          </fieldset>

          {mode !== "rules_only" ? (
            <fieldset className="ai-card-section">
              <legend>Page images</legend>
              <label className="ai-card-check">
                <input type="checkbox" checked={vision} disabled={!view.vision.available && !vision} onChange={(event) => { setVision(event.currentTarget.checked); setConsentChecked(false); }} />
                <span>Read page images with Gemini vision (recommended for scans and handwritten references). Bank, contact and signature areas are blacked out first; this replaces the PDF text suggestions.</span>
              </label>
              {!view.vision.available ? <small>Page images are turned off on this server.</small> : null}
            </fieldset>
          ) : null}

          <fieldset className="ai-card-section">
            <legend>Provider and model</legend>
            <div className="ai-card-grid">
            <label className="ai-card-field">
              <span id="ai-provider-label">Provider</span>
              <select value="gemini" disabled aria-labelledby="ai-provider-label" aria-describedby="ai-provider-note"><option value="gemini">Google Gemini</option></select>
              <small id="ai-provider-note">Gemini is the only supported provider.</small>
            </label>
            <label className="ai-card-field">
              <span id="ai-model-label">Model</span>
              <input aria-labelledby="ai-model-label" value={model} onChange={(event) => setModel(event.currentTarget.value.trim())} placeholder="gemini-3.8-flash" spellCheck={false} autoCapitalize="off" maxLength={64} aria-invalid={model !== "" && !modelValid} />
              <small>{model !== "" && !modelValid ? "Enter a Gemini model id such as gemini-3.8-flash. Do not paste the key here." : "The model id from your Google AI Studio project."}</small>
            </label>
            </div>
          </fieldset>

          <fieldset className="ai-card-section">
            <legend>API key</legend>
            {!view.key_storage_available ? <div className="ai-card-notice">Key storage is not configured on this server (the AI sealing key is missing), so a key cannot be entered.</div> : null}
            <label className="ai-card-field">
              <span id="ai-key-label">Gemini API key</span>
              <input
                aria-labelledby="ai-key-label"
                type="password"
                autoComplete="off"
                spellCheck={false}
                autoCapitalize="off"
                autoCorrect="off"
                data-1p-ignore="true"
                data-lpignore="true"
                value={keyDraft}
                disabled={!view.key_storage_available}
                onChange={(event) => setKeyDraft(event.currentTarget.value)}
                placeholder={view.key.set ? `Saved key ${view.key.hint ?? ""}; leave blank to keep it` : "Paste the key"}
                maxLength={256}
              />
              <small>Write-only: the key is sealed on receipt for the AI worker and is never shown or returned. Only a four-character hint is kept for display.</small>
            </label>
            <div className="ai-card-options" role="radiogroup" aria-label="Where the key is kept">
              <label className={storage === "session" ? "selected" : undefined}>
                <input type="radio" name="ai-key-storage" value="session" checked={storage === "session"} onChange={() => setStorage("session")} />
                <span><strong>This sign-in session only</strong><small>Deleted when you sign out. It stops working when this session ends or reaches its time limit, and must then be entered again.</small></span>
              </label>
              <label className={storage === "saved" ? "selected" : undefined}>
                <input type="radio" name="ai-key-storage" value="saved" checked={storage === "saved"} onChange={() => setStorage("saved")} />
                <span><strong>Saved for the workspace</strong><small>Kept sealed until a manager removes it. AI calls keep working after you sign out.</small></span>
              </label>
            </div>
            {view.key.set ? (
              <div className="ai-card-keyline">
                <KeyRound size={14} aria-hidden="true" />
                <span>Key {view.key.hint} · {view.key.storage === "session" ? `this session only${view.key.expires_at ? `, expires ${when(view.key.expires_at)}` : ""}` : "saved for the workspace"}</span>
                {confirmRemove ? (
                  <>
                    <button className="button button-danger" type="button" disabled={busy} onClick={() => void removeKey()}>Confirm remove key</button>
                    <button className="button button-secondary" type="button" onClick={() => setConfirmRemove(false)}>Keep key</button>
                  </>
                ) : <button className="button button-secondary" type="button" disabled={busy} onClick={() => setConfirmRemove(true)}>Remove key</button>}
              </div>
            ) : null}
            {storageChangeNeedsKey ? <small className="settings-json-error" role="alert">Changing where the key is kept requires entering the key again.</small> : null}
          </fieldset>

          {mode !== "rules_only" ? (
            <fieldset className="ai-card-section">
              <legend>Consent: what is sent</legend>
              {!modelValid ? <p>Enter a model to see exactly what would be sent.</p>
                : previewError ? <div className="inline-error" role="alert">{previewError}</div>
                  : !activePreview ? <p role="status">Loading the consent text…</p> : (
                    <>
                      <p>{activePreview.text}</p>
                      <ul className="ai-card-hooks" aria-label="Data sent per hook">
                        {activePreview.hooks.map((hook) => <li key={hook.hook}><strong>{hook.label}</strong><span>{hook.data_sent}</span></li>)}
                      </ul>
                      {consentOnRecord ? <small>Consent on record{view.consent.at ? ` since ${when(view.consent.at)}` : ""}.</small> : (
                        <label className="ai-card-check">
                          <input type="checkbox" checked={consentChecked} onChange={(event) => setConsentChecked(event.currentTarget.checked)} />
                          <span>I consent to sending the data listed above for these hooks to Google Gemini ({model}).</span>
                        </label>
                      )}
                    </>
                  )}
              {view.consent.given ? <button className="button button-secondary" type="button" disabled={busy} onClick={() => void withdraw()}>Withdraw consent and turn AI off</button> : null}
            </fieldset>
          ) : null}

          <section className="ai-card-section" aria-label="Limits, usage and cost">
            <h3>Limits, usage and cost</h3>
            {view.limits === null ? <div className="ai-card-notice">Limits are not configured on this server, so AI calls stay blocked until an operator sets them.</div> : null}
            {usageError ? <div className="inline-error" role="alert">{usageError}</div> : null}
            <dl className="ai-card-usage">
              <div><dt>Calls today</dt><dd>{usage ? usage.calls_today.toLocaleString() : "…"}{perDayCalls !== null ? ` / ${perDayCalls.toLocaleString()}` : ""}</dd></div>
              <div><dt>Tokens today</dt><dd>{usage ? usage.tokens_today.toLocaleString() : "…"}{perDayTokens !== null ? ` / ${perDayTokens.toLocaleString()}` : ""}</dd></div>
              <div><dt>Calls this month</dt><dd>{usage ? usage.calls_this_month.toLocaleString() : "…"}</dd></div>
              <div><dt>Cost today</dt><dd>{usage ? (usage.usd_available && usage.usd_today !== null ? `$${usage.usd_today} (estimate)` : "Not available") : "…"}</dd></div>
            </dl>
            {usage ? <small>{usage.usd_notice} {usage.calls_with_estimated_usage > 0 ? usage.token_notice : ""}</small> : null}
            {!view.budget.usd_cap_offered ? <small>No USD spending cap is offered: it needs a configured price and a guaranteed token bound for this model. The daily call and token limits still apply.</small> : null}
            {usage && usage.uncertain_requests.length ? (
              <div className="ai-card-uncertain">
                <strong>Calls with an uncertain outcome</strong>
                <p>These calls may have reached Gemini and been billed, but ReconBench could not confirm the result. Acknowledging one accepts that and allows exactly one new attempt.</p>
                <ul>
                  {usage.uncertain_requests.map((item) => (
                    <li key={item.id}>
                      <span>{item.hook ?? item.call_kind.replaceAll("_", " ")}{item.finished_at ? ` · ${when(item.finished_at)}` : ""}</span>
                      <button className="button button-secondary" type="button" disabled={busy} onClick={() => void acknowledge(item.id)}>Acknowledge</button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          {testMessage ? <div className="mapping-info ai-card-message" role="status" aria-label="Provider test result">{testMessage}</div> : null}
          <div className="dialog-actions">
            <span>{dirty ? "Unsaved changes. Save before testing." : `Settings version ${view.version}`}</span>
            <div>
              <button className="button button-secondary" type="button" disabled={testBlocked} onClick={() => void runTest()}>Test provider</button>
              <button className="button button-primary" type="submit" disabled={saveBlocked || !dirty}>{busy ? "Working…" : "Save AI settings"}</button>
            </div>
          </div>
        </form>
      </section>
    </div>,
    document.body,
  );
}
