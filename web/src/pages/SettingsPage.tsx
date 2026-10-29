import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { createPortal } from "react-dom";
import { Save, Settings, Sparkles } from "lucide-react";
import { Link } from "react-router";

import {
  runLocalAiGuardrailTest,
  type AiGuardrailReport,
} from "../api/ai";
import { getWorkspaceConfiguration, saveWorkspaceConfiguration, type ConfigProblemField, type WorkspaceConfiguration } from "../api/settings";
import {
  addVendorAlias,
  deleteVendorAlias,
  listMappingVendors,
  listVendorAliases,
  type MappingVendor,
  type VendorAlias,
} from "../api/mapping";
import { AiProvidersPanel } from "../components/AiProvidersPanel";
import { ScannedInvoicesSwitch } from "../components/ScannedInvoicesSwitch";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../auth/useAuth";
import { diffConfig, validateEngine, type ConfigChange, type EngineBody } from "../settings/configModel";
import {
  BuyerEntitiesSection,
  GstSlabsSection,
  PaymentTermsSection,
  PoliciesSection,
  SynonymsSection,
  TolerancesSection,
  UomSection,
} from "../settings/SettingsSections";

interface Conflict {
  theirs: WorkspaceConfiguration;
  etag: string;
  changes: ConfigChange[];
}

const aliasSourceLabels: Record<VendorAlias["source"], string> = {
  merge: "Learned from a vendor merge",
  learned: "Learned from a confirmed assignment",
  manual: "Manual entry",
  seed: "Seeded",
};

function engineOf(body: Record<string, unknown> | undefined): EngineBody | null {
  const engine = body?.["engine"];
  return typeof engine === "object" && engine !== null ? engine as EngineBody : null;
}

function serverFieldErrors(fields: ConfigProblemField[] | undefined): Record<string, string> {
  return Object.fromEntries((fields ?? []).map((field) => [field.location.join("."), "The server rejected this value."]));
}

