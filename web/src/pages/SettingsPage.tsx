import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Save, Settings, Sparkles } from "lucide-react";
import { Link } from "react-router";

import { runLocalAiGuardrailTest, type AiGuardrailReport } from "../api/ai";
import { getWorkspaceConfiguration, saveWorkspaceConfiguration, type WorkspaceConfiguration } from "../api/settings";
import {
  addVendorAlias,
  deleteVendorAlias,
  listMappingVendors,
  listVendorAliases,
  type MappingVendor,
  type VendorAlias,
} from "../api/mapping";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../auth/useAuth";

const toleranceRules = [
  ["qty_vs_grn", "Invoice quantity vs accepted GRN"],
  ["cum_qty_vs_po", "Cumulative billed vs PO quantity"],
  ["unit_rate", "Invoice unit rate vs PO rate"],
  ["total_vs_expected", "Invoice taxable total vs expected"],
  ["arithmetic", "Invoice arithmetic"],
  ["cum_value_vs_po", "Cumulative PO value"],
] as const;

const referenceEditors = [
  ["synonyms", "Document field synonyms"],
  ["uom", "UOM aliases and conversions"],
  ["buyer_entities", "Buyer entities and GSTINs"],
  ["gst_slabs", "Effective GST slabs"],
  ["payment_terms", "Payment terms"],
  ["overrides", "Vendor and item overrides"],
] as const;

