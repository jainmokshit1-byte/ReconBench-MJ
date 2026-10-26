import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, Clipboard, Download, RefreshCw, X } from "lucide-react";
import { useSearchParams } from "react-router";
import { useVirtualizer } from "@tanstack/react-virtual";

import {
  addCaseAction,
  bulkCaseAction,
  createReconciliationExport,
  createReconciliationRun,
  downloadReconciliationExport,
  getReconciliationExport,
  getReconciliationCase,
  getReasonCodes,
  getReconciliationKpis,
  getReconciliationRun,
  listRecentReconciliationRuns,
  listReconciliationCases,
  retryReconciliationExport,
  runManualReconciliation,
} from "../api/reconciliation";
import { listCurrentDocuments, type DocumentRow } from "../api/ingestion";
import type { CaseDetailResponse, CaseSummary, ExportSummary, RunSummary } from "../api/reconciliation";
import { AiLineOpinion } from "../components/AiLineOpinion";
import { AiSharingBanner } from "../components/AiSharingBanner";
import { PageHeader } from "../components/PageHeader";
import { checkRows, REASON_TEXT, type CheckRow, type ReasonText } from "../utils/reconChecks";
import { useAuth } from "../auth/useAuth";
import { usePeriod } from "../contexts/usePeriod";

const statusOptions = [
  ["", "All statuses"],
  ["exception", "Exception"],
  ["needs_review", "Needs Review"],
  ["unlinked", "Unlinked"],
  ["duplicate", "Duplicate"],
  ["pending", "Pending"],
  ["matched_flags", "Matched with flags"],
  ["matched", "Matched"],
  ["approved", "Approved"],
  ["on_hold", "On hold"],
  ["rejected", "Rejected"],
] as const;
const dateFormatter = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" });

function monthStart(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
}

function today(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function displayDate(value: unknown): string {
  if (typeof value !== "string" || !value) return "—";
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : dateFormatter.format(parsed);
}

function amount(value: unknown): string {
  return typeof value === "string" && value !== "" ? `₹ ${value}` : "—";
}

function label(value: unknown): string {
  return typeof value === "string" ? value.replaceAll("_", " ") : "—";
}

interface CaseTableRowProps {
  item: CaseSummary;
  virtualIndex: number;
  isSelected: boolean;
  isActive: boolean;
  onSelect: (caseId: string) => void;
  onToggleSelected: (caseId: string, selected: boolean) => void;
}

export const CaseTableRow = memo(function CaseTableRow({ item, virtualIndex, isSelected, isActive, onSelect, onToggleSelected }: CaseTableRowProps) {
  return (
    <tr
      data-index={virtualIndex}
      style={{ height: 36 }}
      className={isActive ? "is-selected" : ""}
      onClick={() => onSelect(item.case_id)}
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(item.case_id);
        }
      }}
    >
      <td>
        <input
          type="checkbox"
          aria-label={`Select invoice ${item.invoice_no ?? item.case_id}`}
          checked={isSelected}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => onToggleSelected(item.case_id, event.currentTarget.checked)}
        />
      </td>
      <td title={`${item.invoice_no ?? item.canonical_doc_no ?? "Invoice number unavailable"} · ${item.vendor_id ? `Vendor ${item.vendor_id}` : "Unassigned vendor"}`}>
        <strong>{item.invoice_no ?? item.canonical_doc_no ?? "Invoice number unavailable"}</strong>
      </td>
      <td title={item.invoice_date ?? "Date unavailable"}>{displayDate(item.invoice_date)}</td>
      <td title={item.po_nos.join(", ")}>{item.po_nos.length ? item.po_nos.join(", ") : "—"}</td>
      <td title={item.grn_nos.join(", ")}>{item.grn_nos.length ? item.grn_nos.join(", ") : "—"}</td>
      <td title="Invoice taxable value">{amount(item.invoice_taxable)}</td>
      <td title="Expected taxable value (billed quantity × PO rate)">{amount(item.expected_taxable)}</td>
      <td title="Invoice taxable − expected taxable">{amount(item.variance)}</td>
      <td title="Grand total (tax included)">{amount(item.invoice_total)}</td>
      <td title={item.overlay ? `Computed: ${label(item.computed_status)}` : label(item.effective_status)}>
        <span className={`recon-status recon-status-${item.effective_status}`}>
          <span>{item.overlay ? `${label(item.effective_status)} / ${label(item.computed_status)}` : label(item.effective_status)}{item.is_stale ? " · stale" : ""}</span>
        </span>
      </td>
      <td title={item.reason_codes.map(label).join(", ")}>{item.reason_codes.length ? item.reason_codes.map(label).join(", ") : "None"}</td>
    </tr>
  );
});

