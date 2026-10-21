import { useEffect, useState } from "react";
import { Check, X } from "lucide-react";

import { displayError, getFileContent, saveColumnMapping } from "../api/ingestion";
import { parseCsvPreviewRows } from "../utils/csvPreview";

const CANONICAL_FIELDS = [
  ["doc_no", "Document number"],
  ["doc_date", "Document date"],
  ["vendor_name", "Vendor name"],
  ["vendor_gstin", "Vendor GSTIN"],
  ["buyer_gstin", "Buyer GSTIN"],
  ["po_refs", "PO reference"],
  ["grn_refs", "GRN reference"],
  ["description", "Item description"],
  ["item_code", "Item code"],
  ["hsn_sac", "HSN / SAC"],
  ["uom", "Unit of measure"],
  ["quantity", "Quantity"],
  ["rate", "Unit rate"],
  ["amount", "Line amount"],
  ["taxable_total", "Taxable total"],
  ["cgst", "CGST"],
  ["sgst", "SGST"],
  ["igst", "IGST"],
  ["grand_total", "Invoice / document total"],
  ["tax_rate", "Tax rate"],
] as const;

export function CsvMappingDialog({
  fileId,
  fileName,
  csrfToken,
  onClose,
  onSaved,
}: {
  fileId: string;
  fileName: string;
  csrfToken: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [rows, setRows] = useState<string[][]>([]);
  const [headerIndex, setHeaderIndex] = useState(0);
  const [mappedFields, setMappedFields] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const header = rows[headerIndex] ?? [];
  const nonBlankHeader = header.map((value, index) => value.trim() || `Column ${index + 1}`);
  const mappingCount = Object.values(mappedFields).filter(Boolean).length;

  useEffect(() => {
    let active = true;
    void getFileContent(fileId)
      .then((response) => response.text())
      .then((content) => {
        if (!active) return;
        const previewRows = parseCsvPreviewRows(content);
        if (previewRows.length === 0) throw new Error("The CSV has no readable rows.");
        setRows(previewRows);
        const bestHeader = previewRows.reduce(
          (best, candidate, index) => (candidate.length > (previewRows[best]?.length ?? 0) ? index : best),
          0,
        );
        setHeaderIndex(bestHeader);
      })
      .catch((cause: unknown) => {
        if (active) setError(displayError(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [fileId]);

  const save = async () => {
    const columnMapping: Record<string, string> = {};
    nonBlankHeader.forEach((label, index) => {
      const field = mappedFields[index];
      if (field) columnMapping[label] = field;
    });
    if (Object.keys(columnMapping).length < 3) {
      setError("Map at least three columns before saving.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await saveColumnMapping(fileId, columnMapping, csrfToken);
      onSaved();
    } catch (cause) {
      setError(displayError(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="dialog-card csv-mapping-dialog" role="dialog" aria-modal="true" aria-labelledby="mapping-title">
        <div className="dialog-heading">
          <div>
            <div className="panel-kicker">CSV LAYOUT</div>
            <h2 id="mapping-title">Map columns for {fileName}</h2>
            <p>Choose what each source column means. This map is saved for the same file layout and the parse is queued again.</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close mapping dialog" onClick={onClose}><X size={18} /></button>
        </div>
        {loading ? <p className="preview-message" role="status">Reading CSV headers…</p> : null}
        {!loading && rows.length > 1 ? (
          <label className="header-row-picker">
            Header row
            <select value={headerIndex} onChange={(event) => { setHeaderIndex(Number(event.target.value)); setMappedFields({}); }}>
              {rows.slice(0, 20).map((row, index) => (
                <option key={index} value={index}>Row {index + 1}: {row.slice(0, 3).join(" · ")}</option>
              ))}
            </select>
          </label>
        ) : null}
        {!loading && header.length > 0 ? (
          <div className="mapping-table-wrap">
            <table className="mapping-table">
              <thead><tr><th>Source column</th><th>Example value</th><th>Map to</th></tr></thead>
              <tbody>
                {nonBlankHeader.map((label, index) => (
                  <tr key={`${index}-${label}`}>
                    <td>{label}</td>
                    <td>{rows[headerIndex + 1]?.[index] ?? "—"}</td>
                    <td>
                      <select
                        aria-label={`Map ${label}`}
                        value={mappedFields[index] ?? ""}
                        onChange={(event) => {
                          // Read the value now: the updater runs later, after React has reset the event.
                          const value = event.currentTarget.value;
                          setMappedFields((previous) => ({ ...previous, [index]: value }));
                        }}
                      >
                        <option value="">Ignore this column</option>
                        {CANONICAL_FIELDS.map(([value, title]) => <option key={value} value={value}>{title}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        <div className="dialog-actions">
          <span>{mappingCount} columns mapped · at least 3 required</span>
          <div>
            <button className="button button-secondary" type="button" onClick={onClose}>Cancel</button>
            <button className="button button-primary" type="button" disabled={loading || saving || mappingCount < 3} onClick={() => void save()}>
              <Check size={15} aria-hidden="true" /> {saving ? "Saving…" : "Save and retry"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
