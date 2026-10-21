import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Plus, RotateCcw, X } from "lucide-react";

import type { MappingVendor } from "../api/mapping";
import {
  SYNONYM_GROUPS,
  THRESHOLDS,
  TOLERANCE_CHECKS,
  gstinProblem,
  type EngineBody,
  type ToleranceCheck,
  type ToleranceValues,
} from "./configModel";

export interface SectionProps {
  engine: EngineBody;
  defaults: EngineBody | null;
  errors: Record<string, string>;
  update: (change: (engine: EngineBody) => void) => void;
}

function FieldError({ errors, path }: { errors: Record<string, string>; path: string }) {
  const message = errors[path];
  return message ? <small className="settings-field-error" role="alert">{message}</small> : null;
}

function DefaultNote({ current, initial, onReset, label }: { current: string; initial: string | undefined; onReset: () => void; label: string }) {
  if (initial === undefined) return null;
  if (current === initial) return <span className="settings-default">Default</span>;
  return <span className="settings-default settings-default-changed">Default {initial} <button className="button button-subtle settings-reset" type="button" aria-label={`Reset ${label} to default`} onClick={onReset}><RotateCcw size={12} /> Reset</button></span>;
}

function Section({ kicker, title, children, description, id }: { kicker: string; title: string; description: ReactNode; children: ReactNode; id?: string }) {
  return <section className="panel settings-panel" aria-label={title} id={id}><div className="panel-heading"><div><div className="panel-kicker">{kicker}</div><h2>{title}</h2></div></div><p>{description}</p>{children}</section>;
}

export function TolerancesSection({ engine, defaults, errors, update, vendors }: SectionProps & { vendors: MappingVendor[] }) {
  const tolerances = engine.tolerances;
  const mode = tolerances.limit_mode;
  const vendorName = (id: string | null | undefined) => vendors.find((vendor) => vendor.id === id)?.canonical_name ?? id ?? "";
  const setValue = (check: ToleranceCheck, key: "pct" | "abs", value: string) => update((next) => { next.tolerances[check][key] = value; });
  const rows = [
    ...engine.overrides.vendors.flatMap((override, index) => Object.entries(override.tolerances).map(([check, values]) => ({ id: `v-${index}-${check}`, kind: "vendor" as const, index, check, values, scope: `Vendor · ${vendorName(override.vendor_id)}` }))),
    ...engine.overrides.items.flatMap((override, index) => Object.entries(override.tolerances).map(([check, values]) => ({ id: `i-${index}-${check}`, kind: "item" as const, index, check, values, scope: `Item · ${override.item_code}${override.vendor_id ? ` · ${vendorName(override.vendor_id)}` : ""}` }))),
  ];
  const setOverride = (kind: "vendor" | "item", index: number, check: string, key: "pct" | "abs", value: string) => update((next) => {
    const list = kind === "vendor" ? next.overrides.vendors : next.overrides.items;
    const target = list[index]?.tolerances[check];
    if (target) target[key] = value;
  });
  const removeOverride = (kind: "vendor" | "item", index: number, check: string) => update((next) => {
    const list = kind === "vendor" ? next.overrides.vendors : next.overrides.items;
    const target = list[index];
    if (!target) return;
    delete target.tolerances[check];
    if (!Object.keys(target.tolerances).length) list.splice(index, 1);
  });
  return <Section kicker="RECONCILIATION CONTROLS" title="Tolerances" description="Global limits apply unless an override matches. Overrides resolve item for a vendor, then item, then vendor, then global.">
    <div className="recon-table-wrap"><table className="recon-table settings-table"><thead><tr><th>Check</th><th>Scope</th><th>%</th><th>Absolute</th><th>What it means</th></tr></thead><tbody>
      {TOLERANCE_CHECKS.map((check) => {
        const value = tolerances[check.key];
        const initial = defaults?.tolerances[check.key];
        return <tr key={check.key}><td>{check.title}</td><td>Global</td>
          <td><input aria-label={`${check.title} percentage tolerance`} inputMode="decimal" value={value.pct} onChange={(event) => setValue(check.key, "pct", event.currentTarget.value)} /><DefaultNote label={`${check.title} percentage`} current={value.pct} initial={initial?.pct} onReset={() => setValue(check.key, "pct", initial?.pct ?? "0")} /><FieldError errors={errors} path={`engine.tolerances.${check.key}.pct`} /></td>
          <td><input aria-label={`${check.title} absolute tolerance`} inputMode="decimal" value={value.abs} onChange={(event) => setValue(check.key, "abs", event.currentTarget.value)} /> <span className="settings-unit">{check.absUnit}</span><DefaultNote label={`${check.title} absolute`} current={value.abs} initial={initial?.abs} onReset={() => setValue(check.key, "abs", initial?.abs ?? "0")} /><FieldError errors={errors} path={`engine.tolerances.${check.key}.abs`} /></td>
          <td className="settings-example">{check.example(value.pct, value.abs, mode)}</td></tr>;
      })}
      {rows.map((row) => {
        const info = TOLERANCE_CHECKS.find((check) => check.key === row.check);
        const base = `engine.overrides.${row.kind === "vendor" ? "vendors" : "items"}.${row.index}.tolerances.${row.check}`;
        return <tr key={row.id} className="settings-override-row"><td>{info?.title ?? row.check}</td><td>{row.scope}</td>
          <td><input aria-label={`${row.scope} ${info?.title ?? row.check} percentage`} inputMode="decimal" value={row.values.pct} onChange={(event) => setOverride(row.kind, row.index, row.check, "pct", event.currentTarget.value)} /><FieldError errors={errors} path={`${base}.pct`} /></td>
          <td><input aria-label={`${row.scope} ${info?.title ?? row.check} absolute`} inputMode="decimal" value={row.values.abs} onChange={(event) => setOverride(row.kind, row.index, row.check, "abs", event.currentTarget.value)} /> <span className="settings-unit">{info?.absUnit}</span><FieldError errors={errors} path={`${base}.abs`} /></td>
          <td><button className="button button-subtle" type="button" aria-label={`Remove override ${row.scope} ${info?.title ?? row.check}`} onClick={() => removeOverride(row.kind, row.index, row.check)}>Remove</button></td></tr>;
      })}
    </tbody></table></div>
    <AddOverrideForm engine={engine} vendors={vendors} update={update} />
  </Section>;
}