export function SettingsPage() {
  const { user, hasCapability } = useAuth();
  const [saved, setSaved] = useState<WorkspaceConfiguration | null>(null);
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [etag, setEtag] = useState('"1"');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [aiTesting, setAiTesting] = useState(false);
  const [aiReport, setAiReport] = useState<AiGuardrailReport | null>(null);
  const [aliasRows, setAliasRows] = useState<VendorAlias[]>([]);
  const [vendors, setVendors] = useState<MappingVendor[]>([]);
  const [aliasVendorId, setAliasVendorId] = useState("");
  const [aliasDraft, setAliasDraft] = useState("");
  const [aliasSearch, setAliasSearch] = useState("");
  const [aliasBusy, setAliasBusy] = useState(false);
  const [aliasError, setAliasError] = useState<string | null>(null);
  const [aliasNotice, setAliasNotice] = useState<string | null>(null);
  const [aliasesLoading, setAliasesLoading] = useState(true);
  const canManageAI = hasCapability("ai.manage");
  const canEditLinks = hasCapability("links.edit");
  const canChangeSettings = hasCapability("settings.change");

  const adopt = (value: WorkspaceConfiguration, nextEtag: string) => {
    setSaved(value);
    setConfig(value);
    setEtag(nextEtag);
    setServerErrors({});
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const current = await getWorkspaceConfiguration();
      setSaved(current.value);
      setConfig(current.value);
      setEtag(current.etag);
      setServerErrors({});
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Workspace configuration could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const refreshAliases = useCallback(async () => {
    setAliasesLoading(true);
    setAliasError(null);
    try {
      const [rows, vendorRows] = await Promise.all([listVendorAliases(), listMappingVendors()]);
      setAliasRows(rows);
      setVendors(vendorRows.filter((vendor) => vendor.id && !vendor.unassigned));
      setAliasVendorId((current) => current || vendorRows.find((vendor) => vendor.id && !vendor.unassigned)?.id || "");
    } catch (cause) {
      setAliasError(cause instanceof Error ? cause.message : "Vendor aliases could not be loaded.");
    } finally {
      setAliasesLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refreshAliases(), 0);
    return () => window.clearTimeout(timer);
  }, [refreshAliases]);

  const engine = engineOf(config?.body);
  const defaults = engineOf(config?.defaults);
  const savedEngine = engineOf(saved?.body);
  const changes = useMemo(() => (saved && config ? diffConfig(saved.body, config.body) : []), [saved, config]);
  const clientErrors = useMemo(() => (engine ? validateEngine(engine) : {}), [engine]);
  const errors = { ...serverErrors, ...clientErrors };
  const errorCount = Object.keys(clientErrors).length;

  const update = (change: (engine: EngineBody) => void) => {
    setNotice(null);
    setServerErrors({});
    setConfig((current) => {
      if (!current) return current;
      const nextBody = structuredClone(current.body);
      const nextEngine = engineOf(nextBody);
      if (!nextEngine) return current;
      change(nextEngine);
      return { ...current, body: nextBody };
    });
  };

  const save = async () => {
    if (!config || !user?.csrf_token || errorCount) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await saveWorkspaceConfiguration(config.body, etag, user.csrf_token);
      adopt(result.value, result.etag);
      setNotice("saved");
    } catch (cause) {
      const value = cause as Error & { status?: number; current?: unknown; fields?: ConfigProblemField[] };
      const current = value.current as { etag?: string; config?: WorkspaceConfiguration } | undefined;
      if (value.status === 409 && current?.config && saved) {
        setConflict({ theirs: current.config, etag: current.etag ?? `"${current.config.seq}"`, changes: diffConfig(saved.body, current.config.body) });
      } else if (value.status === 409) {
        setError("Another user changed settings. Reload current settings before making another change.");
      } else if (value.status === 422) {
        setServerErrors(serverFieldErrors(value.fields));
        setError(`The server rejected ${value.fields?.length ?? "some"} field${value.fields?.length === 1 ? "" : "s"}: ${(value.fields ?? []).map((field) => field.location.join(" › ")).join("; ") || value.message}`);
      } else {
        setError(cause instanceof Error ? cause.message : "Settings could not be saved.");
      }
    } finally {
      setBusy(false);
    }
  };

  const saveVendorAlias = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!user?.csrf_token || !aliasVendorId || !aliasDraft.trim()) return;
    setAliasBusy(true);
    setAliasError(null);
    setAliasNotice(null);
    try {
      await addVendorAlias(aliasVendorId, aliasDraft.trim(), user.csrf_token);
      setAliasDraft("");
      setAliasNotice("Vendor alias saved. Related mappings may need a reconciliation rerun.");
      await refreshAliases();
    } catch (cause) {
      setAliasError(cause instanceof Error ? cause.message : "The vendor alias could not be saved.");
    } finally {
      setAliasBusy(false);
    }
  };

  const removeVendorAlias = async (aliasId: string) => {
    if (!user?.csrf_token) return;
    setAliasBusy(true);
    setAliasError(null);
    setAliasNotice(null);
    try {
      await deleteVendorAlias(aliasId, user.csrf_token);
      setAliasNotice("Vendor alias removed.");
      await refreshAliases();
    } catch (cause) {
      setAliasError(cause instanceof Error ? cause.message : "The vendor alias could not be removed.");
    } finally {
      setAliasBusy(false);
    }
  };

  const testAiGuardrails = async () => {
    if (!user?.csrf_token) return;
    setAiTesting(true);
    setError(null);
    setAiReport(null);
    try {
      const report = await runLocalAiGuardrailTest(user.csrf_token);
      setAiReport(report);
      setNotice(report.passed
        ? "Offline AI guardrail checks passed. No invoice data was sent to a provider."
        : "An offline AI guardrail check failed; AI remains disabled.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The local AI guardrail test could not run.");
    } finally {
      setAiTesting(false);
    }
  };

  const vendorName = (id: string) => vendors.find((vendor) => vendor.id === id)?.canonical_name ?? id;
  const search = aliasSearch.trim().toLowerCase();
  const visibleAliases = search ? aliasRows.filter((alias) => `${alias.alias_raw} ${vendorName(alias.vendor_id)} ${alias.source}`.toLowerCase().includes(search)) : aliasRows;
  const saveDisabled = !config || busy || loading || !changes.length || errorCount > 0 || !canChangeSettings;
  const sectionProps = engine ? { engine, defaults, errors, update } : null;

  return <>
    <PageHeader title="Settings" description="Versioned reconciliation rules and workspace reference data. Each save creates a new immutable configuration version." action={<button className="button button-primary" type="button" disabled={saveDisabled} onClick={() => void save()}><Save size={15} /> {busy ? "Saving…" : "Save settings"}</button>} />
    {error ? <div className="inline-error" role="alert">{error} {error.includes("Reload") ? <button className="button" type="button" onClick={() => void load()}>Reload</button> : null}</div> : null}
    {notice === "saved" ? <div className="mapping-info" role="status">Configuration saved as version {config?.seq}. Existing reconciliation cases are now stale and keep their old settings until you re-run. <Link className="text-link" to="/reconciliation">Open Reconciliation to re-run</Link></div> : notice ? <div className="mapping-info" role="status">{notice}</div> : null}
    {loading ? <div className="panel">Loading workspace settings…</div> : null}
    {!canChangeSettings && config ? <div className="mapping-info" role="note">You can view settings. Only a manager can change them.</div> : null}
    {savedEngine && !savedEngine.buyer_entities.some((entity) => entity.gstin.trim()) ? <div className="mapping-info" role="note" aria-label="Buyer entities not configured">Your company's own GSTINs are not set. Until they are, PDF readings cannot tell your GSTIN apart from the supplier's, so every supplier GSTIN needs review. Uploads still work. <a className="text-link" href="#buyer-entities">Add buyer entities</a></div> : null}
    <AiProvidersPanel />
    <ScannedInvoicesSwitch />
    {config && sectionProps ? <>
      <TolerancesSection {...sectionProps} vendors={vendors} />
      <PoliciesSection {...sectionProps} />
      <SynonymsSection {...sectionProps} />
      <UomSection {...sectionProps} />
      <BuyerEntitiesSection {...sectionProps} />
      <GstSlabsSection {...sectionProps} />
      <PaymentTermsSection {...sectionProps} />
      <section className="panel settings-panel" aria-label="Vendor aliases"><div className="panel-heading"><div><div className="panel-kicker">VENDOR IDENTITY</div><h2>Vendor aliases</h2></div><label className="settings-search"><span className="sr-only">Search vendor aliases</span><input aria-label="Search vendor aliases" placeholder="Search alias, vendor or source" value={aliasSearch} onChange={(event) => setAliasSearch(event.currentTarget.value)} /></label></div><p>Aliases help group printed supplier names with an existing vendor. They are saved immediately, separately from the versioned settings, and do not merge vendors or bypass PAN conflict checks.</p>{aliasError ? <div className="inline-error" role="alert">{aliasError}</div> : null}{aliasNotice ? <div className="mapping-info" role="status">{aliasNotice}</div> : null}<form className="settings-alias-form" onSubmit={(event) => void saveVendorAlias(event)}><label><span>Vendor</span><select aria-label="Vendor for alias" value={aliasVendorId} onChange={(event) => setAliasVendorId(event.currentTarget.value)} required><option value="">Choose vendor…</option>{vendors.map((vendor) => <option value={vendor.id ?? ""} key={vendor.id}>{vendor.canonical_name}</option>)}</select></label><label><span>Printed vendor alias</span><input aria-label="Printed vendor alias" value={aliasDraft} onChange={(event) => setAliasDraft(event.currentTarget.value)} maxLength={240} required /></label><button className="button" type="submit" disabled={!canEditLinks || aliasBusy || aliasesLoading || !aliasVendorId || !aliasDraft.trim()}>{aliasBusy ? "Saving…" : "Add alias"}</button></form>{aliasesLoading ? <p role="status">Loading vendor aliases…</p> : visibleAliases.length ? <div className="recon-table-wrap"><table className="recon-table"><thead><tr><th>Vendor</th><th>Printed alias</th><th>Source</th><th>Action</th></tr></thead><tbody>{visibleAliases.map((alias) => <tr key={alias.id}><td>{vendorName(alias.vendor_id)}</td><td>{alias.alias_raw}</td><td>{aliasSourceLabels[alias.source]}</td><td><button className="button button-subtle" type="button" aria-label={`Remove alias ${alias.alias_raw}`} disabled={!canEditLinks || aliasBusy} onClick={() => void removeVendorAlias(alias.id)}>Remove</button></td></tr>)}</tbody></table></div> : <p>{aliasRows.length ? "No aliases match this search." : "No vendor aliases are saved yet."}</p>}</section>
      <section className="panel settings-panel"><div className="panel-heading"><div><div className="panel-kicker">AI ASSIST</div><h2>Offline guardrail test</h2></div><span className="quiet-badge">Local only</span></div><p>This local stub validates grounded suggestions, PO-pool membership, strict output shape, and spec/PAN conflict handling. It never contacts an external provider or changes a financial verdict. Mode, consent and limits are managed from the AI Assist card on <Link className="text-link" to="/home">Home</Link>.</p><button className="button" type="button" disabled={!canManageAI || aiTesting} onClick={() => void testAiGuardrails()}><Sparkles size={15} /> {aiTesting ? "Checking guardrails…" : "Run offline guardrail test"}</button>{aiReport ? <ul className="ai-guardrail-results" aria-label="AI guardrail results">{Object.entries(aiReport.checks).map(([name, passed]) => <li key={name}><span>{name.replaceAll("_", " ")}</span><strong>{passed ? "Passed" : "Failed"}</strong></li>)}</ul> : null}<Link className="text-link" to="/admin/errors"><Settings size={14} /> Processing errors and retry queue</Link></section>
      <small className="settings-version">Configuration version {config.seq} · rules hash {config.rules_hash}</small>
      {changes.length || errorCount ? <div className="settings-savebar" role="region" aria-label="Unsaved settings">
        <span>{changes.length} unsaved change{changes.length === 1 ? "" : "s"}{errorCount ? ` · ${errorCount} field${errorCount === 1 ? "" : "s"} to fix before saving` : ""}</span>
        <div><button className="button" type="button" disabled={busy || !changes.length} onClick={() => { if (saved) adopt(saved, etag); }}>Discard changes</button><button className="button button-primary" type="button" disabled={saveDisabled} onClick={() => void save()}><Save size={14} /> {busy ? "Saving…" : "Save as new version"}</button></div>
      </div> : null}
    </> : null}
    {conflict ? createPortal(<div className="dialog-backdrop" role="presentation"><div className="dialog-card settings-conflict" role="dialog" aria-modal="true" aria-labelledby="settings-conflict-title">
      <div className="dialog-heading"><div><div className="panel-kicker">VERSION CONFLICT</div><h2 id="settings-conflict-title">Someone saved settings first</h2><p>Version {conflict.theirs.seq} is now active. Your {changes.length} unsaved change{changes.length === 1 ? " was" : "s were"} not saved. Review what they changed, then load their version and re-apply your edits.</p></div></div>
      <div className="settings-conflict-body"><table className="recon-table settings-table"><thead><tr><th>Setting</th><th>Before</th><th>Now</th></tr></thead><tbody>{conflict.changes.length ? conflict.changes.map((change) => <tr key={change.path}><td><code>{change.path}</code></td><td>{change.before}</td><td>{change.after}</td></tr>) : <tr><td colSpan={3}>No setting values differ; only the version moved.</td></tr>}</tbody></table></div>
      <div className="dialog-actions"><span>Your edits stay on screen until you load the new version.</span><div><button className="button" type="button" onClick={() => setConflict(null)}>Keep my edits for now</button><button className="button button-primary" type="button" onClick={() => { adopt(conflict.theirs, conflict.etag); setConflict(null); setError(null); }}>Load version {conflict.theirs.seq}</button></div></div>
    </div></div>, document.body) : null}
  </>;
}
