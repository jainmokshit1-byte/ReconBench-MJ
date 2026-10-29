import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, Sparkles, X } from "lucide-react";

import {
  AI_MODEL_PATTERN,
  AiApiError,
  acknowledgeAiRequest,
  getAiConsentPreview,
  getAiUsage,
  saveAiSettings,
} from "../api/ai";
import { currentReaderPreview } from "../api/ai";
import type { AiAutoVerifyFieldType, AiConsentPreview, AiMode, AiSettingsUpdate, AiSettingsView, AiUsage } from "../api/ai";
import { useAuth } from "../auth/useAuth";
import { useAiProviders } from "./useAiProviders";

type Settings = { view: AiSettingsView; etag: string };

const modeOptions: ReadonlyArray<[AiMode, string, string]> = [
  ["rules_only", "Off: rules only", "Nothing is sent to an AI provider."],
  ["ai_assisted", "AI Assist", "Suggestions only where the rules are unsure."],
  ["ai_first", "AI-first", "Extraction suggestions for every PDF, plus the other hooks."],
];

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

function sameFieldTypes(left: readonly AiAutoVerifyFieldType[], right: readonly AiAutoVerifyFieldType[]): boolean {
  return left.length === right.length && left.every((fieldType, index) => fieldType === right[index]);
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
  const { settings, loadError, reload: load, setSettings } = useAiProviders();
  const [open, setOpen] = useState(false);

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
            ? `${view.mode_label}.${view.model ? ` Provider: Google Gemini (${view.model}).` : ""} ${view.auto_verify.field_types.length ? "Selected fields may be marked verified after strict reader, validator and crop checks; their values are never changed." : "AI output stays advisory until a person confirms it."}`
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
  // The Gemini model comes from the provider section above; this dialog never edits it.
  const model = view.model ?? "";
  const [vision, setVision] = useState(view.vision.enabled);
  const [autoVerifyFieldTypes, setAutoVerifyFieldTypes] = useState<AiAutoVerifyFieldType[]>(view.auto_verify.field_types);
  const [preview, setPreview] = useState<AiConsentPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [consentChecked, setConsentChecked] = useState(false);
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
  // With page images on, preview the reader that is saved (Gemini or the company server), so
  // the fingerprint matches what a save keeps; the reader itself is chosen in Settings.
  const savedReader = vision && view.reader_strategy === "single" ? currentReaderPreview(view) : undefined;
  const readerKey = savedReader ? JSON.stringify(savedReader) : "";
  useEffect(() => {
    if (mode === "rules_only" || !modelValid) return undefined;
    let active = true;
    const reader = readerKey ? JSON.parse(readerKey) as ReturnType<typeof currentReaderPreview> : undefined;
    const timer = window.setTimeout(() => {
      getAiConsentPreview(mode, model, vision, autoVerifyFieldTypes, reader)
        .then((next) => { if (active) { setPreview(next); setPreviewError(null); } })
        .catch((cause: unknown) => { if (active) { setPreview(null); setPreviewError(message(cause, "The consent text could not be loaded.")); } });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [mode, model, modelValid, vision, autoVerifyFieldTypes, readerKey]);

  const activePreview = mode !== "rules_only" && preview?.mode === mode && preview.model === model && preview.vision === vision && sameFieldTypes(preview.auto_verify_field_types, autoVerifyFieldTypes) ? preview : null;
  const consentOnRecord = activePreview !== null && view.consent.current && view.consent.required_fingerprint === activePreview.fingerprint;
  const needsConsent = mode !== "rules_only" && !consentOnRecord;
  const autoVerifyChanged = !sameFieldTypes(autoVerifyFieldTypes, view.auto_verify.field_types);
  const dirty = mode !== view.mode || vision !== view.vision.enabled || autoVerifyChanged;
  const saveBlocked = busy
    || (mode !== "rules_only" && !modelValid)
    || (needsConsent && (!activePreview || !consentChecked));

  const apply = (next: Settings, text: string) => {
    onChange(next);
    setMode(next.view.mode);
    setVision(next.view.vision.enabled);
    setAutoVerifyFieldTypes(next.view.auto_verify.field_types);
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
    if (autoVerifyChanged) body.auto_verify_field_types = autoVerifyFieldTypes;
    if (needsConsent && activePreview && consentChecked) body.consent = { accept: true, fingerprint: activePreview.fingerprint };
    setBusy(true);
    setError(null);
    setNotice(null);
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

  return createPortal(
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="dialog-card ai-card-dialog" role="dialog" aria-modal="true" aria-labelledby="ai-card-title">
        <div className="dialog-heading">
          <div>
            <div className="panel-kicker">OPTIONAL ASSIST</div>
            <h2 id="ai-card-title">AI Assist settings</h2>
            <p>{autoVerifyFieldTypes.length
              ? "Selected fields may be marked verified automatically only after two independent readers agree, validators pass and a local crop reread agrees. Their values are never changed. Other AI output stays advisory."
              : "AI output is a suggestion or advisory opinion only. It never changes quantities, totals, links used in reconciliation or verdicts unless a person accepts it."}</p>
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

          <fieldset className="ai-card-section">
            <legend>Auto-verify selected fields</legend>
            <p>Off by default. Only field types with a zero-false-safe benchmark gate are available. A selected field must pass every D42 reader, validator and crop check; conflicts stay for review.</p>
            <div className="ai-card-options">
              {view.auto_verify.available_field_types.map(({ type, label }) => (
                <label key={type} className={autoVerifyFieldTypes.includes(type) ? "selected" : undefined}>
                  <input
                    type="checkbox"
                    checked={autoVerifyFieldTypes.includes(type)}
                    onChange={(event) => {
                      const selected = new Set(autoVerifyFieldTypes);
                      if (event.currentTarget.checked) selected.add(type);
                      else selected.delete(type);
                      setAutoVerifyFieldTypes(view.auto_verify.available_field_types
                        .map((field) => field.type)
                        .filter((field) => selected.has(field)));
                      setConsentChecked(false);
                    }}
                  />
                  <span><strong>{label}</strong><small>Requires fresh consent when AI Assist is on.</small></span>
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

          <p className="ai-card-provider-note">{model ? `Text assist runs on Google Gemini (${model}).` : "No Gemini model is set."} Providers, keys and connection tests are managed in the AI providers section.</p>

          {mode !== "rules_only" ? (
            <fieldset className="ai-card-section">
              <legend>Consent: what is sent</legend>
              {!modelValid ? <p>Connect Gemini in the AI providers section to see exactly what would be sent.</p>
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

          <div className="dialog-actions">
            <span>{dirty ? "Unsaved changes." : `Settings version ${view.version}`}</span>
            <div>
              <button className="button button-primary" type="submit" disabled={saveBlocked || !dirty}>{busy ? "Working…" : "Save AI settings"}</button>
            </div>
          </div>
        </form>
      </section>
    </div>,
    document.body,
  );
}