function AddOverrideForm({ engine, vendors, update }: Pick<SectionProps, "engine" | "update"> & { vendors: MappingVendor[] }) {
  const [scope, setScope] = useState<"vendor" | "item">("vendor");
  const [vendorId, setVendorId] = useState("");
  const [itemCode, setItemCode] = useState("");
  const [check, setCheck] = useState<ToleranceCheck>("unit_rate");
  const [problem, setProblem] = useState<string | null>(null);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const code = itemCode.trim();
    if (scope === "vendor" && !vendorId) return setProblem("Choose the vendor for this override.");
    if (scope === "item" && !code) return setProblem("Enter the item code for this override.");
    const existing = scope === "vendor"
      ? engine.overrides.vendors.find((row) => row.vendor_id === vendorId)
      : engine.overrides.items.find((row) => row.item_code.trim().toLowerCase() === code.toLowerCase() && (row.vendor_id ?? "") === vendorId);
    if (existing?.tolerances[check]) return setProblem("That scope already overrides this check; edit the row above.");
    setProblem(null);
    const start: ToleranceValues = { ...engine.tolerances[check] };
    update((next) => {
      if (scope === "vendor") {
        const row = next.overrides.vendors.find((item) => item.vendor_id === vendorId);
        if (row) row.tolerances[check] = start;
        else next.overrides.vendors.push({ vendor_id: vendorId, tolerances: { [check]: start } });
      } else {
        const row = next.overrides.items.find((item) => item.item_code.trim().toLowerCase() === code.toLowerCase() && (item.vendor_id ?? "") === vendorId);
        if (row) row.tolerances[check] = start;
        else next.overrides.items.push({ item_code: code, vendor_id: vendorId || null, tolerances: { [check]: start } });
      }
    });
    setItemCode("");
  };
  return <form className="settings-inline-form" onSubmit={submit} aria-label="Add tolerance override">
    <label><span>Scope</span><select aria-label="Override scope" value={scope} onChange={(event) => setScope(event.currentTarget.value as "vendor" | "item")}><option value="vendor">Vendor</option><option value="item">Item</option></select></label>
    {scope === "item" ? <label><span>Item code</span><input aria-label="Override item code" value={itemCode} onChange={(event) => setItemCode(event.currentTarget.value)} maxLength={120} /></label> : null}
    <label><span>{scope === "item" ? "Vendor (optional)" : "Vendor"}</span><select aria-label="Override vendor" value={vendorId} onChange={(event) => setVendorId(event.currentTarget.value)}><option value="">{scope === "item" ? "All vendors" : "Choose vendor…"}</option>{vendors.map((vendor) => <option key={vendor.id} value={vendor.id ?? ""}>{vendor.canonical_name}</option>)}</select></label>
    <label><span>Check</span><select aria-label="Override check" value={check} onChange={(event) => setCheck(event.currentTarget.value as ToleranceCheck)}>{TOLERANCE_CHECKS.map((item) => <option key={item.key} value={item.key}>{item.title}</option>)}</select></label>
    <button className="button" type="submit"><Plus size={14} /> Add override</button>
    {problem ? <small className="settings-field-error" role="alert">{problem}</small> : null}
  </form>;
}

