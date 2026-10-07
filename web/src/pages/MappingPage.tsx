import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, ArrowRight, Check, Clipboard, FileSearch, GitMerge, Pencil, Plus, RefreshCw, Search, Unlink } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router";

import {
  assignMappingVendor,
  bulkConfirmMappingLinks,
  createMappingVendor,
  decideMergeSuggestion,
  editMappingLink,
  listMappingDocuments,
  listMappingVendors,
  listMergeSuggestions,
  MappingApiError,
  renameMappingVendor,
} from "../api/mapping";
import type { MappingDocument, MappingVendor, MergeSuggestion } from "../api/mapping";
import { getFile } from "../api/ingestion";
import type { IngestionFile } from "../api/ingestion";
import { IngestionPreview } from "../components/IngestionPreview";
import { AiSharingBanner } from "../components/AiSharingBanner";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../auth/useAuth";
import { createReconciliationRun } from "../api/reconciliation";

type DocType = "po" | "grn" | "invoice";
type VendorSelection = string | "unassigned" | null;

const tabs: Array<{ id: DocType; label: string }> = [
  { id: "po", label: "PO" },
  { id: "grn", label: "GRN" },
  { id: "invoice", label: "Invoice" },
];

function displayDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(parsed);
}

function displayAmount(value: string | number | null, currency: string | null): string {
  if (value === null) return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return `${currency ?? "INR"} ${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(numeric)}`;
}

function initialDocumentType(value: string | null): DocType {
  return value === "po" || value === "grn" || value === "invoice" ? value : "invoice";
}

const PENDING_NAME = "(name pending)";

function vendorDisplayName(vendor: MappingVendor): string {
  const canonicalName = vendor.canonical_name.trim();
  if (!canonicalName) return PENDING_NAME;
  const normalize = (value: string) => value.replaceAll(/[^A-Z0-9]/gi, "").toUpperCase();
  const canonicalIdentity = normalize(canonicalName);
  const identifiers = [...vendor.gstins, ...(vendor.pan ? [vendor.pan] : [])].map(normalize);
  if (!identifiers.includes(canonicalIdentity)) return canonicalName;

  const humanAliases = [...new Set(
    (vendor.aliases ?? [])
      .map((alias) => alias.trim())
      .filter((alias) => alias && !identifiers.includes(normalize(alias))),
  )];
  return humanAliases.length === 1 ? humanAliases[0] ?? canonicalName : canonicalName;
}

function NameStatusBadge({ vendor }: { vendor: MappingVendor }) {
  if (vendor.unassigned) return null;
  if (vendor.name_status === "pending" || !vendor.canonical_name.trim()) {
    return <span className="mapping-name-badge is-pending" title="No readable supplier name yet. Rename the vendor to set one.">Name pending</span>;
  }
  if (vendor.name_status === "provisional") {
    return <span className="mapping-name-badge" title="Read by OCR and not yet confirmed. Rename the vendor to confirm it.">Check name</span>;
  }
  return null;
}