function object(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function SettingsPage() {
  const { user, hasCapability } = useAuth();
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [etag, setEtag] = useState('"1"');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [aiTesting, setAiTesting] = useState(false);
  const [aiReport, setAiReport] = useState<AiGuardrailReport | null>(null);
  const [referenceDrafts, setReferenceDrafts] = useState<Record<string, string>>({});
  const [referenceErrors, setReferenceErrors] = useState<Record<string, string>>({});
  const [aliasRows, setAliasRows] = useState<VendorAlias[]>([]);
  const [vendors, setVendors] = useState<MappingVendor[]>([]);
  const [aliasVendorId, setAliasVendorId] = useState("");
  const [aliasDraft, setAliasDraft] = useState("");
  const [aliasBusy, setAliasBusy] = useState(false);
  const [aliasError, setAliasError] = useState<string | null>(null);
  const [aliasNotice, setAliasNotice] = useState<string | null>(null);
  const [aliasesLoading, setAliasesLoading] = useState(true);
  const canManageAI = hasCapability("ai.manage");
  const canEditLinks = hasCapability("links.edit");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const current = await getWorkspaceConfiguration();
      setConfig(current.value);
      setEtag(current.etag);
      const engine = object(current.value.body["engine"]);
      setReferenceDrafts(Object.fromEntries(referenceEditors.map(([key]) => [
        key,
        JSON.stringify(engine[key] ?? {}, null, 2),
      ])));
      setReferenceErrors({});
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

  const body = config?.body ?? {};
  const engine = object(body["engine"]);
  const tolerance = object(engine["tolerances"]);
  const setTolerance = (rule: string, key: "pct" | "abs", value: string) => {
    setConfig((current) => {
      if (!current) return current;
      const nextBody = structuredClone(current.body);
      const nextEngine = object(nextBody["engine"]);
      const nextTolerances = object(nextEngine["tolerances"]);
      const nextRule = object(nextTolerances[rule]);
      nextRule[key] = value;
      nextTolerances[rule] = nextRule;
      nextEngine["tolerances"] = nextTolerances;
      nextBody["engine"] = nextEngine;
      return { ...current, body: nextBody };
    });
  };

  const setLimitMode = (value: "or" | "both") => {
    setConfig((current) => {
      if (!current) return current;
      const nextBody = structuredClone(current.body);
      const nextEngine = object(nextBody["engine"]);
      const nextTolerances = object(nextEngine["tolerances"]);
      nextTolerances["limit_mode"] = value;
      nextEngine["tolerances"] = nextTolerances;
      nextBody["engine"] = nextEngine;
      return { ...current, body: nextBody };
    });
  };

  const setReferenceJson = (key: string, value: string) => {
    setReferenceDrafts((current) => ({ ...current, [key]: value }));
    try {
      const parsed: unknown = JSON.parse(value);
      setReferenceErrors((current) => ({ ...current, [key]: "" }));
      setConfig((current) => {
        if (!current) return current;
        const nextBody = structuredClone(current.body);
        const nextEngine = object(nextBody["engine"]);
        nextEngine[key] = parsed;
        nextBody["engine"] = nextEngine;
        return { ...current, body: nextBody };
      });
    } catch {
      setReferenceErrors((current) => ({ ...current, [key]: "Enter valid JSON before saving." }));
    }
  };

  const save = async () => {
    if (!config || !user?.csrf_token) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await saveWorkspaceConfiguration(config.body, etag, user.csrf_token);
      setConfig(result.value);
      setEtag(result.etag);
      const engine = object(result.value.body["engine"]);
      setReferenceDrafts(Object.fromEntries(referenceEditors.map(([key]) => [key, JSON.stringify(engine[key] ?? {}, null, 2)])));
      setNotice("Configuration saved as a new version. Existing reconciliation cases are now stale; run reconciliation again to use these settings.");
    } catch (cause) {
      const value = cause as Error & { status?: number };
      if (value.status === 409) {
        setError("Another user changed settings. Reload current settings before making another change.");
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

  return <>
    <PageHeader title="Settings" description="Versioned reconciliation tolerances and workspace reference data." action={<button className="button button-primary" type="button" disabled={!config || busy || loading || Object.values(referenceErrors).some(Boolean)} onClick={() => void save()}><Save size={15} /> {busy ? "Saving…" : "Save settings"}</button>} />
    {error ? <div className="inline-error" role="alert">{error} {error.includes("Reload") ? <button className="button" type="button" onClick={() => void load()}>Reload</button> : null}</div> : null}
    {notice ? <div className="mapping-info" role="status">{notice}</div> : null}
    {loading ? <div className="panel">Loading workspace settings…</div> : null}
    {config ? <>
      <section className="panel settings-panel"><div className="panel-heading"><div><div className="panel-kicker">RECONCILIATION CONTROLS</div><h2>Tolerance rules</h2></div><label className="settings-mode"><span>Limit mode</span><select value={String(tolerance["limit_mode"] ?? "or")} onChange={(event) => setLimitMode(event.currentTarget.value as "or" | "both")}><option value="or">OR: percentage or absolute limit</option><option value="both">Both: percentage and absolute limit</option></select></label></div><p>These values are applied by the deterministic rules engine. Changing them creates a new immutable configuration version.</p><div className="recon-table-wrap"><table className="recon-table"><thead><tr><th>Check</th><th>Percentage tolerance</th><th>Absolute tolerance (₹ / units)</th></tr></thead><tbody>{toleranceRules.map(([key, title]) => { const value = object(tolerance[key]); return <tr key={key}><td>{title}</td><td><input aria-label={`${title} percentage tolerance`} inputMode="decimal" value={String(value["pct"] ?? "0")} onChange={(event) => setTolerance(key, "pct", event.currentTarget.value)} /></td><td><input aria-label={`${title} absolute tolerance`} inputMode="decimal" value={String(value["abs"] ?? "0")} onChange={(event) => setTolerance(key, "abs", event.currentTarget.value)} /></td></tr>; })}</tbody></table></div><small>Configuration version {config.seq} · rules hash {config.rules_hash}</small></section>
      <section className="panel settings-panel"><div className="panel-heading"><div><div className="panel-kicker">REFERENCE DATA</div><h2>Matching and finance reference values</h2></div></div><p>These structured values are saved as a new immutable config version. Enter valid JSON; the server validates the field shapes and reconciliation is marked stale after saving.</p><div className="settings-reference-grid">{referenceEditors.map(([key, title]) => <details className="settings-reference" key={key}><summary>{title}</summary><label><span className="sr-only">{title} JSON</span><textarea aria-label={`${title} JSON`} spellCheck={false} value={referenceDrafts[key] ?? ""} onChange={(event) => setReferenceJson(key, event.currentTarget.value)} rows={9} /></label>{referenceErrors[key] ? <small className="settings-json-error" role="alert">{referenceErrors[key]}</small> : null}</details>)}</div></section>
      <section className="panel settings-panel" aria-label="Vendor aliases"><div className="panel-heading"><div><div className="panel-kicker">VENDOR IDENTITY</div><h2>Vendor aliases</h2></div></div><p>Aliases help group printed supplier names with an existing vendor. They do not merge vendors or bypass PAN conflict checks.</p>{aliasError ? <div className="inline-error" role="alert">{aliasError}</div> : null}{aliasNotice ? <div className="mapping-info" role="status">{aliasNotice}</div> : null}<form className="settings-alias-form" onSubmit={(event) => void saveVendorAlias(event)}><label><span>Vendor</span><select aria-label="Vendor for alias" value={aliasVendorId} onChange={(event) => setAliasVendorId(event.currentTarget.value)} required><option value="">Choose vendor…</option>{vendors.map((vendor) => <option value={vendor.id ?? ""} key={vendor.id}>{vendor.canonical_name}</option>)}</select></label><label><span>Printed vendor alias</span><input aria-label="Printed vendor alias" value={aliasDraft} onChange={(event) => setAliasDraft(event.currentTarget.value)} maxLength={240} required /></label><button className="button" type="submit" disabled={!canEditLinks || aliasBusy || aliasesLoading || !aliasVendorId || !aliasDraft.trim()}>{aliasBusy ? "Saving…" : "Add alias"}</button></form>{aliasesLoading ? <p role="status">Loading vendor aliases…</p> : aliasRows.length ? <div className="recon-table-wrap"><table className="recon-table"><thead><tr><th>Vendor</th><th>Printed alias</th><th>Source</th><th>Action</th></tr></thead><tbody>{aliasRows.map((alias) => <tr key={alias.id}><td>{vendors.find((vendor) => vendor.id === alias.vendor_id)?.canonical_name ?? alias.vendor_id}</td><td>{alias.alias_raw}</td><td>{alias.source}</td><td><button className="button button-subtle" type="button" aria-label={`Remove alias ${alias.alias_raw}`} disabled={!canEditLinks || aliasBusy} onClick={() => void removeVendorAlias(alias.id)}>Remove</button></td></tr>)}</tbody></table></div> : <p>No vendor aliases are saved yet.</p>}</section>
      <section className="panel settings-panel"><div className="panel-heading"><div><div className="panel-kicker">AI ASSIST</div><h2>Rules-only mode</h2></div><span className="quiet-badge">AI off</span></div><p>AI remains disabled. This local stub validates grounded suggestions, PO-pool membership, strict output shape, and spec/PAN conflict handling. It never contacts an external provider or changes a financial verdict. Provider, model, key storage and document-sharing consent remain off until approved.</p><button className="button" type="button" disabled={!canManageAI || aiTesting} onClick={() => void testAiGuardrails()}><Sparkles size={15} /> {aiTesting ? "Checking guardrails…" : "Run offline guardrail test"}</button>{aiReport ? <ul className="ai-guardrail-results" aria-label="AI guardrail results">{Object.entries(aiReport.checks).map(([name, passed]) => <li key={name}><span>{name.replaceAll("_", " ")}</span><strong>{passed ? "Passed" : "Failed"}</strong></li>)}</ul> : null}<Link className="text-link" to="/admin/errors"><Settings size={14} /> Processing errors and retry queue</Link></section>
    </> : null}
  </>;
}