export function PoliciesSection({ engine, defaults, errors, update }: SectionProps) {
  const policies = engine.policies;
  const initial = defaults?.policies;
  return <Section kicker="MONEY POLICIES" title="Policies and thresholds" description="These change payable and status figures. Each new value applies to cases after the next run.">
    <div className="settings-policy-grid">
      <label className="settings-field"><span>Limit mode</span><select aria-label="Limit mode" value={engine.tolerances.limit_mode} onChange={(event) => { const value = event.currentTarget.value as "or" | "both"; update((next) => { next.tolerances.limit_mode = value; }); }}><option value="or">OR — pass if either limit holds (looser)</option><option value="both">Both — pass only if both limits hold (stricter)</option></select>
        <small>Example: PO rate ₹48,000 with ±0.5% or ±₹1. <strong>OR</strong> allows up to ₹240 difference; <strong>Both</strong> allows only ₹1.</small><DefaultNote label="limit mode" current={engine.tolerances.limit_mode} initial={defaults?.tolerances.limit_mode} onReset={() => update((next) => { next.tolerances.limit_mode = defaults?.tolerances.limit_mode ?? "or"; })} /></label>
      <label className="settings-field"><span>Payable when the rate is over tolerance</span><select aria-label="Payable rate mode" value={policies.payable_rate_mode} onChange={(event) => { const value = event.currentTarget.value as "pay_at_po_rate" | "hold"; update((next) => { next.policies.payable_rate_mode = value; }); }}><option value="pay_at_po_rate">Pay at PO rate</option><option value="hold">Hold</option></select>
        <small>Example: invoice rate ₹49,000, PO rate ₹48,000. <strong>Hold</strong>: payable is held until the vendor corrects it. <strong>Pay at PO rate</strong>: payable uses ₹48,000.</small><DefaultNote label="payable rate mode" current={policies.payable_rate_mode} initial={initial?.payable_rate_mode} onReset={() => update((next) => { next.policies.payable_rate_mode = initial?.payable_rate_mode ?? "pay_at_po_rate"; })} /></label>
      <label className="settings-field"><span>Invoice dated before the PO</span><select aria-label="Date order level" value={policies.date_order_level} onChange={(event) => { const value = event.currentTarget.value as "fail" | "warn"; update((next) => { next.policies.date_order_level = value; }); }}><option value="fail">Fail the case</option><option value="warn">Warn only</option></select><DefaultNote label="date order level" current={policies.date_order_level} initial={initial?.date_order_level} onReset={() => update((next) => { next.policies.date_order_level = initial?.date_order_level ?? "fail"; })} /></label>
      <label className="settings-field"><span>Pending ageing (days)</span><input aria-label="Pending ageing days" inputMode="numeric" value={String(policies.pending_ageing_days)} onChange={(event) => { const raw = event.currentTarget.value.trim(); update((next) => { next.policies.pending_ageing_days = /^\d+$/.test(raw) ? Number(raw) : (raw as unknown as number); }); }} /><small>A GRN-pending case older than this is flagged as aged.</small><DefaultNote label="pending ageing days" current={String(policies.pending_ageing_days)} initial={initial === undefined ? undefined : String(initial.pending_ageing_days)} onReset={() => update((next) => { next.policies.pending_ageing_days = initial?.pending_ageing_days ?? 30; })} /><FieldError errors={errors} path="engine.policies.pending_ageing_days" /></label>
      {THRESHOLDS.map(([key, title, help]) => <label className="settings-field" key={key}><span>{title}</span><input aria-label={`${title} threshold`} inputMode="decimal" value={engine.thresholds[key]} onChange={(event) => { const value = event.currentTarget.value; update((next) => { next.thresholds[key] = value; }); }} /><small>{help}</small><DefaultNote label={`${title} threshold`} current={engine.thresholds[key]} initial={defaults?.thresholds[key]} onReset={() => update((next) => { next.thresholds[key] = defaults?.thresholds[key] ?? next.thresholds[key]; })} /><FieldError errors={errors} path={`engine.thresholds.${key}`} /></label>)}
    </div>
  </Section>;
}