function VendorNameEditor({ vendor, busy, onRename }: { vendor: MappingVendor; busy: boolean; onRename: (name: string) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  if (!open) {
    return <button className="icon-button" type="button" aria-label="Rename vendor" disabled={busy} onClick={() => { setName(vendor.canonical_name); setOpen(true); }}><Pencil size={14} /></button>;
  }
  return (
    <form className="mapping-inline-form" onSubmit={(event) => { event.preventDefault(); if (name.trim()) { onRename(name.trim()); setOpen(false); } }}>
      <input value={name} maxLength={300} aria-label="Vendor name" placeholder="Supplier legal name" onChange={(event) => setName(event.currentTarget.value)} />
      <button className="table-action" type="submit" disabled={busy || !name.trim()}>Save</button>
      <button className="table-action" type="button" onClick={() => setOpen(false)}>Cancel</button>
    </form>
  );
}

function CreateVendorForm({ busy, onCreate }: { busy: boolean; onCreate: (vendor: { name: string; gstin: string }) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [gstin, setGstin] = useState("");
  if (!open) {
    return <button className="table-action" type="button" disabled={busy} onClick={() => setOpen(true)}><Plus size={14} /> Create vendor</button>;
  }
  return (
    <form className="mapping-inline-form" onSubmit={(event) => { event.preventDefault(); if (name.trim()) { onCreate({ name: name.trim(), gstin: gstin.trim() }); setOpen(false); setName(""); setGstin(""); } }}>
      <input value={name} maxLength={300} aria-label="New vendor name" placeholder="Supplier legal name" onChange={(event) => setName(event.currentTarget.value)} />
      <input value={gstin} maxLength={20} aria-label="New vendor GSTIN" placeholder="GSTIN (optional)" onChange={(event) => setGstin(event.currentTarget.value)} />
      <button className="table-action" type="submit" disabled={busy || !name.trim()}>Create</button>
      <button className="table-action" type="button" onClick={() => setOpen(false)}>Cancel</button>
    </form>
  );
}

function vendorSubtitle(vendor: MappingVendor): string {
  const ids = vendor.gstins.length ? vendor.gstins.join(" · ") : vendor.pan ?? "No GSTIN anchor";
  return `${ids} · ${vendor.counts.invoice} invoices`;
}

function LinkHealth({ counts }: { counts: MappingVendor["counts"] }) {
  const total = counts.linked + counts.suggested + counts.unlinked;
  const linked = total ? (counts.linked / total) * 100 : 0;
  const suggested = total ? (counts.suggested / total) * 100 : 0;
  return (
    <div className="mapping-health" aria-label={`${counts.linked} linked, ${counts.suggested} suggested, ${counts.unlinked} unlinked`}>
      <div className="mapping-health-track" aria-hidden="true">
        <span className="mapping-health-linked" style={{ width: `${linked}%` }} />
        <span className="mapping-health-suggested" style={{ width: `${suggested}%` }} />
      </div>
      <small>{counts.linked} linked · {counts.suggested} suggested · {counts.unlinked} unlinked</small>
    </div>
  );
}

function SuggestionBanner({
  suggestions,
  onDecide,
  disabled,
}: {
  suggestions: MergeSuggestion[];
  onDecide: (suggestion: MergeSuggestion, decision: "accept" | "reject", survivorId?: string) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!suggestions.length) return null;
  return (
    <section className="mapping-suggestion-banner" aria-label="Vendor merge suggestions">
      <div className="mapping-suggestion-banner-heading">
        <GitMerge size={17} aria-hidden="true" />
        <div><strong>{suggestions.length} vendor merge {suggestions.length === 1 ? "suggestion" : "suggestions"}</strong><span>Compare identity evidence. Different PANs cannot be merged.</span></div>
        <button className="button" type="button" onClick={() => setOpen((value) => !value)}>{open ? "Hide" : "Review"}</button>
      </div>
      {open ? (
        <div className="mapping-suggestion-list">
          {suggestions.map((suggestion) => (
            <article className="mapping-suggestion-card" key={suggestion.id}>
              <div className="mapping-suggestion-pair">
                <div><strong>{suggestion.vendor_a_name}</strong><span>{suggestion.vendor_a_pan ?? "PAN unavailable"}</span><small>{suggestion.vendor_a_gstins.join(" · ") || "No GSTIN"}</small></div>
                <ArrowRight size={16} aria-hidden="true" />
                <div><strong>{suggestion.vendor_b_name}</strong><span>{suggestion.vendor_b_pan ?? "PAN unavailable"}</span><small>{suggestion.vendor_b_gstins.join(" · ") || "No GSTIN"}</small></div>
              </div>
              <div className="mapping-suggestion-actions">
                <span>Similarity {Math.round(Number(suggestion.score) * 100)}%</span>
                {suggestion.pan_conflict ? <span className="text-danger"><AlertTriangle size={13} /> PAN conflict</span> : null}
                {suggestion.ai_opinion ? <span className="ai-opinion-note"><span className="ai-advisory-tag">AI opinion · advisory</span> {suggestion.ai_opinion === "same" ? "Likely the same supplier" : suggestion.ai_opinion === "different" ? "Likely different suppliers" : "Unsure"}{suggestion.ai_reason ? `: ${suggestion.ai_reason}` : ""}</span> : null}
                <button className="button" type="button" disabled={disabled || suggestion.pan_conflict} onClick={() => onDecide(suggestion, "accept", suggestion.vendor_a_id)}>Keep {suggestion.vendor_a_name}</button>
                <button className="button" type="button" disabled={disabled || suggestion.pan_conflict} onClick={() => onDecide(suggestion, "accept", suggestion.vendor_b_id)}>Keep {suggestion.vendor_b_name}</button>
                <button className="button button-subtle" type="button" disabled={disabled} onClick={() => onDecide(suggestion, "reject")}>Reject</button>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function MappingDocumentRow({
  row,
  vendors,
  purchaseOrders,
  selectedVendor,
  editable,
  busy,
  onAssign,
  onLinkAction,
  onPreview,
  onCopyLink,
  isDeepLinked,
}: {
  row: MappingDocument;
  vendors: MappingVendor[];
  purchaseOrders: MappingDocument[];
  selectedVendor: string | null;
  editable: boolean;
  busy: boolean;
  onAssign: (documentId: string, vendorId: string) => void;
  onLinkAction: (linkId: string, action: "confirm" | "change" | "unlink", version: number, targetId?: string) => void;
  onPreview: (fileId: string, documentId: string) => void;
  onCopyLink: (row: MappingDocument) => void;
  isDeepLinked: boolean;
}) {
  const [assignTo, setAssignTo] = useState("");
  const [targetByLink, setTargetByLink] = useState<Record<string, string>>({});
  const link = row.links.find((item) => item.state !== "rejected") ?? row.links[0];
  const resolution = row.resolution;
  const activeVendor = vendors.find((vendor) => vendor.id === selectedVendor);
  const vendorAssignmentMismatch = Boolean(selectedVendor && row.vendor_id !== selectedVendor);
  const normalizedDocumentGstin = row.vendor_gstin?.replaceAll(/[^A-Z0-9]/gi, "").toUpperCase();
  const vendorGstinMismatch = Boolean(
    activeVendor &&
      normalizedDocumentGstin &&
      activeVendor.gstins.length > 0 &&
      !activeVendor.gstins.some(
        (gstin) => gstin.replaceAll(/[^A-Z0-9]/gi, "").toUpperCase() === normalizedDocumentGstin,
      ),
  );
  const supplierMismatch = vendorAssignmentMismatch || vendorGstinMismatch;
  const displayStatus = link?.state ?? (resolution?.outcome === "suggested" ? "suggested" : "unlinked");
  const candidateRows = resolution?.candidates ?? [];
  const aiProposal = Boolean(link?.ai_suggested && link.state === "suggested");
  return (
    <tr id={`mapping-document-${row.id}`} className={isDeepLinked ? "mapping-document-target" : ""}>
      <td><strong>{row.doc_no ?? "No document number"}</strong><small>{isDeepLinked ? "Opened from shared link" : row.needs_verification ? "Needs verification" : row.doc_type.toUpperCase()}</small></td>
      <td className={supplierMismatch ? "mapping-supplier-mismatch" : undefined}>
        <strong>{row.vendor_name?.trim() || "Supplier name unavailable"}</strong>
        <small>{row.vendor_gstin ? `GSTIN ${row.vendor_gstin}` : "GSTIN not extracted"}</small>
        {supplierMismatch ? <small className="text-danger" role="alert">Supplier identity differs from this vendor group</small> : null}
      </td>
      <td>{displayDate(row.doc_date)}</td>
      <td className="mapping-amount">{displayAmount(row.grand_total, row.currency)}</td>
      <td>
        <span className={`mapping-state mapping-state-${displayStatus}`}>{displayStatus === "auto" ? "Auto" : displayStatus === "confirmed" ? "Confirmed" : displayStatus === "suggested" ? "Suggested" : "Unlinked"}</span>
        {link?.ai_suggested ? <span className="ai-advisory-tag" title="Proposed by AI from the top five same-vendor POs">{aiProposal ? "AI-suggested" : "Confirmed from AI suggestion"}</span> : null}
        {link ? <small className="mapping-evidence-meta">{link.method.replaceAll("_", " ")} · {Math.round(Number(link.confidence) * 100)}%</small> : null}
        {aiProposal ? <small className="ai-opinion-note">Not used in reconciliation until a person confirms it.</small> : null}
        {resolution?.detail_code === "PO_NOT_IN_POOL" ? <small className="mapping-not-in-pool">PO not in pool — upload the earlier PO file.</small> : null}
        {link?.evidence ? <details className="mapping-evidence"><summary>Evidence</summary><p>{link.evidence}</p></details> : null}
        {candidateRows.length ? <details className="mapping-evidence"><summary>Top candidates</summary><ol>{candidateRows.slice(0, 3).map((candidate, index) => <li key={`${String(candidate["po_document_id"] ?? index)}-${index}`}>{String(candidate["po_no"] ?? candidate["po_number"] ?? "Candidate PO")} · {String(candidate["score"] ?? "score unavailable")}</li>)}</ol></details> : null}
      </td>
      <td>
        {selectedVendor === null && editable ? (
          <div className="mapping-link-actions"><select aria-label={`Assign ${row.doc_no ?? row.doc_type} to vendor`} value={assignTo} disabled={busy} onChange={(event) => setAssignTo(event.currentTarget.value)}><option value="">Choose vendor…</option>{vendors.filter((vendor) => vendor.id).map((vendor) => <option key={vendor.id ?? ""} value={vendor.id ?? ""}>{vendorDisplayName(vendor)}</option>)}</select><button className="table-action" type="button" disabled={busy || !assignTo} onClick={() => onAssign(row.id, assignTo)}>Assign</button></div>
        ) : row.doc_type !== "po" && link && link.state !== "rejected" && editable ? (
          <div className="mapping-link-actions">
            {(link.state === "auto" || link.state === "suggested") ? <button className="table-action" type="button" disabled={busy} onClick={() => onLinkAction(link.id, "confirm", link.version_no)}><Check size={13} /> Confirm</button> : null}
            <label className="mapping-change-picker"><span className="sr-only">Replacement purchase order</span><select value={targetByLink[link.id] ?? ""} disabled={busy} onChange={(event) => setTargetByLink((previous) => ({ ...previous, [link.id]: event.currentTarget.value }))}><option value="">Choose PO…</option>{purchaseOrders.map((po) => <option value={po.id} key={po.id}>{po.doc_no ?? "PO without number"} · {po.vendor_name?.trim() || "Supplier name unavailable"} · {po.vendor_gstin ?? "GSTIN unavailable"}</option>)}</select></label>
            <button className="table-action" type="button" disabled={busy || !targetByLink[link.id] || targetByLink[link.id] === link.po_document_id} onClick={() => onLinkAction(link.id, "change", link.version_no, targetByLink[link.id])}>Change</button>
            <button className="table-action table-action-danger" type="button" disabled={busy} onClick={() => onLinkAction(link.id, "unlink", link.version_no)}><Unlink size={13} /> {aiProposal ? "Reject" : "Unlink"}</button>
          </div>
        ) : row.doc_type !== "po" ? <span className="mapping-muted">No current link</span> : <span className="mapping-muted">Governing PO</span>}
      </td>
      <td><button className="table-action" type="button" onClick={() => onPreview(row.source_file_id, row.id)} aria-label={`Preview ${row.doc_no ?? row.doc_type}`}><FileSearch size={14} /> Preview</button><button className="table-action" type="button" onClick={() => onCopyLink(row)} aria-label={`Copy link to ${row.doc_no ?? row.doc_type}`}><Clipboard size={14} /> Copy link</button></td>
    </tr>
  );
}

export function MappingPage() {
  const { user, hasCapability } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const documentId = searchParams.get("document_id");
  const [vendors, setVendors] = useState<MappingVendor[]>([]);
  const [documents, setDocuments] = useState<MappingDocument[]>([]);
  const [purchaseOrders, setPurchaseOrders] = useState<MappingDocument[]>([]);
  const [suggestions, setSuggestions] = useState<MergeSuggestion[]>([]);
  const [selected, setSelected] = useState<VendorSelection>(() => searchParams.get("vendor_id") ?? null);
  const [docType, setDocType] = useState<DocType>(() => initialDocumentType(searchParams.get("doc_type")));
  const [search, setSearch] = useState("");
  const [hasIssues, setHasIssues] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ file: IngestionFile; documentId: string } | null>(null);
  const editable = hasCapability("links.edit");
  const csrfToken = user?.csrf_token ?? "";
  const selectedVendor = selected === "unassigned" ? null : selected;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [vendorRows, suggestionRows] = await Promise.all([
        listMappingVendors(search, hasIssues),
        listMergeSuggestions(),
      ]);
      setVendors(vendorRows);
      setSuggestions(suggestionRows);
      let next = selected;
      if (next === "unassigned" && !vendorRows.some((vendor) => vendor.unassigned)) next = null;
      if (next && next !== "unassigned" && !vendorRows.some((vendor) => vendor.id === next)) next = null;
      if (next === null) {
        if (vendorRows.some((vendor) => vendor.unassigned)) next = "unassigned";
        else next = vendorRows.find((vendor) => vendor.id)?.id ?? null;
      }
      setSelected(next);
      if (next === null) {
        setDocuments([]);
        setPurchaseOrders([]);
      } else {
        const vendorId = next === "unassigned" ? null : next;
        const [documentRows, poRows] = await Promise.all([
          listMappingDocuments(vendorId, docType),
          vendorId ? listMappingDocuments(vendorId, "po") : Promise.resolve([]),
        ]);
        setDocuments(documentRows);
        setPurchaseOrders(poRows);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Mapping data could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [docType, hasIssues, search, selected]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  useEffect(() => {
    if (!documentId || loading) return;
    document.getElementById(`mapping-document-${documentId}`)?.scrollIntoView({ block: "center" });
  }, [documentId, documents, loading]);

  const activeVendor = useMemo(
    () => vendors.find((vendor) => vendor.id === selectedVendor) ?? null,
    [selectedVendor, vendors],
  );

  const runAction = async (action: () => Promise<unknown>, success: string) => {
    if (!csrfToken) {
      setError("Your session security token is missing. Sign in again and retry.");
      return;
    }
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      await action();
      setInfo(success);
      await refresh();
    } catch (cause) {
      if (cause instanceof MappingApiError && cause.status === 409) {
        setError(`${cause.message} The latest state has been reloaded; review it before trying again.`);
        await refresh();
      } else {
        setError(cause instanceof Error ? cause.message : "The mapping change could not be saved.");
      }
    } finally {
      setBusy(false);
    }
  };

  const startReconciliation = async () => {
    if (!user?.csrf_token) {
      setError("Your session security token is missing. Sign in again and retry.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const run = await createReconciliationRun(
        user.csrf_token,
        selectedVendor ? { kind: "vendor", vendor_id: selectedVendor } : { kind: "all" },
      );
      navigate(`/reconciliation?run_id=${encodeURIComponent(run.id)}${selectedVendor ? `&vendor_id=${encodeURIComponent(selectedVendor)}` : ""}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The reconciliation run could not be started.");
    } finally {
      setBusy(false);
    }
  };

  const openPreview = async (fileId: string, documentId: string) => {
    try {
      const { file } = await getFile(fileId);
      setPreview({ file, documentId });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The source preview could not be opened.");
    }
  };

  const chooseVendor = (selection: VendorSelection) => {
    setSelected(selection);
    const next = new URLSearchParams();
    if (selection && selection !== "unassigned") next.set("vendor_id", selection);
    next.set("doc_type", docType);
    setSearchParams(next);
  };

  const chooseDocType = (nextType: DocType) => {
    setDocType(nextType);
    const next = new URLSearchParams(searchParams);
    next.set("doc_type", nextType);
    next.delete("document_id");
    setSearchParams(next);
  };

  const copyVendorLink = async () => {
    if (!activeVendor?.id) return;
    const url = new URL(window.location.href);
    url.searchParams.set("vendor_id", activeVendor.id);
    url.searchParams.set("doc_type", docType);
    url.searchParams.delete("document_id");
    try {
      await navigator.clipboard.writeText(url.toString());
      setInfo("Vendor link copied.");
    } catch {
      setError("The vendor link could not be copied. Copy the page address from your browser.");
    }
  };

  const copyDocumentLink = async (row: MappingDocument) => {
    const url = new URL(window.location.href);
    if (selectedVendor) url.searchParams.set("vendor_id", selectedVendor);
    else url.searchParams.delete("vendor_id");
    url.searchParams.set("doc_type", row.doc_type);
    url.searchParams.set("document_id", row.id);
    try {
      await navigator.clipboard.writeText(url.toString());
      setInfo("Document link copied.");
    } catch {
      setError("The document link could not be copied. Copy the page address from your browser.");
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="DOCUMENT RELATIONSHIPS"
        title="Mapping"
        description="Group documents by vendor and review invoice or GRN links to purchase orders. Financial checks happen in Reconciliation."
        action={<div className="mapping-page-actions">{editable ? <button className="button" type="button" disabled={busy || loading} onClick={() => void runAction(() => bulkConfirmMappingLinks(selectedVendor, csrfToken), "Eligible links at or above 0.95 were confirmed.")}><Check size={14} /> Confirm all ≥ 0.95</button> : null}{hasCapability("recon.run") ? <button className="button button-primary" type="button" disabled={busy || loading} onClick={() => void startReconciliation()}>Run reconciliation</button> : null}</div>}
      />

      <AiSharingBanner />
      {error ? <div className="inline-error" role="alert">{error}</div> : null}
      {info ? <div className="mapping-info" role="status">{info}</div> : null}
      <SuggestionBanner
        suggestions={suggestions}
        disabled={busy || !editable}
        onDecide={(suggestion, decision, survivorId) => void runAction(
          () => decideMergeSuggestion(suggestion.id, decision, survivorId ?? null, suggestion.version, csrfToken),
          decision === "accept" ? "Vendor identities were merged. Existing source documents retain their original vendor reference." : "The merge suggestion was rejected.",
        )}
      />

      <div className="mapping-workspace-grid">
        <section className="panel mapping-vendor-panel" aria-label="Vendor list">
          <div className="mapping-list-toolbar">
            <label className="mapping-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.currentTarget.value)} placeholder="Search vendor" aria-label="Search vendor" /></label>
            <label className="mapping-issues-filter"><input type="checkbox" checked={hasIssues} onChange={(event) => setHasIssues(event.currentTarget.checked)} /> Has issues</label>
            <button className="icon-button" type="button" aria-label="Refresh vendor list" disabled={loading} onClick={() => void refresh()}><RefreshCw size={14} /></button>
          </div>
          <div className="mapping-vendor-list">
            {loading && !vendors.length ? <div className="mapping-empty" role="status">Loading vendors…</div> : null}
            {!loading && !vendors.length ? <div className="mapping-empty"><GitMerge size={19} /><strong>No parsed vendor records</strong><span>Upload PO, GRN, and invoice files in Ingestion to begin.</span></div> : null}
            {vendors.map((vendor) => {
              const key = vendor.unassigned ? "unassigned" : vendor.id;
              return (
                <button key={key ?? vendor.canonical_name} className={`mapping-vendor-row${selected === key ? " is-selected" : ""}`} type="button" onClick={() => chooseVendor(key)}>
                  <span className={`mapping-vendor-avatar${vendor.unassigned ? " is-unassigned" : ""}`}>{vendor.unassigned ? <AlertTriangle size={15} /> : vendorDisplayName(vendor).slice(0, 1).toUpperCase()}</span>
                  <span className="mapping-vendor-copy"><strong>{vendorDisplayName(vendor)} <NameStatusBadge vendor={vendor} /></strong><small>{vendorSubtitle(vendor)}</small><LinkHealth counts={vendor.counts} /></span>
                  {vendor.has_issues ? <span className="mapping-issue-dot" aria-label="Has mapping issues" /> : null}
                </button>
              );
            })}
          </div>
        </section>

        <section className="panel mapping-detail-panel" aria-label="Vendor documents">
          {activeVendor || selected === "unassigned" ? (
            <>
              <div className="mapping-detail-heading">
                <button className="icon-button mapping-back-button" type="button" aria-label="Back to vendor list" onClick={() => chooseVendor(null)}><ArrowLeft size={15} /></button>
                <div><div className="panel-kicker">{selected === "unassigned" ? "NEEDS A VENDOR" : "VENDOR DETAIL"}</div><h2>{activeVendor ? vendorDisplayName(activeVendor) : "Unassigned"}{activeVendor ? <> <NameStatusBadge vendor={activeVendor} /></> : null}</h2><p>{activeVendor ? vendorSubtitle(activeVendor) : `${documents.length} documents need a vendor assignment`}</p></div>
                {activeVendor?.id && editable && activeVendor.version !== undefined ? <VendorNameEditor key={activeVendor.id} vendor={activeVendor} busy={busy} onRename={(name) => void runAction(() => renameMappingVendor(activeVendor.id ?? "", name, activeVendor.version ?? 0, csrfToken), "Vendor name saved. Automatic resolution will not change it.")} /> : null}
                {activeVendor ? <button className="icon-button" type="button" aria-label="Copy vendor link" onClick={() => void copyVendorLink()}><Clipboard size={14} /></button> : null}
                <button className="icon-button" type="button" aria-label="Refresh vendor documents" disabled={loading} onClick={() => void refresh()}><RefreshCw size={14} /></button>
              </div>
              <div className="mapping-tabs" role="tablist" aria-label="Document type">
                {tabs.map((tab) => <button key={tab.id} type="button" role="tab" aria-selected={docType === tab.id} className={docType === tab.id ? "is-active" : ""} onClick={() => chooseDocType(tab.id)}>{tab.label}<span>{tab.id === "po" ? activeVendor?.counts.po ?? 0 : tab.id === "grn" ? activeVendor?.counts.grn ?? 0 : activeVendor?.counts.invoice ?? 0}</span></button>)}
              </div>
              {selected === "unassigned" ? <div className="mapping-context-note"><AlertTriangle size={14} /> Assign these records to an existing vendor. Vendors are created automatically only from a verified supplier GSTIN; for a supplier without one, create the vendor here and assign the records.{editable ? <CreateVendorForm busy={busy} onCreate={(vendor) => void runAction(() => createMappingVendor(vendor, csrfToken), "Vendor created. Assign its records below.")} /> : null}</div> : null}
              <div className="mapping-documents-table-wrap">
                <table className="mapping-documents-table">
                  <thead><tr><th>Document</th><th>Extracted supplier identity</th><th>Date</th><th>Amount</th><th>Link and evidence</th><th>Actions</th><th>Source</th></tr></thead>
                  <tbody>
                    {documents.map((row) => <MappingDocumentRow key={row.id} row={row} vendors={vendors} purchaseOrders={purchaseOrders} selectedVendor={selectedVendor} editable={editable} busy={busy} isDeepLinked={documentId === row.id} onAssign={(documentId, vendorId) => void runAction(() => assignMappingVendor(documentId, vendorId, csrfToken), "Vendor assignment saved and protected from automatic reassignment.")} onLinkAction={(linkId, action, version, targetId) => void runAction(() => editMappingLink(linkId, action, version, csrfToken, targetId), `Link ${action} was saved. Affected reconciliation results are stale until re-run.`)} onPreview={(fileId, documentId) => void openPreview(fileId, documentId)} onCopyLink={(documentRow) => void copyDocumentLink(documentRow)} />)}
                    {!loading && !documents.length ? <tr><td className="mapping-table-empty" colSpan={7}>No {docType.toUpperCase()} records for this vendor.</td></tr> : null}
                  </tbody>
                </table>
              </div>
              <div className="mapping-detail-footer"><span>Mapping determines identity and document links; it does not approve or reject invoice amounts.</span><span>{documents.length} records</span></div>
            </>
          ) : (
            <div className="mapping-empty mapping-empty-detail"><GitMerge size={23} /><strong>Select a vendor</strong><span>Vendor-linked purchase orders, receipts, invoices and their evidence will appear here.</span></div>
          )}
        </section>
      </div>

      {preview ? <IngestionPreview file={preview.file} batchId={preview.file.batch_id} focusDocumentId={preview.documentId} onClose={() => setPreview(null)} /> : null}
    </>
  );
}