function checks(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null) : [];
}

export function CheckTable({ rows }: { rows: CheckRow[] }) {
  if (!rows.length) return <p>No rule checks were stored for this case.</p>;
  return (
    <div className="case-lines-wrap">
      <table className="case-lines-table case-checks-table">
        <thead>
          <tr><th>Check</th><th>Invoice value</th><th>Reference value</th><th>Difference</th><th>Rule</th><th>Result</th><th>Why</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>{row.name}</td>
              <td>{row.invoiceValue}</td>
              <td>{row.referenceValue}</td>
              <td>{row.difference}{row.differencePct !== "—" ? <small> ({row.differencePct})</small> : null}</td>
              <td>{row.rule}</td>
              <td>{row.result}</td>
              <td>{row.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function documentLabel(document: DocumentRow): string {
  const number = document["doc_no"] ?? document["canonical_doc_no"] ?? document.id.slice(0, 8);
  const vendor = document["vendor_name"] ?? "Unknown vendor";
  const dateValue = document["doc_date"] ?? "date unknown";
  return `${String(number)} · ${String(vendor)} · ${String(dateValue)}`;
}

export function ReconciliationPage() {
  const { user, hasCapability } = useAuth();
  const { period } = usePeriod();
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState<CaseSummary[]>([]);
  const tableWrapRef = useRef<HTMLDivElement>(null);
  const loadingMoreRef = useRef(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<RunSummary | null>(null);
  const [exportJob, setExportJob] = useState<ExportSummary | null>(null);
  const [detail, setDetail] = useState<CaseDetailResponse | null>(null);
  const [reasonCatalogue, setReasonCatalogue] = useState<Record<string, ReasonText>>(REASON_TEXT);
  useEffect(() => {
    let active = true;
    getReasonCodes()
      .then((catalogue) => {
        if (!active) return;
        setReasonCatalogue((previous) => ({
          ...previous,
          ...Object.fromEntries(catalogue.items.map((item) => [item.code, { title: item.title, message: item.message }])),
        }));
      })
      .catch(() => undefined); // the built-in sentences stay
    return () => {
      active = false;
    };
  }, []);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailEtag, setDetailEtag] = useState('"1"');
  const selectedCaseId = searchParams.get("case_id");
  // A run opened from Mapping or the Run button covers invoices of any date, so its cases
  // are shown without the period's date filter until the user picks dates again.
  const runInView = searchParams.get("run_id");
  const [status, setStatus] = useState("");
  const [fromDate, setFromDate] = useState(() => (runInView ? "" : monthStart()));
  const [toDate, setToDate] = useState(() => (runInView ? "" : today()));
  const [kpis, setKpis] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionDialog, setActionDialog] = useState<"approve" | "hold" | "reject" | "note" | "release" | null>(null);
  const [actionText, setActionText] = useState("");
  const [selectedCases, setSelectedCases] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<"approve" | "hold" | "reject">("approve");
  const [bulkReason, setBulkReason] = useState("");
  const [manualOpen, setManualOpen] = useState(false);
  const [manualDocuments, setManualDocuments] = useState<DocumentRow[]>([]);
  const [manualInvoiceId, setManualInvoiceId] = useState("");
  const [manualPoIds, setManualPoIds] = useState<string[]>([]);
  const [manualGrnIds, setManualGrnIds] = useState<string[]>([]);
  const [manualResult, setManualResult] = useState<Record<string, unknown> | null>(null);
  const canRun = hasCapability("recon.run");
  const canDecide = hasCapability("cases.decide");
  const hasStaleCases = useMemo(() => items.some((item) => item.is_stale), [items]);
  const getCaseKey = useCallback((index: number) => items[index]?.case_id ?? index, [items]);

  useEffect(() => {
    const year = Number(period.slice(0, 4));
    const month = Number(period.slice(5, 7));
    const lastDay = new Date(year, month, 0).getDate();
    const timer = window.setTimeout(() => {
      setFromDate(runInView ? "" : `${period}-01`);
      setToDate(runInView ? "" : `${period}-${String(lastDay).padStart(2, "0")}`);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [period, runInView]);

  const refreshCases = useCallback(async (nextCursor: string | null = null) => {
    if (nextCursor && loadingMoreRef.current) return;
    if (nextCursor) loadingMoreRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const page = await listReconciliationCases({
        vendorId: searchParams.get("vendor_id"),
        runId: searchParams.get("run_id"),
        status: status || null,
        from: fromDate || null,
        to: toDate || null,
        cursor: nextCursor,
      });
      setItems((current) => nextCursor ? [...current, ...page.items] : page.items);
      setCursor(nextCursor);
      setNextCursor(page.next_cursor);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Reconciliation cases could not be loaded.");
    } finally {
      setLoading(false);
      if (nextCursor) loadingMoreRef.current = false;
    }
  }, [fromDate, searchParams, status, toDate]);

  // TanStack Virtual intentionally exposes imperative scroll measurements, which React Compiler cannot memoize.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => tableWrapRef.current,
    estimateSize: () => 36,
    overscan: 4,
    getItemKey: getCaseKey,
  });
  const virtualRows = rowVirtualizer.getVirtualItems();
  const firstVirtualRow = virtualRows[0];
  const lastVirtualRow = virtualRows.at(-1);

  const loadNextPageNearEnd = useCallback(() => {
    const element = tableWrapRef.current;
    if (!element || !nextCursor || loading || loadingMoreRef.current) return;
    const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
    if (remaining <= 36 * 3) void refreshCases(nextCursor);
  }, [loading, nextCursor, refreshCases]);

  useEffect(() => {
    loadNextPageNearEnd();
  }, [items.length, loadNextPageNearEnd]);

  const refreshKpis = useCallback(async () => {
    if (!fromDate || !toDate) return;
    try {
      setKpis(await getReconciliationKpis(fromDate, toDate));
    } catch {
      setKpis(null);
    }
  }, [fromDate, toDate]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshCases();
      void refreshKpis();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshCases, refreshKpis]);

  useEffect(() => {
    const runId = searchParams.get("run_id");
    if (!runId) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const run = await getReconciliationRun(runId);
        if (cancelled) return;
        setActiveRun(run);
        if (run.status === "succeeded") {
          setNotice(`Run completed: ${run.done_cases} invoice cases evaluated.`);
          await refreshCases();
          await refreshKpis();
          return;
        }
        if (run.status === "failed") {
          setError(run.error_message ?? "The reconciliation run failed.");
          return;
        }
        timer = window.setTimeout(() => void poll(), 1500);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Run progress could not be loaded.");
      }
    };
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [refreshCases, refreshKpis, searchParams]);

  useEffect(() => {
    if (searchParams.get("run_id")) return;
    let cancelled = false;
    void listRecentReconciliationRuns().then((runs) => {
      const latest = runs[0];
      if (!cancelled && latest) setActiveRun(latest);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [searchParams]);

  useEffect(() => {
    if (!selectedCaseId) return;
    let cancelled = false;
    setDetailLoading(true);
    void getReconciliationCase(selectedCaseId).then((result) => {
      if (cancelled) return;
      setDetail(result.value);
      setDetailEtag(result.etag);
    }).catch((cause: unknown) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Case details could not be loaded.");
    }).finally(() => {
      if (!cancelled) setDetailLoading(false);
    });
    return () => { cancelled = true; };
  }, [selectedCaseId]);

  useEffect(() => {
    if (!exportJob || !["queued", "building"].includes(exportJob.status)) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const latest = await getReconciliationExport(exportJob.id);
        if (cancelled) return;
        if (latest.status === "ready") {
          await downloadReconciliationExport(latest.id);
          if (!cancelled) {
            setExportJob(latest);
            setNotice("Excel export is ready and has been downloaded.");
          }
          return;
        }
        if (latest.status === "failed") {
          setExportJob(latest);
          setError(latest.error_message ?? "The Excel export failed. You can retry it.");
          return;
        }
        if (latest.status !== exportJob.status || latest.version !== exportJob.version) {
          setExportJob(latest);
          return;
        }
        timer = window.setTimeout(() => void poll(), 1500);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Export status could not be loaded.");
      }
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [exportJob]);

  const run = async () => {
    if (!user?.csrf_token) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const vendorId = searchParams.get("vendor_id");
      const created = await createReconciliationRun(
        user.csrf_token,
        vendorId ? { kind: "vendor", vendor_id: vendorId } : { kind: "all" },
      );
      setSearchParams((previous) => {
        const next = new URLSearchParams(previous);
        next.set("run_id", created.id);
        return next;
      });
      setActiveRun({ id: created.id, status: "queued", done_cases: 0, total_cases: 0, status_counts: {}, error_code: null, error_message: null, started_at: null, finished_at: null, metrics: {} });
      setNotice("Reconciliation was queued. You can leave this page; processing continues in the worker.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The reconciliation run could not be started.");
    } finally {
      setBusy(false);
    }
  };

  const exportExcel = async () => {
    if (!activeRun || activeRun.status !== "succeeded" || !user?.csrf_token) return;
    setBusy(true);
    setError(null);
    try {
      const vendorId = searchParams.get("vendor_id");
      const created = await createReconciliationExport(
        activeRun.id,
        {
          ...(vendorId ? { vendor_id: vendorId } : {}),
          ...(status ? { status: [status] } : {}),
          ...(fromDate ? { from: fromDate } : {}),
          ...(toDate ? { to: toDate } : {}),
        },
        user.csrf_token,
      );
      setExportJob({ id: created.id, run_id: activeRun.id, status: "queued", error_code: null, error_message: null, version: 1, expires_at: null });
      setNotice("Excel workbook is being prepared in the background.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The Excel export could not be started.");
    } finally {
      setBusy(false);
    }
  };

  const retryExport = async () => {
    if (!exportJob || !user?.csrf_token) return;
    try {
      await retryReconciliationExport(exportJob.id, exportJob.version, user.csrf_token);
      setExportJob({ ...exportJob, status: "queued", version: exportJob.version + 1 });
      setError(null);
      setNotice("Export retry was queued.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The export retry could not be queued.");
    }
  };

  const selectCase = useCallback((caseId: string | null) => {
    if (caseId) setDetail(null);
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (caseId) next.set("case_id", caseId);
      else next.delete("case_id");
      return next;
    });
  }, [setSearchParams]);

  const selectCaseFromRow = useCallback((caseId: string) => selectCase(caseId), [selectCase]);
  const toggleCaseSelection = useCallback((caseId: string, selected: boolean) => {
    setSelectedCases((previous) => {
      const next = new Set(previous);
      if (selected) next.add(caseId);
      else next.delete(caseId);
      return next;
    });
  }, []);

  const runBulkAction = async () => {
    if (!user?.csrf_token || !bulkReason.trim()) {
      setError("Enter a reason before applying a bulk decision.");
      return;
    }
    const selected = items.filter((item) => selectedCases.has(item.case_id));
    if (!selected.length) return;
    setBusy(true);
    setError(null);
    try {
      const response = await bulkCaseAction(
        selected.map((item) => ({ case_id: item.case_id, expected_version: item.version })),
        bulkAction,
        bulkReason.trim(),
        user.csrf_token,
      );
      const conflicts = response.items.filter((item) => item["status"] === 409).length;
      const completed = response.items.filter((item) => item["status"] === 200).length;
      setNotice(`Bulk decision saved for ${completed} case${completed === 1 ? "" : "s"}${conflicts ? `; ${conflicts} changed and need reload` : ""}.`);
      setSelectedCases(new Set());
      setBulkReason("");
      await refreshCases();
      await refreshKpis();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Bulk decision could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  const openManualRecon = async () => {
    setManualOpen(true);
    setManualResult(null);
    try {
      const [invoices, purchaseOrders, receipts] = await Promise.all([
        listCurrentDocuments("invoice"), listCurrentDocuments("po"), listCurrentDocuments("grn"),
      ]);
      setManualDocuments([...invoices, ...purchaseOrders, ...receipts]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Manual Recon documents could not be loaded.");
    }
  };

  const submitManualRecon = async () => {
    if (!user?.csrf_token || !manualInvoiceId || !manualPoIds.length) return;
    setBusy(true);
    setError(null);
    try {
      setManualResult(await runManualReconciliation(manualInvoiceId, manualPoIds, manualGrnIds, user.csrf_token));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Manual Recon could not be evaluated.");
    } finally {
      setBusy(false);
    }
  };

  const submitAction = async () => {
    if (!selectedCaseId || !detail || !user?.csrf_token || !actionDialog) return;
    if ((actionDialog === "approve" || actionDialog === "hold" || actionDialog === "reject" || actionDialog === "note") && !actionText.trim()) {
      setError(actionDialog === "note" ? "Enter a note before saving." : "Enter a reason before continuing.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await addCaseAction(selectedCaseId, detailEtag, actionDialog, user.csrf_token, actionText.trim());
      const refreshed = await getReconciliationCase(selectedCaseId);
      setDetail(refreshed.value);
      setDetailEtag(refreshed.etag);
      setActionDialog(null);
      setActionText("");
      setNotice("Decision saved. The computed reconciliation result remains unchanged; the effective status now includes the review action.");
      await refreshCases(cursor);
      await refreshKpis();
    } catch (cause) {
      const errorValue = cause as Error & { status?: number };
      if (errorValue.status === 409) {
        setError("This case changed since you opened it. The latest case is being reloaded; review it before trying again.");
        const refreshed = await getReconciliationCase(selectedCaseId).catch(() => null);
        if (refreshed) {
          setDetail(refreshed.value);
          setDetailEtag(refreshed.etag);
        }
      } else {
        setError(cause instanceof Error ? cause.message : "The decision could not be saved.");
      }
    } finally {
      setBusy(false);
    }
  };

  const totalLabel = useMemo(() => {
    if (!activeRun) return "Latest cases";
    return `${label(activeRun.status)} · ${activeRun.done_cases} of ${activeRun.total_cases} cases`;
  }, [activeRun]);

  return (
    <>
      <PageHeader
        eyebrow="INVOICE-LED RECONCILIATION"
        title="Reconciliation"
        description="Each invoice receives an explainable result. Ambiguous links and low-confidence evidence stay in review rather than being forced into a match."
        action={<div className="recon-header-actions"><button className="button" type="button" disabled={!canRun || busy} onClick={() => void openManualRecon()}>Manual Recon</button><button className="button" type="button" disabled={!activeRun || activeRun.status !== "succeeded" || busy || hasStaleCases} onClick={() => void exportExcel()}><Download size={15} /> Export Excel</button><button className="button button-primary" type="button" disabled={!canRun || busy || activeRun?.status === "queued" || activeRun?.status === "running"} onClick={() => void run()}><RefreshCw size={15} /> {items.length ? "Run / Re-run" : "Run reconciliation"}</button></div>}
      />

      <AiSharingBanner />
      {error ? <div className="inline-error" role="alert">{error}</div> : null}
      {notice ? <div className="mapping-info" role="status">{notice}</div> : null}
      {hasStaleCases ? <div className="recon-stale-banner"><AlertTriangle size={16} /> These cases are stale. Run reconciliation again before exporting this view.</div> : null}
      {exportJob ? <div className="recon-progress panel" role="status"><div><strong>Excel export · {label(exportJob.status)}</strong>{exportJob.status === "failed" ? <button className="button" type="button" onClick={() => void retryExport()}>Retry export</button> : null}</div>{["queued", "building"].includes(exportJob.status) ? <small>Workbook is generated from the selected run and filters.</small> : null}</div> : null}

      <section className="panel recon-toolbar" aria-label="Reconciliation filters">
        <label><span>Invoice date from</span><input type="date" value={fromDate} onChange={(event) => setFromDate(event.currentTarget.value)} /></label>
        <label><span>To</span><input type="date" value={toDate} onChange={(event) => setToDate(event.currentTarget.value)} /></label>
        <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.currentTarget.value)}>{statusOptions.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
        {searchParams.get("vendor_id") ? <button className="button button-subtle" type="button" onClick={() => setSearchParams({})}>Clear vendor scope</button> : null}
        {runInView ? <small className="recon-all-dates">Showing the cases of this run{!fromDate && !toDate ? " (all invoice dates)" : ""}. <button className="text-button" type="button" onClick={() => setSearchParams((previous) => { const next = new URLSearchParams(previous); next.delete("run_id"); return next; })}>Show all cases</button></small> : null}
        <span className="recon-run-state" role="status">{totalLabel}</span>
      </section>

      {activeRun && (activeRun.status === "queued" || activeRun.status === "running") ? (
        <section className="recon-progress panel" aria-label="Reconciliation progress">
          <div><strong>{activeRun.status === "queued" ? "Waiting for worker" : "Evaluating invoice cases"}</strong><span>{activeRun.done_cases} / {activeRun.total_cases} invoices</span></div>
          <progress max={Math.max(activeRun.total_cases, 1)} value={activeRun.done_cases} />
          <small>You can navigate away; the worker continues this run.</small>
        </section>
      ) : null}

      {kpis ? (
        <section className="recon-kpis" aria-label="Period KPI summary">
          {([["Invoices", kpis["invoice_count"]], ["Matched %", kpis["matched_pct"]], ["Pending", kpis["pending_count"]], ["Exceptions", kpis["exception_count"]], ["Unlinked", kpis["unlinked_count"]], ["At risk", amount(kpis["at_risk_total"])], ["GRNI", amount(kpis["grni_total"])]] as Array<[string, unknown]>).map(([name, value]) => <div className="panel recon-kpi" key={name}><span>{name}</span><strong>{value === null || value === undefined ? "—" : String(value)}</strong></div>)}
        </section>
      ) : null}

      <section className="panel recon-table-panel">
        <div className="panel-heading"><div><div className="panel-kicker">CASE QUEUE</div><h2>Invoice cases</h2></div><button className="icon-button" type="button" aria-label="Refresh cases" disabled={loading} onClick={() => void refreshCases()}><RefreshCw size={15} /></button></div>
        {selectedCases.size ? <div className="bulk-action-bar"><strong>{selectedCases.size} selected</strong><select aria-label="Bulk action" value={bulkAction} onChange={(event) => setBulkAction(event.currentTarget.value as "approve" | "hold" | "reject")}><option value="approve">Approve</option><option value="hold">Hold</option><option value="reject">Reject</option></select><input aria-label="Bulk decision reason" placeholder="Required reason" value={bulkReason} onChange={(event) => setBulkReason(event.currentTarget.value)} /><button className="button button-primary" type="button" disabled={busy || !bulkReason.trim()} onClick={() => void runBulkAction()}>Apply to selected</button><button className="button button-subtle" type="button" onClick={() => setSelectedCases(new Set())}>Clear selection</button></div> : null}
        <div className="recon-table-wrap" ref={tableWrapRef} onScroll={loadNextPageNearEnd}>
          <table className="recon-table">
            <thead><tr><th>Select</th><th>Invoice</th><th>Date</th><th>PO(s)</th><th>GRN(s)</th><th>Invoice taxable</th><th>Expected taxable</th><th>Variance (taxable)</th><th>Grand total</th><th>Status</th><th>Issues</th></tr></thead>
            <tbody>
              {firstVirtualRow && firstVirtualRow.start > 0 ? <tr aria-hidden="true" className="recon-virtual-spacer"><td colSpan={11} style={{ height: firstVirtualRow.start }} /></tr> : null}
              {virtualRows.map((virtualRow) => {
                const item = items[virtualRow.index];
                if (!item) return null;
                return <CaseTableRow key={item.case_id} item={item} virtualIndex={virtualRow.index} isSelected={selectedCases.has(item.case_id)} isActive={selectedCaseId === item.case_id} onSelect={selectCaseFromRow} onToggleSelected={toggleCaseSelection} />;
              })}
              {lastVirtualRow && lastVirtualRow.end < rowVirtualizer.getTotalSize() ? <tr aria-hidden="true" className="recon-virtual-spacer"><td colSpan={11} style={{ height: rowVirtualizer.getTotalSize() - lastVirtualRow.end }} /></tr> : null}
              {!loading && items.length === 0 ? <tr><td className="recon-table-empty" colSpan={11}>No cases for these filters yet. Run reconciliation after documents are mapped.</td></tr> : null}
              {loading && items.length === 0 ? <tr><td className="recon-table-empty" colSpan={11}>Loading invoice cases…</td></tr> : null}
            </tbody>
          </table>
        </div>
        <div className="recon-table-footer"><span>{items.length} loaded · ordered by severity and invoice date</span>{nextCursor ? <button className="button" type="button" disabled={loading} onClick={() => void refreshCases(nextCursor)}>Load more <ChevronDown size={14} /></button> : null}</div>
      </section>

      {selectedCaseId && detailLoading && (!detail || String(detail.case["case_id"]) !== selectedCaseId) ? (
        <div className="case-panel-backdrop" role="presentation">
          <aside className="case-panel case-panel-loading" role="dialog" aria-modal="true" aria-label="Loading case details" aria-busy="true">
            <div className="case-panel-heading"><div><div className="panel-kicker">CASE EVIDENCE</div><h2>Loading invoice case…</h2></div></div>
            <div className="case-panel-skeleton" aria-hidden="true"><span /><span /><span /><span /></div>
            <div className="case-panel-section"><h3>Why this status</h3><div className="case-panel-skeleton" aria-hidden="true"><span /><span /><span /></div></div>
            <div className="case-panel-section"><h3>Invoice | PO | GRN lines</h3><div className="case-panel-skeleton" aria-hidden="true"><span /><span /><span /><span /></div></div>
          </aside>
        </div>
      ) : null}

      {detail && selectedCaseId && String(detail.case["case_id"]) === selectedCaseId ? (
        <div className="case-panel-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) selectCase(null); }}>
          <aside className="case-panel" role="dialog" aria-modal="true" aria-labelledby="case-panel-title">
            <div className="case-panel-heading"><div><div className="panel-kicker">CASE EVIDENCE</div><h2 id="case-panel-title">{String(detail.case["invoice_no"] ?? detail.case["invoice_id"] ?? "Invoice case")}</h2></div><div className="case-panel-tools"><button className="icon-button" type="button" aria-label="Copy case link" onClick={() => void navigator.clipboard.writeText(window.location.href)}><Clipboard size={15} /></button><button className="icon-button" type="button" aria-label="Close case details" onClick={() => selectCase(null)}><X size={17} /></button></div></div>
            <div className="case-panel-status"><span className={`recon-status recon-status-${String(detail.case["effective_status"] ?? "pending")}`}>{label(detail.case["effective_status"])}</span>{detail.case["overlay"] ? <small>Computed result: {label(detail.case["status"])}</small> : null}</div>
            {detail.case["is_stale"] ? <div className="recon-stale-banner"><AlertTriangle size={15} /> Mapping or settings changed; run again before export.</div> : null}
            <section className="case-summary-grid" aria-label="Financial summary">
              <div><span>Invoice taxable</span><strong>{amount(detail.case["invoice_taxable"])}</strong></div>
              <div><span>Expected taxable</span><strong>{amount(detail.case["expected_taxable"])}</strong></div>
              <div><span>Variance (taxable)</span><strong>{amount(detail.case["variance"])}</strong></div>
              <div><span>Grand total</span><strong>{amount(detail.case["invoice_total"])}</strong></div>
              <div><span>At risk</span><strong>{amount(detail.case["at_risk"])}</strong></div>
            </section>
            <section className="case-panel-section"><h3>Why this status</h3><CheckTable rows={checkRows(checks(detail.case["checks"]), reasonCatalogue)} /></section>
            <section className="case-panel-section" aria-label="Cumulative PO and GRN ledger"><h3>Cumulative PO / GRN ledger</h3><div className="case-ledger-list">{detail.lines.map((line, index) => <div className="case-ledger-row" key={`ledger-${String(line["line_no"])}-${index}`}><strong>Line {String(line["line_no"] ?? index + 1)}</strong><span>PO ordered <b>{String(line["po_qty"] ?? "—")}</b></span><span>Billed before <b>{String(line["billed_before"] ?? "—")}</b></span><span>This invoice <b>{String(line["billed_qty"] ?? "—")}</b></span><span>GRN accepted <b>{String(line["grn_accepted_cum"] ?? "—")}</b></span><span>Balance after <b>{String(line["balance_after"] ?? "—")}</b></span></div>)}{!detail.lines.length ? <p>No linked line ledger is available for this case.</p> : null}</div></section>
            <section className="case-panel-section"><h3>Invoice | PO | GRN lines</h3><div className="case-lines-wrap"><table className="case-lines-table"><thead><tr><th>Line</th><th>Item result</th><th>Invoice qty</th><th>PO qty</th><th>GRN accepted</th><th>Billed before</th><th>Balance after</th><th>Status</th></tr></thead><tbody>{detail.lines.map((line, index) => <tr key={`${String(line["line_no"])}-${index}`}><td>{String(line["line_no"] ?? index + 1)}</td><td>{label(line["alignment_state"])}<AiLineOpinion value={line["ai_opinion"]} /></td><td>{String(line["billed_qty"] ?? "—")}</td><td>{String(line["po_qty"] ?? "—")}</td><td>{String(line["grn_accepted_cum"] ?? "—")}</td><td>{String(line["billed_before"] ?? "—")}</td><td>{String(line["balance_after"] ?? "—")}</td><td>{label(line["status"])}</td></tr>)}</tbody></table></div></section>
            <section className="case-panel-section"><h3>Source evidence</h3>{detail.documents.map((document, index) => <details className="case-evidence-document" key={`${String(document["document_id"])}-${index}`}><summary>{label(document["doc_type"])} · {String(document["doc_no"] ?? "No document number")} · {displayDate(document["doc_date"])}</summary><div className="case-evidence-fields">{Array.isArray(document["fields"]) ? document["fields"].map((field, fieldIndex) => isEvidenceField(field) ? <EvidenceField key={`${field.field}-${fieldIndex}`} field={field} /> : null) : null}<small>Source file: {String(document["display_name"] ?? "unavailable")}</small></div></details>)}</section>
            <section className="case-panel-section"><h3>Decision history</h3><ol className="case-history">{detail.history.map((event, index) => <li key={`${String(event["id"])}-${index}`}><strong>{label(event["action"])}</strong><span>{String(event["reason"] ?? event["note"] ?? "")}</span><small>{String(event["occurred_at"] ?? "")}</small></li>)}</ol>{!detail.history.length ? <p>No human actions yet.</p> : null}</section>
            {canDecide ? <div className="case-action-bar"><button className="button button-primary" type="button" onClick={() => setActionDialog("approve")}><Check size={14} /> Approve</button><button className="button" type="button" onClick={() => setActionDialog("hold")}>Hold</button><button className="button button-danger" type="button" onClick={() => setActionDialog("reject")}>Reject</button><button className="button button-subtle" type="button" onClick={() => setActionDialog("note")}>Add note</button>{detail.case["overlay"] ? <button className="button button-subtle" type="button" onClick={() => { setActionText(""); setActionDialog("release"); }}>Release decision</button> : null}</div> : <div className="case-readonly-state">Read-only view. You do not have permission to change this case.</div>}
          </aside>
        </div>
      ) : null}

      {actionDialog ? <div className="case-action-modal-backdrop"><form className="case-action-modal" onSubmit={(event) => { event.preventDefault(); void submitAction(); }}><div className="panel-heading"><div><div className="panel-kicker">REVIEW DECISION</div><h2>{label(actionDialog)}</h2></div><button className="icon-button" type="button" aria-label="Close decision dialog" onClick={() => setActionDialog(null)}><X size={16} /></button></div>{actionDialog === "release" ? <p>This clears the active approval, hold, or rejection overlay. The computed engine result remains recorded.</p> : <label><span>{actionDialog === "note" ? "Note" : "Reason"}{actionDialog === "approve" ? " (required)" : ""}</span><textarea value={actionText} onChange={(event) => setActionText(event.currentTarget.value)} required={actionDialog === "approve" || actionDialog === "note"} rows={4} /></label>}<div className="case-action-modal-buttons"><button className="button" type="button" onClick={() => setActionDialog(null)}>Cancel</button><button className="button button-primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save decision"}</button></div></form></div> : null}

      {manualOpen ? <div className="case-action-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setManualOpen(false); }}><form className="case-action-modal manual-recon-modal" onSubmit={(event) => { event.preventDefault(); void submitManualRecon(); }}><div className="panel-heading"><div><div className="panel-kicker">ONE-OFF ENGINE EVALUATION</div><h2>Manual Recon</h2></div><button className="icon-button" type="button" aria-label="Close Manual Recon" onClick={() => setManualOpen(false)}><X size={16} /></button></div><p>Choose an invoice and supporting PO/GRN evidence. This runs the same deterministic rules immediately; it does not save links or change the current case.</p><label><span>Invoice</span><select value={manualInvoiceId} onChange={(event) => setManualInvoiceId(event.currentTarget.value)} required><option value="">Choose an invoice</option>{manualDocuments.filter((document) => document["doc_type"] === "invoice").map((document) => <option key={document.id} value={document.id}>{documentLabel(document)}</option>)}</select></label><label><span>Purchase orders (choose at least one)</span><select multiple size={5} value={manualPoIds} onChange={(event) => setManualPoIds(Array.from(event.currentTarget.selectedOptions, (option) => option.value))}>{manualDocuments.filter((document) => document["doc_type"] === "po").map((document) => <option key={document.id} value={document.id}>{documentLabel(document)}</option>)}</select></label><label><span>Goods receipts (optional, multiple)</span><select multiple size={5} value={manualGrnIds} onChange={(event) => setManualGrnIds(Array.from(event.currentTarget.selectedOptions, (option) => option.value))}>{manualDocuments.filter((document) => document["doc_type"] === "grn").map((document) => <option key={document.id} value={document.id}>{documentLabel(document)}</option>)}</select></label>{manualResult ? <div className="manual-result" role="status"><strong>Engine result: {label(manualResult["status"])}</strong><span>Payable taxable: {amount(manualResult["payable_taxable"])}</span><span>At risk: {amount(manualResult["at_risk"])}</span><span>Reasons: {Array.isArray(manualResult["reason_codes"]) ? manualResult["reason_codes"].map(label).join(", ") || "None" : "—"}</span><small>This is a preview result. Run reconciliation to persist it in the case list.</small></div> : null}<div className="case-action-modal-buttons"><button className="button" type="button" onClick={() => setManualOpen(false)}>Close</button><button className="button button-primary" type="submit" disabled={busy || !manualInvoiceId || !manualPoIds.length}>{busy ? "Evaluating…" : "Evaluate"}</button></div></form></div> : null}
    </>
  );
}

function isEvidenceField(value: unknown): value is Record<string, unknown> & { field: string } {
  return typeof value === "object" && value !== null && typeof (value as { field?: unknown }).field === "string";
}

function EvidenceField({ field }: { field: Record<string, unknown> & { field: string } }) {
  const value = field["value"];
  const confidence = field["confidence"];
  const page = field["page"];
  const snippet = field["snippet"];
  const pageLabel = typeof page === "number" || typeof page === "string" ? ` · page ${page}` : "";
  return (
    <p>
      <strong>{label(field.field)}</strong>: {typeof value === "string" || typeof value === "number" ? String(value) : "—"}
      <small>confidence {confidence === null || confidence === undefined ? "—" : String(confidence)}{pageLabel}</small>
      {typeof snippet === "string" && snippet ? <blockquote>{snippet}</blockquote> : null}
    </p>
  );
}