function TagEditor({ label, tags, onChange }: { label: string; tags: string[]; onChange: (tags: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const value = draft.trim();
    if (value && !tags.some((tag) => tag.toLowerCase() === value.toLowerCase())) onChange([...tags, value]);
    setDraft("");
  };
  return <div className="settings-tags" role="group" aria-label={`${label} synonyms`}>
    <span className="settings-tags-label">{label}</span>
    <ul>{tags.map((tag) => <li key={tag}>{tag}<button type="button" aria-label={`Remove ${label} synonym ${tag}`} onClick={() => onChange(tags.filter((item) => item !== tag))}><X size={11} /></button></li>)}</ul>
    <input aria-label={`Add ${label} synonym`} placeholder="Add label, press Enter" value={draft} onChange={(event) => setDraft(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); add(); } }} onBlur={add} maxLength={80} />
  </div>;
}

export function SynonymsSection({ engine, update }: SectionProps) {
  return <Section kicker="EXTRACTION" title="Label synonyms" description="Printed labels the parsers look for, per field. They add to the built-in labels; removing one here does not remove a built-in label.">
    <div className="settings-tag-grid">{SYNONYM_GROUPS.map(([key, label]) => <TagEditor key={key} label={label} tags={engine.synonyms[key]} onChange={(tags) => update((next) => { next.synonyms[key] = tags; })} />)}</div>
  </Section>;
}

export function UomSection({ engine, errors, update }: SectionProps) {
  const [raw, setRaw] = useState("");
  const [canonical, setCanonical] = useState("");
  const aliases = Object.entries(engine.uom.aliases).sort(([left], [right]) => left.localeCompare(right));
  const addAlias = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const from = raw.trim().toUpperCase();
    const to = canonical.trim().toUpperCase();
    if (!from || !to) return;
    update((next) => { next.uom.aliases[from] = to; });
    setRaw("");
    setCanonical("");
  };
  return <Section kicker="UNITS" title="UOM map" description="Printed units map to a canonical unit. Conversions apply only to the item code given, for example BOX → NOS × 12.">
    <div className="settings-two-col">
      <div><h3>Aliases</h3><table className="recon-table settings-table"><thead><tr><th>Printed unit</th><th>Canonical unit</th><th /></tr></thead><tbody>{aliases.map(([from, to]) => <tr key={from}><td>{from}</td><td><input aria-label={`Canonical unit for ${from}`} value={to} onChange={(event) => { const value = event.currentTarget.value.toUpperCase(); update((next) => { next.uom.aliases[from] = value; }); }} /><FieldError errors={errors} path={`engine.uom.aliases.${from}`} /></td><td><button className="button button-subtle" type="button" aria-label={`Remove unit alias ${from}`} onClick={() => update((next) => { delete next.uom.aliases[from]; })}>Remove</button></td></tr>)}</tbody></table>
        <form className="settings-inline-form" onSubmit={addAlias} aria-label="Add unit alias"><label><span>Printed unit</span><input aria-label="New printed unit" value={raw} onChange={(event) => setRaw(event.currentTarget.value)} maxLength={20} /></label><label><span>Canonical unit</span><input aria-label="New canonical unit" value={canonical} onChange={(event) => setCanonical(event.currentTarget.value)} maxLength={20} /></label><button className="button" type="submit" disabled={!raw.trim() || !canonical.trim()}><Plus size={14} /> Add</button></form></div>
      <div><h3>Item conversions</h3><table className="recon-table settings-table"><thead><tr><th>Item code</th><th>From</th><th>To</th><th>Factor</th><th /></tr></thead><tbody>{engine.uom.conversions.map((row, index) => {
        const set = (key: "item_code" | "from" | "to" | "factor", value: string) => update((next) => { const target = next.uom.conversions[index]; if (target) target[key] = value; });
        const base = `engine.uom.conversions.${index}`;
        return <tr key={index}><td><input aria-label={`Conversion ${index + 1} item code`} value={row.item_code} onChange={(event) => set("item_code", event.currentTarget.value)} /><FieldError errors={errors} path={`${base}.item_code`} /></td><td><input aria-label={`Conversion ${index + 1} from unit`} value={row.from} onChange={(event) => set("from", event.currentTarget.value.toUpperCase())} /><FieldError errors={errors} path={`${base}.from`} /></td><td><input aria-label={`Conversion ${index + 1} to unit`} value={row.to} onChange={(event) => set("to", event.currentTarget.value.toUpperCase())} /><FieldError errors={errors} path={`${base}.to`} /></td><td><input aria-label={`Conversion ${index + 1} factor`} inputMode="decimal" value={row.factor} onChange={(event) => set("factor", event.currentTarget.value)} /><FieldError errors={errors} path={`${base}.factor`} /></td><td><button className="button button-subtle" type="button" aria-label={`Remove conversion ${index + 1}`} onClick={() => update((next) => { next.uom.conversions.splice(index, 1); })}>Remove</button></td></tr>;
      })}</tbody></table><button className="button" type="button" onClick={() => update((next) => { next.uom.conversions.push({ item_code: "", from: "BOX", to: "NOS", factor: "1" }); })}><Plus size={14} /> Add conversion</button></div>
    </div>
  </Section>;
}

export function BuyerEntitiesSection({ engine, errors, update }: SectionProps) {
  return <Section id="buyer-entities" kicker="TAX" title="Buyer entities" description="Your own GSTINs. Used by the buyer-entity check, to tell your GSTIN apart from the supplier's on scanned documents, and to decide intra- or inter-state tax. While the list is empty, every supplier GSTIN read from a PDF needs review.">
    <table className="recon-table settings-table"><thead><tr><th>Entity name</th><th>GSTIN</th><th /></tr></thead><tbody>{engine.buyer_entities.map((entity, index) => {
      const live = entity.gstin ? gstinProblem(entity.gstin) : null;
      return <tr key={index}><td><input aria-label={`Buyer entity ${index + 1} name`} value={entity.name} onChange={(event) => { const value = event.currentTarget.value; update((next) => { const target = next.buyer_entities[index]; if (target) target.name = value; }); }} /><FieldError errors={errors} path={`engine.buyer_entities.${index}.name`} /></td>
        <td><input className="settings-gstin" aria-label={`Buyer entity ${index + 1} GSTIN`} aria-invalid={Boolean(live)} value={entity.gstin} maxLength={15} onChange={(event) => { const value = event.currentTarget.value.toUpperCase().replace(/\s+/g, ""); update((next) => { const target = next.buyer_entities[index]; if (target) target.gstin = value; }); }} />{live || errors[`engine.buyer_entities.${index}.gstin`] ? <small className="settings-field-error" role="alert">{live ?? errors[`engine.buyer_entities.${index}.gstin`]}</small> : entity.gstin ? <small className="settings-valid">Valid GSTIN · state {entity.gstin.slice(0, 2)}</small> : null}</td>
        <td><button className="button button-subtle" type="button" aria-label={`Remove buyer entity ${index + 1}`} onClick={() => update((next) => { next.buyer_entities.splice(index, 1); })}>Remove</button></td></tr>;
    })}</tbody></table>
    <button className="button" type="button" onClick={() => update((next) => { next.buyer_entities.push({ name: "", gstin: "" }); })}><Plus size={14} /> Add buyer entity</button>
  </Section>;
}

export function GstSlabsSection({ engine, errors, update }: SectionProps) {
  return <Section kicker="TAX" title="GST slabs" description="Allowed GST rates, chosen by invoice date. Leave dates empty for a slab with no boundary.">
    <table className="recon-table settings-table"><thead><tr><th>Rate %</th><th>From</th><th>To</th><th /></tr></thead><tbody>{engine.gst_slabs.map((slab, index) => {
      const base = `engine.gst_slabs.${index}`;
      const set = (key: "rate" | "from" | "to", value: string) => update((next) => { const target = next.gst_slabs[index]; if (!target) return; if (key === "rate") target.rate = value; else target[key] = value || null; });
      return <tr key={index}><td><input aria-label={`GST slab ${index + 1} rate`} inputMode="decimal" value={slab.rate} onChange={(event) => set("rate", event.currentTarget.value)} /><FieldError errors={errors} path={`${base}.rate`} /></td><td><input type="date" aria-label={`GST slab ${index + 1} from`} value={slab.from ?? ""} onChange={(event) => set("from", event.currentTarget.value)} /></td><td><input type="date" aria-label={`GST slab ${index + 1} to`} value={slab.to ?? ""} onChange={(event) => set("to", event.currentTarget.value)} /><FieldError errors={errors} path={`${base}.to`} /></td><td><button className="button button-subtle" type="button" aria-label={`Remove GST slab ${slab.rate}%`} onClick={() => update((next) => { next.gst_slabs.splice(index, 1); })}>Remove</button></td></tr>;
    })}</tbody></table>
    <button className="button" type="button" onClick={() => update((next) => { next.gst_slabs.push({ rate: "", from: null, to: null }); })}><Plus size={14} /> Add slab</button>
  </Section>;
}

export function PaymentTermsSection({ engine, errors, update }: SectionProps) {
  return <Section kicker="FINANCE" title="Payment-terms defaults" description="Reference list of standard terms. The payment due date is currently read from the terms printed on the PO; this list is not yet applied to it.">
    <table className="recon-table settings-table"><thead><tr><th>Label</th><th>Days</th><th /></tr></thead><tbody>{engine.payment_terms.map((term, index) => <tr key={index}><td><input aria-label={`Payment term ${index + 1} label`} value={term.label} onChange={(event) => { const value = event.currentTarget.value; update((next) => { const target = next.payment_terms[index]; if (target) target.label = value; }); }} /><FieldError errors={errors} path={`engine.payment_terms.${index}.label`} /></td><td><input aria-label={`Payment term ${index + 1} days`} inputMode="numeric" value={String(term.days)} onChange={(event) => { const raw = event.currentTarget.value.trim(); update((next) => { const target = next.payment_terms[index]; if (target) target.days = /^\d+$/.test(raw) ? Number(raw) : (raw as unknown as number); }); }} /><FieldError errors={errors} path={`engine.payment_terms.${index}.days`} /></td><td><button className="button button-subtle" type="button" aria-label={`Remove payment term ${term.label || index + 1}`} onClick={() => update((next) => { next.payment_terms.splice(index, 1); })}>Remove</button></td></tr>)}</tbody></table>
    <button className="button" type="button" onClick={() => update((next) => { next.payment_terms.push({ label: "", days: 30 }); })}><Plus size={14} /> Add term</button>
  </Section>;
}
