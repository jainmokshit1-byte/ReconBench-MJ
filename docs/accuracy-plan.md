# Accuracy plan: extraction → mapping → reconciliation (Qwen-first)

This is the single authoritative accuracy plan. The other authoritative plan is [`ReconBench_Implementation_Plan.md`](ReconBench_Implementation_Plan.md), which is read-only and is never copied here; this file only points to it. Real sample values and ground truth are not kept in the repo; they live in the owner's local `work/` folder (git-ignored).

**Reading conventions.** Every number cites a report file in `work/accuracy-reports/` and a decision-log ID (`Dnn`, [`decision-log.md`](decision-log.md)). **verified** = read from code, a stored report or the live stack on the stated date. **documented** = stated by an earlier decision or doc and not re-checked. **unverified** = not checked; the check that would verify it is named. Counts are always shown with their denominators, and rates carry the sample size because the truth set is small (§3.4).

---

## 0. Status

| | |
|---|---|
| Date / last decision | 22 Oct 2026 / **D98** (D93–D98: the owner's queue run of 21–22 Oct; D78 was the consolidation; engineering since: D82 WI-PV1, D85 WI-PV2; D83/D84 plan and owner decisions; D86 cards WI-D4f and WI-IMP1 and the approvals for the August dumps; D87 S06 truth correction and Tier S pool links; D88 match rule; D89 WI-D4f results, detour closed; D90 chosen order, no self-support, WI-PERF1 targets; D91 WI-PV3 done, WI-Q2 design; D92 WI-PV3b recorded, WI-Q2 built and measured) |
| Main-plan anchors | [Task 4.9](ReconBench_Implementation_Plan.md) (PDF extractor; gate ≥ 97% critical-field accuracy, no field < 95%; "blocked until real PDFs exist"), **Task 4.9b** (scanned-invoice OCR, G-30: it is **not in the main plan**; see [`gates/phase-4.md`](gates/phase-4.md) "Task 4.9b" and decision-log G-30), [Task 4.17](ReconBench_Implementation_Plan.md) (AI assist), and the [Phase 4 exit gate](ReconBench_Implementation_Plan.md) (TRD M3). G-07 is the register row for real samples, not the exit gate itself. |
| Branch / tree | `phase2-hosted-ci`; 166 uncommitted entries at the end of D85 (97 modified, 69 untracked; 159 at the start of that session); the patches `work/patches/wi-pv1.patch` and `wi-pv2.patch` isolate the WI-PV1 and WI-PV2 hunks; other sessions share the tree, containers and database |
| Consolidation | done 20 Oct (D79): snapshot commit tagged `pre-plan-merge-20261020`, then the five retired documents removed (§14) |
| Change of requirement | 20 Oct (D80): both providers supported, connected from the frontend, selected by users; Gemini is not turned off (§4.15) |
| Session rules (D83, 20 Oct) | **Gemini runs on the free tier** (`gemini-3.5-flash-lite`, no billing): cost is ₹0, the O-24 cap is dormant, **rate limits and data use are the constraints** (§4.15.9, WI-PV7, O-25). **Unit of evidence = unique document** (§2.5, §3.2a, O-26). **No deploy before WI-PV5** (AC-INT1b). |

**Where we are.** The rules + OCR path, QR, page split, vendor hygiene, vision reader, consensus (D42), verify screen, identity and matching v2 are built (AC-0 … AC-9a, D32–D58). The provider layer, the Qwen reader, settings, shadow store and the evaluation harness are built **in the working tree only** (D62–D67; offline suites 1090 passed per D77, not re-run for this plan). Qwen3.5-9B on the company vLLM server has been benchmarked offline in six server reads (D68–D77). With prompt `vision.prompt.v4.qwen` and the D77 cross-page conflict rule it reads **275/313 headers (87.9%)** on the 39 dev PDFs, against **242/313 (77.3%)** for Tesseract + rules on the same documents (`ev2-e5-v4-ctl.json`, repeat 0 = the 16 Oct read, D74 answers rescored under D77). Lines are weaker: amount 90/123, quantity 79/114, rate 85/118, description 47/108 (40 wrong), HSN 46/75. **No accuracy target is met.** All hard-zero metrics are 0 in every stored report, and the simulated critical false-accept count is 0.

**Qwen is not live.** The running containers (images built 16 Oct 04:41) contain no D62–D77 code (no `openai_compat.py`, no `endpoint_policy.py`, migrations end at 0024, container `RULES_HASH` `43b153f7…` vs tree `1e9af32f…`); the live database is at `0024` while the tree needs `0027`; `compose.yaml` forwards no `RB_AI_VLLM_*` variable. The live app runs Gemini `ai_first` with page images on (`gemini-3.5-flash-lite`), suggestions only: 40 documents, 29 `h6_vision` requests (26 applied, 2 discarded invalid, 1 failed; 7–9 Oct), 17 H1 requests, **0 `consensus_run` rows**, 0 document versions created from an AI reader (live DB read-only check, 20 Oct). Qwen output never reaches a document value today, and Excel export on real data has never been measured.

**Requirement change (D80, 20 Oct).** The product supports **both** LLM providers: Qwen (internal vLLM, OpenAI-compatible) and Gemini (Google). An admin connects a provider from Settings → AI providers with credentials stored per workspace; a provider becomes selectable only after a successful server-side connection test. The workspace picks a default provider for extraction and one for AI assist (H1–H4 and matching/reconciliation assist); an upload can override the extraction provider; a document can be re-read with the other provider as a new reading that never overwrites the current one. **Gemini stays on**: it remains the live default while Qwen runs in shadow (AC-LIVE-S), and the default after the merge stage (AC-LIVE-M) is an owner decision taken from side-by-side measured numbers. There is no silent failover and no voting between providers. The safety contract (validators, OCR agreement, narrowed O-10, DP-L1 thresholds, circuit breaker, hard zeros) is measured and enabled per provider and per field type; a provider without measured numbers gives suggestions only. Existing provider, settings, consent, allowlist and key-sealing code (D64–D66) is reused, not duplicated (§4.15).

**Owner decisions recorded 20 Oct (O-1 … O-17, DP-L1, §9):** two-tier truth set; auto-accept only through agreement or a narrowly defined deterministic validator (GSTIN matched to the vendor master; a fully closed amount chain) plus the DP-L1 lower-bound gate (option A), a shadow period and a circuit breaker; Gemini stays connectable and selectable (D80, replaces "optional"); D72 lifted for WI-V2 only; version rule through `CONSENSUS_VERSION`; provider down → rules + OCR with a visible flag, no silent failover (O-6, rewritten in D80); live rebuild only inside AC-INT1 with a tested backup.

**Stage 4 progress (D82, 20 Oct):** **WI-PV1 is done and awaiting owner review** (schema, backfill, tests on disposable databases only; the live database and containers untouched). The WI-D5 labelling protocol, label-sheet template, agreement tool and truth tally are prepared. The truth tally shows 10 truth invoices, not about 18 (§3.4).

**Stage 4 progress (D85, 20 Oct):** **WI-PV2 is done and awaiting owner review** (migration `0029`: the Gemini key moves to its card, `key_tier`, rotation candidate; connect, rotate, disconnect and health through one set of endpoints for both providers; disposable databases only, no model call, the live stack untouched). Next: WI-PV3 (admin enforcement, O-18), then WI-PV4 … PV8. Until WI-PV3 lands, every logged-in user can use the new endpoints (D84).

**Truth and linking data (D86, D87, 21 Oct):** the owner added full August 2026 Tally dumps (251 POs, 250 GRNs) and 15 invoice PDFs; the PDFs are byte-identical to files already in the truth set (S01 to S08 and their 7 re-scans), so **no new unique invoice**. WI-D4f (full-pool linking arm, dev tool) and WI-IMP1 (native Tally import, product task before AC-INT1) are written as cards (§7.7, §7.2): WI-D4f is approved but not built, WI-IMP1 is not approved to build. `truth.json` now holds the owner-confirmed Tier S pool links (invoice to PO 6, invoice to GRN 7, GRN to PO 6, plus one GRN citing an external July PO), 8 complete sets with the pool, 16 typed handwritten references on 8 invoices, and the S06 correction (PO 08-087 to 08-081). No code was changed and no model was called.

**WI-D4f detour closed (D89, 21 Oct):** the full-pool linking arm ran on a disposable database (two harness versions, two arms, plus a harness-only sign-normalised diagnostic); results are in WI-D4f and §3.4, and the follow-up cards WI-R1, WI-R2, WI-PERF1, the WI-BE7a guard MG-11, the WI-Q2 output-type fix and the AC-8b evidence are written. **Work returns to the plan's order.** Owner-proposed next order (21 Oct): WI-IMP1 sign normalisation, then WI-Q2 and WI-BE8 in parallel, then WI-PV3, then WI-R1, WI-R2, WI-PERF1 and WI-BE7. Engineer's recommendation in D89 (differs; the owner chooses): WI-PV3 alongside WI-Q2 (different files), then WI-D4, WI-PERF1, WI-BE8, WI-IMP1 (with sign normalisation), then WI-BE7a with MG-11, WI-R1 and WI-R2.

**Chosen order (owner, D90, 21 Oct; replaces the ready queue below where they differ):** **WI-PV3 ∥ WI-Q2** (WI-Q2: design first, no model call) → **WI-D4** (with the no-self-support rule) → **WI-PERF1** → **WI-BE8** → **WI-IMP1** (sign normalisation as data correctness, before AC-INT1) → **WI-BE7a** with MG-11 → **WI-R1**, **WI-R2**. WI-PV3 is authorised now (disposable databases only, no commit).

**Stage 4 progress (D91, 21 Oct):** **WI-PV3 is done and awaiting owner review** (migration `0030` last-manager lock; `ai.manage` from the stored role on every AI-settings and provider write; denial audit rows; disposable databases only, no model call, live stack untouched; `work/patches/wi-pv3.patch`). The **WI-Q2 design** is written in its card (no model call; request counts per provider listed for the owner). Next in the chosen order: WI-Q2 step Q2.0 (offline, stored answers) on the owner's go, then WI-D4.

**Stage 4 progress (D92, 21 Oct):** WI-PV3 accepted by the owner; **WI-PV3b** (role assignment command) recorded, not built, placed before AC-INT1b. **WI-Q2 built** (typed handwritten references with logged normalisation, header-band crop, type-aware matching, prompt variants `v4.q2` with schema v5; engine 0.1.8) and measured on the dev set with Qwen only (Gemini skipped: the bench key was not set). Handwritten references typed correctly on unique documents rose from 3/16 (stored v4 answers) to **12/16** with the v4 full-page read plus the band read; **the `v4.q2` full-page prompt is not adopted**: against a same-day v4 control it lost 12 header and 14 rate values and accepted 3 wrong values. In the v2 pool no invoice link was unlocked by handwriting (1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false): the matcher needs amount corroboration through aligned lines, which the generic Tally item names do not give (WI-D4). Next in the chosen order: **WI-D4** (no self-support; handwritten-reference corroboration), then WI-PERF1, then WI-BE8.

**Queue run (owner, 21–22 Oct; replaces the chosen order of D90 for this run):** (1) adopt the WI-Q2 shape, (2) offline line-alignment check, (3) WI-IMP1 parser part, (4) WI-D4, (5) Gemini side by side, (6) **WI-BE8 before WI-PERF1** (33 of 35 header misses are missing values), (7) WI-PERF1. Pre-approved model budgets for the run: Qwen 300 requests, Gemini 150 (`gemini-3.5-flash-lite`, free bench key). Disposable databases only, no commit, no live change.

**Queue run, task 1 (D93, 22 Oct): the WI-Q2 shape is adopted in code.** The Qwen reader (`vision_openai_compat`) now reads full pages with `vision.prompt.v4.qwen` and, for invoices, one more call on the page-1 header band with `vision.prompt.v4.q2.qwen`; the band answer adds annotations only (boxes moved back to page 1, a duplicate note not added twice, a broken band answer ignored and logged), never a field or a line. `reader_params` gains a `band` entry for that reader only (O-5: its cache key changes; Gemini's and every bench arm's keys are unchanged). The full-page `v4.q2` prompt stays a bench variant (§12). No live change: the running containers do not carry this code. The Gemini bench key file was empty, so every Gemini run of this queue is skipped.

**Queue run, task 2 (D94, 22 Oct; offline, no model call): line descriptions.** The XML parser takes a Tally line's description from `STOCKITEMNAME` (the synonym `STOCK ITEM NAME`); `BASICUSERDESCRIPTION` is not read. Against the 29 truth invoice lines of S02–S08 (truth values, app scorer, aligned ≥ 0.80 / reviewable ≥ 0.60): by `STOCKITEMNAME` **0** aligned (4 reviewable) with the PO and **0** (4) with the GRN; by `BASICUSERDESCRIPTION` **16/19** (18) with the PO and **16/29** (23) with the GRN; by quantity + rate + amount alone 14/19 and 15/29 (S05 and S08 carry freight in the line amount, WI-R2). S08's 10 lines have no PO in the pool (July). Tool `work/tools/t2_align.py`, counts in `work/accuracy-intake/t2/align.json`.

**Queue run, task 3 (D95, 22 Oct): WI-IMP1 parser part built.** A native Tally "Import Data" export now parses whole (251 POs, 250 GRNs from the August dumps, no split, no layout hint): `&#4;` markers and control characters are removed only after a strict parse fails and logged per voucher (info, or warning when the tag is read for a field in its scope); masters are ignored; the line description is `BASICUSERDESCRIPTION` (the stock item name kept in `extras.stock_item_name`); purchase-side debit amounts are stored as magnitudes and logged with their original text; PO/GRN taxable, CGST/SGST/IGST, round-off and grand totals come from the party and tax ledgers when they close within ₹1 (501/501 do); a Receipt Note is a GRN whose `REFERENCE` and line `ORDERNO` are `po_refs` (a value without a digit, Tally's `Not Applicable`, is never a reference); **1** GRN names two POs and is flagged (D86 counted 6: five were `Not Applicable` markers); the line GST rate is the full rate. Two reconciliation defects that the new data reached were fixed: the snapshot now reads only PO edges (an invoice-to-GRN link crashed it) and a blocked case (duplicate or unlinked) carries no at-risk amount (schema check). Dev bench re-score identical to D87 (439/439 counts; Qwen 275/313, OCR 242/313). **v2 pool (native import): arm B (Qwen v4 + band) invoice links 10/13 (0 auto, 10 suggested; was 1/13), arm A (rules + OCR) 4/13 (0 auto, 4 suggested); false links 0; GRN→PO 217 auto + 9 suggested (was 191 + 35), all agree with `REFERENCE`; truth GRN→PO 5 auto + 1 suggested; re-scans 5/7.**

**Queue run, task 4 (D96, 22 Oct): WI-D4 built (no self-support; header-level handwritten references).** Receipt support for a check comes only from other sources' auto or confirmed links and opening balances, never from the document's own link; a GRN's own lines against the PO's lines count as independent evidence. A typed handwritten reference gives a **suggestion only** when the type matches, the vendor GSTIN matches, the invoice total equals the PO or GRN total within ₹1 (a PO also through a GRN that cites it) and the number is unique for that vendor. `ENGINE_VERSION` 0.1.9. **v2 pool: arm B 11/13 (0 auto, 11 suggested), arm A 4/13 (0 auto, 4 suggested), false links 0; truth GRN→PO 6/6 auto; 216 of the 217 self-supported auto GRN→PO links stay auto (1 becomes a suggestion, 1 correct GRN becomes auto: 217 auto, all agree with `REFERENCE`); mapping converges in 2 passes (694 s, arm A).** Dev bench links, hard zeros and reconciliation unchanged.

**Queue run, task 5 (D97): skipped.** The Gemini bench key file was empty (0 bytes), so no Gemini run was made (0 requests); WI-EV2 arms E1/E2 stay open.

**Queue run, task 6 (D97, 22 Oct): WI-BE8 cropped two-pass reading measured and rejected (bench variant kept).** Order change recorded: WI-BE8 ran before WI-PERF1 (33 of 35 header misses are missing values). Pass 1 = the same-day v4 full-page read of 21 Oct (Q2.1c answers, 0 requests); pass 2 = three overlapping crops of each flagged page (34 pages: 5 lost to the cap, 29 with open totals; 78 + 6 retried = 84 requests). Against pass 1 alone (unique documents, strict rule): headers **99/137 vs 101/136**, header amounts 24 vs 27/46, line amount 28/47, quantity 24/44, rate 26/45 (unchanged; with copies each +8), references 4 vs 3/6, **accepted-wrong 1 vs 0**, documents lost to the cap **0 vs 1**. Fails the guard (fields fell, accepted-wrong not 0): **not adopted**; the app reader stays v4 full pages + band.

**Queue run, task 7 (D98, 22 Oct): WI-PERF1 targets met on the D86 pool.** Profile: 99 % of a mapping pass was reverse matching re-canonicalising every PO number (about 98 million calls); the ledger drain reloaded the whole workspace for each PO. Fixes: a per-call index of canonical PO numbers and memoised pure normalisers in `mapping/links.py`; one snapshot of the current documents per ledger drain batch in `ledger_service.py`. **One mapping pass 26 s (was 401 s), ledger rebuild 5 s (was 442 s), full worker sequence (mapping, ledger, mapping) about 49 s against the 5 min target; links (595 and 589) and ledger rows (455) identical to the task 4 run in both arms.**

**Queue run closed (D98).** Invoice links in the v2 pool: 1/13 → **11/13, all suggestions** (Qwen v4 + band readings) and 4/13 (rules + OCR); false links 0 in every run; truth GRN→PO 6/6 auto. PH2 gate on the same-day Qwen v4 read (unique documents): headers 101/136, line amount 28/47, quantity 24/44, rate 26/45, one document lost to the cap, accepted-wrong 0: **not met**. Qwen requests 84 of the 300 approved; Gemini 0. Next in order (owner decides): WI-BE7a with MG-11, WI-R1, WI-R2; the Gemini runs need a bench key file with the key in it.

**Stage 4 ready queue (recomputed in D80; needs separate authorisation to change code, call a server, run benches or touch deployment):**
1. **WI-PV1** provider connection model, encrypted credentials and per-provider settings (one migration, built on the existing `ai_provider_config`, key sealing and consent code), in parallel with **WI-D5** (truth set and labelling protocol).
2. **WI-BE8** cropped two-pass reading (Qwen) and the **Gemini arms E1–E4** (they need the owner's go, a Gemini key and the owner's Gemini budget), both feeding the side-by-side **WI-EV2** matrix.
3. **WI-BE7a** provider-agnostic merge core with the D77 guards (flag off). Then, still before AC-INT1: WI-PV2 … PV8, WI-BE9a, WI-EXP1, WI-FE1a/b, WI-FE3.
Cheap read-only items alongside: **WI-T1** and **WI-INT0**.

---

## 1. Scope and non-goals

**In scope.** Improve the accuracy and safety of the running application end to end: upload → classification → OCR/text → page and region prep → Qwen reading → parse → normalisation → evidence checks → validation → linking → reconciliation → review → persistence → Excel export. Two LLM providers are supported and user-selectable: Qwen3.5-9B (company vLLM) and Gemini (Google). Tesseract + rules stay as the independent second reader, the fallback and the not-in-OCR-text check (D72). Which provider reads a document is the workspace default, a per-upload override or a re-run; the reading's provider, model and prompt version are kept as provenance. Offline scores count only when the integration ladder (§4.11) reaches the running app.

**Non-goals.**
- Claiming 100% accuracy. The direction is zero errors on payment-affecting fields, reported honestly with sample sizes.
- Reading handwriting as a verified value (D42 rule 4); handwritten references are hints.
- Training or fine-tuning models; changing reconciliation rules or tolerances; AI deciding status, payable amount, links or PO capacity.
- New deterministic OCR/rules extraction work: WI-D1/WI-D2 stay **frozen** (D72). The only exception is WI-V2 (O-4).
- Combining answers from Gemini and Qwen, or calling both for one request (owner decision §0.6 of the retired LLM plan; D62–D67).
- Both LLMs reading the same document and voting or merging (`dual_agree` stays excluded, §0.6 of the retired LLM plan). A manual "compare both" view that shows two readings side by side without merging is an optional PH6 item.
- Automatic failover from one provider to the other. A fallback to the other provider exists only as an explicit, consented workspace setting that is off by default (O-21).
- GSTR-2B (AC-12) and the vendor profile (AC-10) are in the plan but optional/late.

---

## 2. Accuracy contract

### 2.1 Hard zeros (must stay 0 on every run; the bench exits non-zero otherwise)

| ID | Hard zero | Measured as |
|---|---|---|
| HZ-1 | False-safe document: not flagged, with a wrong critical value | bench `FALSE_SAFE` |
| HZ-2 | False auto-link: an automatic link to the wrong PO/GRN | `FALSE_AUTO_LINKS` |
| HZ-3 | False "Matched" | `FALSE_MATCHED` |
| HZ-4 | Wrong auto-created vendor (buyer GSTIN, label, unverified value) | `WRONG_VENDORS_CREATED` |
| HZ-5 | Auto-accepted wrong value: a value that left review without a person and is wrong | `critical_false_accept` (new, WI-EV3), reported **separately from misses** |
| HZ-6 | Silent overwrite of a value without provenance: every change is a new `document_version` carrying its origin; a manual correction or verified field is never replaced | integration test + audit of `document_version`/`field_evidence` (WI-BE7) |

A "0" is reported with its denominator and the one-sided 95% bound: 0 errors in N means a true rate ≤ 3/N (e.g. 0 in 141 critical values ⇒ ≤ 2.1%). The simulated stand-in `review_cleared` is a warning, not a pass: `ev2-e5-v4-ctl.json` shows 1 document (S08-INV) where every critical field was accepted while the rules' lines were wrong (D75(e)); this is MG-4 below.

### 2.2 Targets (carried; the owner may change them)

| Target | Source | Status |
|---|---|---|
| Critical-field accuracy ≥ 97% overall and no critical field < 95% | main plan Task 4.9 | unmet; per field in §3.2 |
| Header fields correct before human touch ≥ 98%; line items ≥ 95%; invoice→PO/GRN link found (linked or suggested) ≥ 95% when the PO is in the data | old accuracy contract (98/95/95) | unmet |
| Documents fully auto-verified ≥ 90% digital/QR and ≥ 60% scans; fields a person must touch ≤ 2 per document | old accuracy contract | unmet (18/51 = 35.3% auto-verified; 4.96 touches; `scanfix-truth-2.json`) |
| Zero errors on payment-affecting fields (grand total, taxable, tax amounts, line amount/qty/rate, supplier GSTIN) | owner direction | reported with n, never claimed |

**Critical fields** (bench `_CRITICAL`): invoice number, date, supplier GSTIN, grand total, printed PO refs; PO: number, date, supplier GSTIN, grand total; GRN: number, date, supplier GSTIN, PO refs. Vendor name is not critical but vendor creation is gated by GSTIN (AC-4).

A target is **claimed** only when the held-out sample size is large enough (§5.1): per field, a lower bound needs `n ≥ 73` error-free values for 95% (110 with one error), `n ≥ 189` for 98%. Here `n` counts **unique documents** (§2.5, D83): copies and re-scans of one document count once.

### 2.3 Trust levels and the D42 rule

| Level | Meaning | Verified? |
|---|---|---|
| **L0** | Digital/structured source, signed QR, or person-entered | yes |
| **L1** | `existing` (rules already verified, ≥ 0.98) or `d42` (below) | yes |
| **L2** | One reader, a never-automatic field, a failed soft validator, or no/negative crop agreement | no (prefilled suggestion) |
| **L3** | Readers disagree, or an LLM contradicts a rules-verified value | no (person picks) |
| **Rejected** | Fails a hard rule (buyer GSTIN as supplier, bad checksum, tax type vs state, invented IGST, off-page value) | never used |

**D42 (decided 8 Oct; every sub-phase implements it).** A machine-read value is verified without a person per field only if (1) two independent readers give the same normalised value (OCR/text, signed QR, XML, vision; two answers of one model never count twice), (2) every deterministic validator passes, (3) a local OCR re-read of the reader's box agrees, (4) never for handwritten/stamped marks, new vendor names, a field a person corrected, or an unconfirmed split, (5) switched on per field type only after the bench shows 0 false-safe and 0 agree-wrong-confirmed for that type, behind a workspace setting that is off by default. Two different vision LLMs are one reader kind (DP-L2: No). Consensus invariants CI-1 … CI-6 (AC-6a tests): consensus never changes a value; L0/person values untouched; policy off ⇒ same object; a vision-only value is never written; result independent of input order; a never-automatic name never gets `auto_verified`.

**O-2 additions (20 Oct) in front of D42, not instead of it.** A critical field may leave review without a person only if **all** hold: (a) *the selected provider's reading and OCR agree* **or** *a deterministic validator confirms*, where the validator-only path (no OCR agreement) is allowed **only** for (i) supplier GSTIN: the checksum passes **and** it is not a buyer entity's GSTIN **and** it equals the vendor-master GSTIN of that vendor; (ii) amounts: the **full chain** closes within rounding (Σ line amounts = taxable and taxable + tax = grand total); partial closure is agreement-only. Every other case is agreement-only. The validator-only path amends D42 rule 1 for these two cases and still needs rules 2, 3 and 5 (owner decision O-10, 20 Oct); (b) the field type passes the lower-bound gate on **held-out** data (DP-L1, §9); (c) 0 critical wrong on that data; (d) a shadow period has passed (§6, PH3); (e) D42 rules 2–5 hold. Nothing else changes D42.

### 2.4 Safety gates by field class (default action per case)

Actions: **AA** auto-accept (may reach L1 through consensus, subject to §2.3), **VA** validate-then-accept (accept only if the named validator passes, same gate), **S** suggest (prefilled, unverified, a person decides), **R** reject (never used), **U** unresolved (flagged, no value). Every `S` keeps the OCR/rules value as the document's current value unless the arbitration policy (§4.3) names the other reader as the leader for that field.

| Class (fields) | LLM alone | LLM and OCR agree | Validator confirms | Readers conflict | Missing |
|---|---|---|---|---|---|
| Critical IDs: doc no., doc date, printed PO/GRN refs | S | AA (gated) | VA only for refs matching an open same-vendor PO/GRN (V-REFEXIST); none for doc no./date | S with both values; never AA | U |
| Supplier GSTIN | S | AA (gated) | **VA** only if checksum passes **and** not the buyer entity's GSTIN **and** equals the vendor-master GSTIN for that vendor; otherwise agreement-only | R if a validator fails, else S | U |
| Vendor name | S | S (never automatic, D42 rule 4) | — | S | U |
| Amounts/totals (taxable, tax, grand) | S | AA (gated) | **VA** only if the full chain closes within rounding: Σ lines = taxable **and** taxable + tax = grand; partial closure is agreement-only | S, never AA | U |
| Line amount / quantity / rate | S | AA for `line_amount` (D42 list), S for qty/rate until gated | VA only through the full amount chain above; qty × rate ≈ amount alone is agreement-only; Σ lines = taxable is required before review is cleared (MG-4) | S | U |
| **Line description** | **S** | **S** | — | S | U |
| HSN/SAC | S | S until DP-L1 data exist | VA after WI-V1 (4/6/8-digit shape) | S | U |
| Handwritten / stamped marks | S (annotation only, hint) | S | — | S | not scored as fields |
| Marketplace order no. | S | S | VA as a link **hint** only after WI-V2 (pattern + "Order" label) | S | U |
| Links | LLM never creates a link; suggestions only; auto only for verified deterministic anchors (D-6) | | | | |
| Reconciliation outcome | Deterministic engine only; `Matched` needs every critical field at/above the review floor | | | | |

**RULE-DESC (owner, 20 Oct).** A line description is never auto-accepted, merged as verified, or used as verified evidence for linking until WI-Q1 shows a measured fix on held-out data (Qwen currently has 40 wrong, not missing, of 108: D74/D75(b)).

### 2.5 Measurement rules

- Every report names: prompt version, model id, server version and canary digest (WI-OPS3), sampling profile, rules hash, `ENGINE_VERSION`, `CONSENSUS_VERSION`, truth-file SHA, dev or held-out split, and the read date.
- One variable changes per experiment; a server-dependent comparison needs a control read the same day (D76: identical requests give identical answers within a day; one drift event is documented, 16 → 17 Oct).
- Hard-zero results and critical false accepts are tracked on their own line, not folded into accuracy.
- Held-out data are looked at only at gates and never used for tuning (§5.1).
- **Unit of evidence = unique document (D83, O-26).** Every per-field `n`, every Wilson bound, every gate and every DP-L1 check counts unique documents: copies, re-scans, re-uploads and photos of one document are one document (identity = one truth `document` entry; its copies are its `sources`). A (document, field) pair is **correct only if correct in every copy that was read**, wrong if any copy gave a wrong value, otherwise missing (strict rule, proposed in D83; the owner may change it). Reports also print the "with copies" counts, labelled as extra information; they are never used for a bound, a gate or a claim. Line rows of one document are correlated, so row counts overstate independence even when unique: line claims also state the number of unique documents behind them.

### 2.6 One contract, measured per provider (D80)

Everything in §2.1 – §2.5 applies to **each connected provider separately**: provenance (provider, model, prompt version) on every value, the same validators, the same OCR-agreement rules including the narrowed O-10, DP-L1 thresholds measured and enabled per provider and per field type, a circuit breaker per provider, and the hard zeros for both. A provider that is Connected is *selectable*; it is *enabled for acceptance* of a field type only when its own held-out numbers pass DP-L1. A provider without measured numbers gives suggestions only. The existing workspace-wide `auto_verify_field_types` (five types, set before any measurement) becomes per provider and starts empty (WI-PV1). Two different LLMs remain one reader kind for D42 (DP-L2): their agreement never verifies anything, and in any case only one provider reads a given request.

---

## 3. Verified baseline

### 3.1 Headline (39 PDFs of the dev set unless stated)

| Measure | Rules + OCR (Tesseract 5.5) | Qwen v4.qwen + D77 | Source |
|---|---|---|---|
| Headers, 39 PDF docs | 242/313 (77.3%); wrong 24 | **275/313 (87.9%)**; wrong 3; accepted-wrong 0 | `ev2-e5-v4-ctl.json` (D77) |
| Headers, all 51 parsed docs (incl. XML) | 302/373 (81.0%) | not run on XML | `scanfix-truth-2.json` (`470682236b6d`, D60) |
| Lines recall (amount) | 94/123 (PDF); 96/141 (all) | 90/123 | same; D74 |
| Links | 3/10 (0 auto, 3 suggested, 7 missed) | n/a (reader only) | `scanfix-truth-2.json` (D58/D60) |
| Auto-verified documents / touches per document | 18/51 (35.3%) / 4.96 | n/a | same |
| Identity: extra documents / wrong merges; duplicates caught | 8 / 0; 0 of 2 | n/a | same (D55) |
| Hard zeros | 0 / 0 / 0 / 0 | simulated `review_cleared` 1 (S08-INV) | same |
| Gemini flash-lite cached (comparison, v3 prompt) | — | headers 271/313; lines 80/123 | `after-d41-lite.json` (D41) |

No Gemini measurement exists under the current `vision.prompt.v4.gemini` and schema v4; the stored Gemini numbers above are v3 on flash-lite (D41). PH2 arms E1–E4 produce them. Until a provider has its own held-out numbers it gives suggestions only (§2.6).

### 3.2 Per field (dev set, 39 PDFs; Qwen = v4.qwen + D77 rule; correct/total)

"Floor" is the Task 4.9 per-field floor (95%). *Max LB* is the highest 95% Wilson lower bound this n could ever show with zero errors (`n/(n+3.84)`); *obs LB* is the Wilson bound of the observed Qwen value. `unverified` where the figure is not in a stored report.

| Field | Qwen | OCR | Floor gap (Qwen) | n | Max LB at n | Obs LB (Qwen) | Source |
|---|---|---|---|---|---|---|---|
| doc no. | 32/39 (82.1%) | 31/39 (79.5%) | −12.9 | 39 | 91.0% | 67.3% | `ev2-e5-v4-ctl.json`; was 27/39 before D77 (D74) |
| doc date | 35/39 (89.7%) | 38/39 (97.4%) | −5.3 | 39 | 91.0% | 76.4% | same |
| vendor name | 39/39 (100%) | 15/39 (38.5%) | n/a (not critical) | 39 | 91.0% | 91.0% | same; was 32/39 |
| GSTIN | 55/59 (93.2%) | 55/59 (93.2%) | −1.8 | 59 | 93.9% | 83.8% | same; was 53/59 |
| totals (taxable, tax, grand) | 102/122 (83.6%) | 91/122 (74.6%) | −11.4 | 122 | 96.9% | 76.0% | same; was 96/122 |
| printed PO/GRN refs | 12/15 (80.0%) | 12/15 (80.0%) | −15.0 | 15 | 79.6% | 54.8% | same |
| line amount | 90/123 (73.2%) | 94/123 (76.4%) | −21.8 | 123 | 97.0% | unverified | same |
| line quantity | 79/114 (69.3%) | 65/114 (57.0%) | −25.7 | 114 | 96.7% | — | same |
| line rate | 85/118 (72.0%) | 67/118 (56.8%) | −23.0 | 118 | 96.9% | — | same |
| line description (ratio ≥ 0.85) | 47/108 (43.5%); **40 wrong**, 21 missing | 59/108 (54.6%); 34 wrong | −51.5 | 108 | 96.6% | — | same; D75(b) |
| HSN/SAC | 46/75 (61.3%) | 61/75 (81.3%) | −33.7 | 75 | 95.1% | — | same |
| handwritten refs found | 1/18 (v4); 6/16 (v6); 4/18 (v5) (old truth; D87: v4 2/18 with the S06 correction alone, 9/57 on the new truth; Gemini flash-lite 6/18 to 9/18 to 24/57; D92 on unique documents, typed correctly: v4 3/16, v4 + band 12/16) | 0 (not in OCR text) | n/a | 18 (57 after D87) | — | — | D74–D76, D87, D92 |
| marketplace order no. | 0/5 (v4); 5/5 (v6, v7) | 0 | n/a | 5 | — | — | D74–D77 |

**Sample size caveat (verified 20 Oct):** the n above counts bench documents, which include copies and re-scans of the same underlying document; the truth file holds only 26 independent documents, 10 of them invoices (§3.4). The Wilson bounds in this table are therefore optimistic, and the true evidence for invoice-only fields is closer to n ≤ 10 than n = 39.

**3.2a Unique-document counts (D83; computed 20 Oct from the truth file, counts only; the with-copies column reproduces the 313 headers and 123 line rows of §3.1 exactly).** The 39 dev PDFs are **20 unique documents**: 10 invoices (29 of the PDFs) and 10 POs/GRNs (10 PDFs). Across all 26 truth documents the number of sources is 1 for 12 documents, 2 for 6, 3 for 5 and 4 for 3. *Max LB* = `n/(n+3.84)`, the best 95% lower bound that zero errors could give.

| Field | n with copies (extra info) | n unique documents | n unique invoices | Max LB: copies / unique docs / unique invoices |
|---|---|---|---|---|
| doc no. | 39 | 20 | 10 | 91.0% / 83.9% / 72.3% |
| doc date | 39 | 20 | 10 | 91.0% / 83.9% / 72.3% |
| vendor name | 39 | 20 | 10 | 91.0% / 83.9% / 72.3% |
| GSTIN (supplier and buyer values) | 59 | 24 | 16 | 93.9% / 86.2% / 80.6% |
| totals (taxable, CGST, SGST, IGST, grand) | 122 | 46 | 36 | 96.9% / 92.3% / 90.4% |
| printed PO/GRN refs | 15 | 6 | 4 | 79.6% / 61.0% / 51.0% |
| line amount (rows) | 123 | 47 | 33 | 97.0% / 92.4% / 89.6% |
| line quantity | 114 | 44 | 30 | 96.7% / 92.0% / 88.7% |
| line rate | 118 | 45 | 31 | 96.8% / 92.1% / 89.0% |
| line description | 108 | 32 | 32 | 96.6% / 89.3% / 89.3% |
| HSN/SAC | 75 | 23 | 23 | 95.1% / 85.7% / 85.7% |

Read: on unique documents **no dev field can reach a 95% lower bound** (best possible: line amount 92.4%, totals 92.3%), and the observed Qwen bounds of the table above fall further. Observed counts on the unique basis are in §3.2b (D84).

**3.2b Observed on unique documents (D84; offline re-score of the stored answers, `--vision cached`, container with `--network none`, no model call).** The run reproduces the stored report exactly (Qwen 275/313 and OCR 242/313 headers, token totals 791,102 / 359,804). Strict rule (§2.5): correct only if correct in every copy read. Source: `work/accuracy-reports/rescore-unique-20261020.json` (counts only); driver `work/tools/rescore_unique_driver.py`. *LB* = 95% Wilson lower bound on the unique `n`.

| Field (all types, 20 unique dev documents) | Qwen with copies | Qwen unique | Qwen LB | OCR with copies | OCR unique | OCR LB |
|---|---|---|---|---|---|---|
| doc no. | 32/39 | 13/20 | 43.3% | 31/39 | 17/20 | 64.0% |
| doc date | 35/39 | 16/20 | 58.4% | 38/39 | 19/20 | 76.4% |
| vendor name | 39/39 | 20/20 | 83.9% | 15/39 | 6/20 | 14.6% |
| GSTIN | 55/59 | 22/24 | 74.2% | 55/59 | 21/24 | 69.0% |
| totals | 102/122 | 27/46 | 44.3% | 91/122 | 29/46 | 48.6% |
| printed PO/GRN refs | 12/15 | 3/6 | 18.8% | 12/15 | 4/6 | 30.0% |
| **all headers** | 275/313 | **101/136** | 66.3% | 242/313 | 96/136 | 62.5% |
| line amount | 90/123 | 27/47 | 43.3% | 94/123 | 25/47 | 39.2% |
| line quantity | 79/114 | 23/44 | 37.9% | 65/114 | 16/44 | 23.8% |
| line rate | 85/118 | 25/45 | 41.2% | 67/118 | 16/45 | 23.2% |
| line description | 47/108 | 13/32 | 25.5% | 59/108 | 14/32 | 28.2% |
| HSN/SAC | 46/75 | 13/23 | 36.8% | 61/75 | 15/23 | 44.9% |

Invoices only (10 documents): Qwen doc no., date and vendor name 10/10 each (LB 72.3%), GSTIN 15/16, totals 27/36, printed refs 2/4, all headers 74/86 (LB 77.2%); OCR doc no. 7/10, date 10/10, vendor name 2/10, GSTIN 14/16, totals 26/36, all headers 61/86. Of the 35 unique Qwen headers that are not correct, 33 are *missing* and 2 *wrong* (both totals); with copies it was 35 missing and 3 wrong. Auto-accepted values (policy `single`): 101 unique values, **0 wrong** (241 and 0 with copies), so the accepted-wrong gate reads 0 in 101 (rule-of-three bound ≤ 3.0%; this replaces the "0 in 136" reading of D83). OCR has 11 wrong unique headers (24 with copies).

| PH2 interim gate (pre-registered on the with-copies basis) | with copies: gate / observed | unique: gate / observed |
|---|---|---|
| headers ≥ 92% | ≥ 288/313 / 275/313 (87.9%) | ≥ 126/136 / **101/136 (74.3%)**, 25 short |
| line amount ≥ 85% | ≥ 105/123 / 90/123 (73.2%) | ≥ 40/47 / **27/47 (57.4%)** |
| line quantity ≥ 80% | ≥ 92/114 / 79/114 (69.3%) | ≥ 36/44 / **23/44 (52.3%)** |
| line rate ≥ 80% | ≥ 95/118 / 85/118 (72.0%) | ≥ 36/45 / **25/45 (55.6%)** |

Reading: the strict rule marks a value missing for the whole document when any copy missed it, so weaker re-scans and photos pull the unique figure down; the percentages in the pre-registered gates were set on the with-copies basis, and on the unique basis they are much farther away. The numbers are reported, not changed; the owner may re-register the interim gates (they are point gates, no bound is claimed from dev).

Reading the table: four critical fields (doc no., date, GSTIN, printed refs) and vendor name **cannot be proven ≥ 95% with the current n** even with zero errors. Qwen and OCR are complementary: OCR is wrong where Qwen is missing and the reverse (OCR doc no. 7 wrong; Qwen 7 missing; OCR totals 12 wrong, Qwen 3). The best-of-both bound for Qwen v4 is not stored (**unverified**; check: `scripts/accuracy_attribution.py` or `diag_cmp.py`-style offline rescoring of `ev2-e5-v4-ctl.json` answers, `--network none`). The earlier Gemini best-of-both bound was 302/313 (`after-d41-lite.json`, D41).

### 3.3 Qwen cost and latency (`ev2-e5-v4-ctl.json`, 39 documents, mixed reads)

Tokens in/out 791,102 / 359,804 over the control run; per-call latency p50 58.8 s, p95 224.1 s (129 answers); answer budget 3,550 tokens for v4; dense pages hit the cap. `repeat_variance` 190/325 is a **cross-day artefact** (reads from 16, 17 and 20 Oct are mixed) and is not sampling noise.

### 3.4 Truth set and what it can prove

- Verified 20 Oct with `scripts/truth_tally.py` (counts only): **26 truth documents (10 invoices, 8 POs, 8 GRNs)** in 16 sets and 35 files (33 PDF, 2 XML); 51 parsed documents (invoice 29, PO 11, GRN 11) because several files are copies or re-scans of the same document; 10 expected links; **2 complete PO + GRN + invoice sets**. Header fields present in truth: doc no., doc date, supplier GSTIN, vendor name 26 each; grand total 24; printed PO refs 12; buyer GSTIN 8; taxable, CGST, SGST, IGST 10 each; 24 documents have lines; 5 have handwritten refs; 2 have a marketplace order number. The earlier "about 18 invoices" was wrong: there are 10. **Counts that include copies are not independent evidence**, so every per-field n in §3.2 overstates the sample.
- **Update 21 Oct (D86, D87).** The 15 invoice PDFs added on 20-21 Oct are byte-identical copies of files already in the truth set (the S01 to S08 originals and the 7 re-scans): **no new unique invoice** (still 10 truth invoices; 8 of them are S01 to S08). The new data are two full August dumps (251 POs, 250 GRNs; the WI-D4f pool, dev-only, never held-out). With the owner's confirmation S02 to S08 carry Tier S pool links: invoice to PO **6**, invoice to GRN **7**, GRN to PO **6** plus **1** GRN that cites an external July PO; **complete PO + GRN + invoice sets: 8 with the pool** (S02 to S07, S09, S10; `truth_tally.py` still prints 2 because pool vouchers are not truth documents); **7 re-scan pairs** (S01, S02, S04 to S08; S03 has none); **16 typed handwritten references on 8 invoices** (`annotation_refs` now 19 strings on 10 documents, was 5 documents). S06's handwritten PO was corrected from 08-087 to 08-081 (D87). S01's second reference (`08-031`) stays `unverified` until the owner confirms it.

- **Pool linking (D89, WI-D4f; dev tool, never held-out).** 8 primary scans against 251 POs and 250 GRNs, arms A (rules + OCR) and B (stored Qwen v4, harness substitution). **0 false auto-links and 0 false suggestions in every run.** Invoice links found: **1 of 13 expected** (S05 to PO 08-222, suggested), in both arms and both harness versions; 12 of the 13 expected numbers exist only in handwriting and no reader delivered them as typed references. Truth GRN to PO: 6 suggested with one mapping pass (harness v1); **4 auto + 2 suggested** with the worker sequence (harness v2: mapping, ledger rebuild, mapping; converged after pass 3); pool-wide v2: **191 auto/labeled + 35 suggested GRN to PO links, all 226 agree with the Tally `REFERENCE`**; 24 GRNs unlinked (23 cite POs outside the August dump). Harness-only sign normalisation of 455 PO and 456 GRN line amounts changed **no** link, case or ledger figure. Re-scan pairs: **5/7** caught as a new version of the original; the other 2 made a second invoice document, with 0 links from them. Mapping took 18.6 min per pass on the pool (v2: about 24 + 8 + 8 min plus 12 min of ledger rebuild under four parallel runs).
- Sizing rule: for `k = n` correct out of `n`, the 95% Wilson lower bound is `n/(n+3.84)`. Needed `n` for a lower bound of 95% / 97% / 98% / 99%: **73 / 125 / 189 / 381** with zero errors; **110 / 185 / 280 / 563** with one error; **142 / 239 / 361 / 726** with two.
- With copies counted, only totals, line amount/qty/rate/description and HSN reach n ≥ 73; on **unique documents none does** (largest: line amount 47, totals 46; §3.2a, D83). With ~5 complete sets the plan can show point estimates, paired before/after comparisons on a fixed set, hard-zero counts with rule-of-three bounds, and regressions; it cannot certify any per-field or link target. Tier H (§5.1) exists to fix exactly this.
- The owner's target moved from 15–20 sets (old D-7) to ~5 (9 Oct) to the two-tier design of O-1 (20 Oct).

### 3.5 Server limits (documented 16 Oct, D-log and D68–D77; **not re-checked** in this consolidation: no network contact)

`http://43.242.226.63/vllm/v1`, over the company VPN, plain HTTP, no key; served `qwen3.5:9b` (`Qwen/Qwen3.5-9B`), vLLM 0.23.0, `max_model_len` 8192; thinking default **on** with no reasoning parser, so every request must send `enable_thinking: false`; structured outputs (`response_format json_schema`) enforce the D73 length limits (0 schema drops in the D74 read); shared with other projects; GPU, image limits and request logging unknown (owner/admin). Failures per run: `PROVIDER_UNAVAILABLE` D75 2, D76 run A 3, D77 v7 3, D77 control 7 plus 12 stopped on `provider_down`. Determinism: identical requests give identical answers within a day (D76: 28 of 28; 27 of 27), one drift event 16 → 17 Oct (22 of 27 differ), none 17 → 20 Oct (17 of 17 equal, D77). The cause (seed, cache or server config) is unknown.

### 3.6 Live application state (verified 20 Oct)

| Item | Value |
|---|---|
| Images | `reconbench-{api,worker,ai-worker,setup}:latest` created 16 Oct 04:41 IST; containers up 3 h |
| Code in images | no `openai_compat.py`, no `endpoint_policy.py`; migrations ≤ 0024; consensus code **is** present and wired in the ai-worker image |
| Live DB | Alembic `0024`; 40 documents, 31 source files; 1 buyer entity configured (count only); 47 `document_page_image` rows, all purged |
| AI settings | `gemini` / `ai_first` / `vision_enabled` true / `gemini-3.5-flash-lite`; `auto_verify_field_types` = `{doc_date, doc_no, gstin, line_amount, reference}` |
| Gemini key tier | **Free tier** (owner checked in Google AI Studio, 20 Oct); D-1's "paid" was wrong. Real documents on the live stack have been sent under free-tier data-use terms; accepted by the owner (O-25, D84). The live stack is unchanged. |
| AI requests | `h1_extract` 17 applied (5 Oct); `h3_rerank` 1 (7 Oct); `h6_vision` 26 applied (9 Oct), 2 discarded invalid, 1 failed (7 Oct); 4 provider tests |
| Consensus | 0 `consensus_run` rows although 26 vision answers were applied and `auto_verify_field_types` is set: cause **unverified** (WI-INT0) |
| Environment | `RB_AI_EXTERNAL_CALLS` value not read (`.env` names only); `RB_AI_VLLM_*` absent from `compose.yaml` and from container environments |

### 3.7 Unverified items and the check for each

| Item | Check |
|---|---|
| Truth invoices ≈ 18 | **Resolved 20 Oct: 10 truth invoices** (26 truth documents: 10 invoices, 8 POs, 8 GRNs); per-field n in §3.2 includes copies and re-scans |
| Best-of-both bound for Qwen v4 | offline rescoring of stored answers, `--network none` |
| Why `consensus_run` = 0 | WI-INT0 |
| Whether the 40 live documents equal the 39 bench PDFs | compare file SHA-256 manifests, counts only (WI-INT0) |
| `RB_AI_EXTERNAL_CALLS` effective value | `docker exec` environment read of one variable (non-secret) in AC-INT1a |
| Container reachability of the vLLM URL from the compose `ai-worker` network | WI-OPS1 smoke test 4 (reachability from `docker run` of the worker image was confirmed in D68) |
| Whether `RB_AI_VLLM_BASE_URL` is still required with per-workspace URLs (D65) | read `ai/worker/providers.py:22-40` in AC-INT1b |
| E-invoice NIC key present in the app (signed QR = L0) | WI-QR1 |
| Per-cell Qwen accuracy by scan type | WI-EV3 `vision_eval` split |
| Current input/output price of `gemini-3.5-flash-lite` | **Not verified on 20 Oct, and not needed while the Gemini project has no billing (free tier, ₹0; D83)**; required before any card is declared paid, because only then the cap computes: the official pricing page (ai.google.dev/gemini-api/docs/pricing) could not be read from this environment (the fetch tool and the built-in browser were both refused). The owner must verify it, record it here with the date, and put it in the price configuration **before cap enforcement is enabled**. Working assumption, not a fact: US$0.30 / 1M input and US$2.50 / 1M output (about US$0.006–0.010 per page); the INR/USD rate for the ₹ cap is also configuration and unverified. |
| Excel export correctness on real data | WI-EV3 export metric (MT-12) on Tier S |

---

## 4. Pipeline architecture

### 4.1 Flow

```
upload ─► classify + split (AC-3) ─► per-document page set
            │
            ├─ Reader A  Tesseract layout + rules (frozen, D72)  ── independent second reader, fallback
            ├─ Reader B  signed e-invoice QR / XML / CSV          ── exact values (L0/L1)
            ├─ Reader C  selected LLM provider (Qwen via vLLM, or Gemini), masked page images ── async, ai-worker
            │              one page per call; full page, or header / table / totals crops (WI-BE8)
            └─ Reader D  person (verify screen, AC-7)
                 ▼
   standalone checks on the LLM reading (check_reading; WI-V1 adds HSN / ref existence)
                 ▼
   merge policy (WI-BE7): arbitration table §4.3 + guards MG-1…MG-10 → new document_version (alternatives kept)
                 ▼
   consensus (D42 + O-2): independent reader + validators + local crop re-read → verified fields only
                 ▼
   vendor resolution → linking (suggestions; auto only for verified anchors) → reconciliation (deterministic)
                 ▼
   review (verify screen, provenance) → persistence (append-only) → Excel export
 Qwen down / answer unusable ► the rules + OCR version stays current; document flagged LLM_UNAVAILABLE (§4.8)
```

### 4.2 Per stage: current implementation, failure modes, fix

| Stage | Current implementation | Failure modes and evidence | Proposed fix (task) | Risk |
|---|---|---|---|---|
| ST-1 Upload | `ingest_file`; zone type; ZIP; bundle prompts a split (D35) | live image lacks D62–D77 (§3.6) | AC-INT1 | migrating the live DB |
| ST-2 Classification and split | `extraction/pages.py`; 54 page kinds correct (D41) | `test_page_split.py` 2 failures (`_current()` duplicate PO rows, D57); B11 same-type scans split only on "Page 1 of N" | WI-D6; B11 accepted gap (§10) | low |
| ST-3 OCR / text | layout engine + text layer; OCR 0.65, text layer 0.90 | 9 header and 24 line-cell OCR-absent misses on scans (diagnosis §5); poor text layers, > 20 pages, Windows decode | frozen (D72): WI-D2, WI-D2b, WI-D2c | — |
| ST-4 Page and region prep | render 2,048 px long side (≈ 175 DPI), mask, one page per call | answers cut at the output cap on dense pages: D68 1, D75 2, D76 9, D77 4 (16 failed calls, the same few dense pages recurring); 33 line rows missing per cell | **WI-BE8** | may hurt normal pages |
| ST-5 Qwen inference | shared server; 30 s provider backoff; 300 s timeout; heartbeat every third tick | unavailable runs (§3.5); p95 224 s; one drift event; thinking must be off | WI-OPS1/2/3 | fallback load |
| ST-6 Structured parse | strict top level + tolerant per-item parse (D73) | 0 schema drops after length limits; `unreadable` never listed (0 of 35–57 nulls) | none; do not rely on `unreadable` as a lever | — |
| ST-7 Normalisation and page merge | look-alike fold; D77 conflict rule | 170 duplicate and 58 conflict drops (control run); `po_refs` grew across pages under v5; order date taken as date under v5–v7 | WI-BE7 guards MG-3, MG-5; arbitration | medium |
| ST-8 Evidence and confidence | LLM-only capped 0.65/0.69; `page_conflict` excluded from accept and consensus | consensus never ran on live data | WI-INT0, AC-INT1 | — |
| ST-9 Validation | `check_reading` rejects; arithmetic, Σ lines, line arithmetic and date window are marks, not drops (D63) | no HSN shape, no reference-existence, no vendor doc-no pattern for LLM values; Σ lines = taxable is mark-only | WI-V1; AC-10 (pattern) | over-flagging |
| ST-10 Linking | resolver + AC-9a scorer | links 3/10: 4 are an AC-9a regression, 2 handwritten refs, 1 vendor pending (diagnosis §5.7); an LLM can fix ≤ 2 of 7 | WI-D4, AC-9b; WI-Q2 (hints) | low |
| ST-11 Reconciliation | deterministic engine | both status disagreements are the unlinked complete sets; hard zeros 0 | follows ST-10 | — |
| ST-12 Review | verify screen (AC-7); suggestions panel shows model only | 30 s target untimed; no provenance/disagreement UI | WI-FE2, AC-7c | — |
| ST-13 Persistence | append-only versions; AI values live only in `ai_cache` | `merge` refused (422 `merge_not_enabled`); no `merge_readings` | WI-BE7a/b/c | **highest** |
| ST-14 Excel export | synthetic workbook equals `expected.yaml` (D28); Home KPIs = Summary | real-data export never measured | MT-12 in WI-EV3; AC-11 | — |

### 4.3 Per-field arbitration (policy `arb.v1`, stored as data, versioned)

Principle: no "always trust the LLM" and no "always trust OCR". The policy names, per field, the **leader** whose value is the document's current (unverified) value when both readers pass validators and disagree; the other reading is kept as the alternative and the field enters review with warning `LLM_DISAGREEMENT`. Nothing here auto-accepts; acceptance is §2.3/§2.4. The default leader is **OCR for every field** (so flag-off = today); a field's leader changes only after the held-out set confirms the dev-set lead (paired comparison, lead ≥ 10 points and wrong-count not higher).

| Field | Dev evidence (Qwen / OCR, correct; wrong) | Proposed leader after confirmation | Conflict handling |
|---|---|---|---|
| vendor name | 39 / 15; 0 / 2 wrong | Qwen | alternative kept; never auto-verified |
| totals (amount) | 102 / 91; 3 / 12 wrong | Qwen | arithmetic tree decides when it closes for exactly one candidate |
| line qty, rate | 79 / 65; 85 / 67 (OCR 12 rate wrong) | Qwen | row-level: qty × rate ≈ amount decides |
| doc no. | 32 / 31; 0 / 7 wrong | OCR (tie) | disagreement ⇒ both shown; look-alike guard stays |
| doc date | 35 / 38 | OCR | Qwen order-date errors seen under v5–v7 ⇒ never lead |
| GSTIN | 55 / 55 | OCR (tie) | checksum + buyer + state; both valid and different ⇒ R for the buyer-role one, else S |
| printed refs | 12 / 12 | OCR (tie) | extra ref on a later page ⇒ MG-5 |
| line amount | 90 / 94 | OCR | row alignment as in the bench |
| line description | 47 / 59, both mostly wrong | OCR; **no AA** (RULE-DESC) | S only |
| HSN/SAC | 46 / 61 | OCR | shape check (V-HSN) |
| handwritten refs | OCR 0 | Qwen as hint only | annotation, never a field |
| marketplace order no. | 0 (v4) / — | Qwen after WI-V2 | rename by pattern |

A value that fails its validator is excluded from the merge but kept as a visible suggestion (`check_failed:<reason>`, D63). A value marked `page_conflict` (D77) is a suggestion only, never the leader and never used by consensus.

### 4.4 Deterministic validators on LLM values

| Validator | Checks | Exists? | Needs D72 lifted? |
|---|---|---|---|
| V-GSTIN | checksum, not a buyer entity, equals the vendor-master GSTIN of the vendor (all three required for the validator-only path), state vs place of supply, tax type vs state | yes (`check_reading`, `values.py`, `parties.py`) | no |
| V-ARITH | taxable + taxes ± round-off = grand (±₹1); amount in words. With V-SUMLINES it forms the **full chain** that alone permits the amount validator-only path | yes, mark-only (D63); chain gate new in WI-BE7a | no |
| V-LINE | qty × rate ≈ amount | yes, mark-only | no |
| V-SUMLINES | Σ line amounts = taxable (±₹1) | yes, mark-only; becomes a **gate** for clearing review (MG-4) | no |
| V-DATE | date window (D42), invoice date ≥ PO date warning | yes, needs `as_of` | no |
| V-REF | reference shape (no address digits, marketplace or sales-order number as PO) | yes | no |
| V-HSN | 4/6/8-digit HSN, 6-digit SAC (`99…`) | OCR table path only (`table.py _HSN_SHAPE`); **new** for LLM values (WI-V1) | no (validator on LLM output) |
| V-REFEXIST | a printed PO/GRN ref equals an open same-vendor PO/GRN number; evidence for the ref only, links stay suggestions | link-time only; **new** at acceptance (WI-V1) | no |
| V-DOCNO-PATTERN | per-vendor document-number pattern | no; AC-10 | no |
| V-MARKETPLACE | label contains "Order" and value matches `DDD-DDDDDDD-DDDDDDD` ⇒ rename to `marketplace_order_ref` | no | **yes, WI-V2 only (O-4)** |
| V-NOTINOCR | value absent from folded OCR text ⇒ cannot be accepted unless the crop re-read agrees; also reported as MT-10 | metric exists (D68+); gate is D42 rule 3 | no |

### 4.5 Dense pages and truncation: WI-BE8 design

Cause: one page per call with a 3,100–3,550-token answer budget (8,192 context, ≈ 3,500 image tokens) truncates dense tables; 33 rows per line cell are missing (D74) and 16 calls hit the cap across D68–D77 (the same dense pages recurring).
- **Region selection (variant A, default, no extra LLM call).** From the deterministic layout of the masked page: a header band (above the table heading, ≤ 35% of height), one or more table bands (heading to summary rows; a band covers at most R rows, start R = 12, tuned on dev), and a totals band (summary rows to page end). No heading found ⇒ the full page is read (fallback, as today).
- **Overlap and context.** Adjacent table bands overlap by one full row (≥ 8% of band height). Every table crop carries the column-heading strip (re-drawn from the same masked pixels) and the text "Page p, band k of m, kind". Long side per crop ≤ 1,600 px.
- **Boxes and merge.** The model returns boxes relative to the crop; pure core `map_crop_box` converts them to page boxes; pure core `merge_regions` combines regions of one page: header fields only from header and totals bands; rows from table bands; an overlapped row seen twice is merged when (amount, quantity, rate) are equal and the mapped boxes overlap (IoU ≥ 0.5); if the two copies disagree both are kept as `region_conflict` suggestions (never a value). Cross-band duplicates of a header field use the D77 conflict rule.
- **Cost.** Calls per page rise from 1 to about 3–5, each with fewer image tokens; total tokens and wall time are reported (latency is not judged until accuracy is decided, D72, but is recorded).
- **Proof it helps without hurting.** One variable (strategy `crops` vs `full`), same prompt, same-day full-page control, dev set. Accept if (a) on the dense subset (documents with an output-cap failure in any D68–D77 read) usable answers go from the control's to all, and line-amount recall on scan pages rises ≥ 5 points; (b) non-dense documents: headers and line cells not lower by more than 1 field in total; (c) wrong-when-answered and accepted-wrong not above the control; (d) a failed region falls back to the full-page answer for that document. Otherwise stay on full pages.

### 4.6 Weak-field levers (one lever and one experiment each)

| Field | Failure | Lever | Experiment (pre-registered target on dev; held-out confirms) |
|---|---|---|---|
| line description | 40 wrong: row text merged with other columns (34, D75(b)); 21 not aligned | WI-Q1: read the description column band per row (layout x-range from the heading) in the table crop pass; hypothesis only: v5's description wording lowered the score (47 → 39) | correct ≥ 65/108 and wrong ≤ 20; RULE-DESC stays until held-out shows it |
| HSN/SAC | 26 missing; OCR leads | arbitration (OCR leads) + V-HSN + crops | Qwen+OCR combined ≥ OCR's 61/75 with 0 new wrong |
| doc no. | 7 missing; own number of PO/GRN renamed to `po_ref`/`grn_ref` (7 of 12 pre-D77 nulls, D75) | WI-Q3: single-rule prompt variant "doc_no is this document's own number (PO number on a PO, GRN number on a GRN)" on v4 | doc no. ≥ 36/39 with other field groups net ≥ −1 and accepted-wrong 0 |
| handwritten refs | 1/18 (v4); 0 in OCR text; v6 reached 6/16 but lost CGST/SGST | WI-Q2: annotations rule alone on v4, and (if BE8 passes) a margin-band crop pass | recall ≥ 6/16 with no loss on any header group; output is link hints only |
| marketplace order no. | 0/5 on v4; v7 sentence gave 5/5 but 4 accepted-wrong on S03-INV | WI-V2 deterministic post-check, no prompt change | 5/5 with 0 side effects on any other field |
| references (printed) | 3 missing, 3 OCR wrong | V-REFEXIST + arbitration | ≥ 13/15 with 0 wrong accepted |

### 4.7 Prompt policy

`vision.prompt.v4.qwen` is the baseline (the vLLM reader is on `qwen` again; v5, v6 and v7 stay as byte-identical variants). A new prompt is accepted only if, on the same fixed dev set and against a same-day v4 control read: the target field gains ≥ 3 correct; every other header or line group is net ≥ −1; accepted wrong critical values are 0; the stated target failure is the one that improved. Each run records prompt, model, server version, canary digest, sampling profile, seed and rules hash. One variable per run. Prompt text changes bump the prompt version and the vLLM consent fingerprint (re-consent).

### 4.8 Server reliability and fallback

| Mechanism | Today | Plan |
|---|---|---|
| Health check | `GET /models` every third heartbeat; writes `last_health_*` (D65) | WI-OPS2: status code `LOCAL_PROVIDER_DOWN` in settings and a Home banner |
| Retry / backoff | single attempt; 30 s backoff after `rate_limited`/`PROVIDER_UNAVAILABLE` | WI-OPS2: one deferred retry after 60 s; then give up |
| Timeout | 300 s per call, `max_concurrency` 1 | unchanged; recorded latency feeds WI-OPS2 |
| Fallback (O-6, rewritten D80) | async path leaves the OCR version current | document-level flag `LLM_UNAVAILABLE` or `PROVIDER_DISCONNECTED` visible in review; **no silent automatic failover**; the UI shows provider status next to the selector and offers a manual switch; routing to the other provider happens only with the explicit setting `allow_provider_fallback` (default off, consent required, every affected document flagged) |
| Queue | would pile up | skip new requests with `PROVIDER_UNAVAILABLE` while health has failed > 5 min |
| Cache | `cache_key_v2` over provider, model, prompt, schema, reader params, masked image digests | unchanged; add server fingerprint as provenance (not in the key) |
| Cross-day drift | undetected | WI-OPS3: canary of 3 synthetic pages read daily; the answer digest and `/version` are stored; a change opens a "server epoch"; comparisons never mix epochs |
| Server down for a week | not handled | every document is processed on rules + OCR and flagged; hard zeros are unaffected because that path is today's; metric `llm_coverage` shows the loss; on recovery the owner chooses whether to back-fill |

### 4.9 Linking and reconciliation

- Evidence-backed links only; similarity is not proof (D-6): auto-link only for verified deterministic anchors; every content or multi-signal link stays a suggestion. Handwritten refs and marketplace order numbers are hints/anchors that still need a same-vendor open PO and an amount signal.
- Cases the Tier S sets must cover (§5.1): partial deliveries, several GRNs for one PO, several invoices for one PO, an exact duplicate and a re-scan, an invoice without a PO number, a cross-vendor same-reference collision, a period boundary.
- Arithmetic is always deterministic (quantity/value ceilings, tolerance, variance split); the LLM only extracts. Known open defects: 4 GRN→PO links lost in AC-9a (WI-D4), B6/B10 (AC-9b), 8 extra documents and 2 missed duplicates (AC-8b).

### 4.10 Merge policy (WI-BE7) and its guards

The pure core `merge_readings(ocr_document, checked_readings, policy)` produces a candidate document; the general worker (never the ai-worker) appends it as a new `document_version` (`extraction_method = 'ai_fallback'`, `derived_from_version_id` = OCR version). Per field: both pass and agree ⇒ keep, supporting readers recorded; Qwen passes and OCR is missing or failing ⇒ Qwen value, source AI, confidence capped 0.69; both pass and disagree ⇒ arbitration leader, alternative kept, `LLM_DISAGREEMENT`; Qwen unreadable/omitted/`check_failed` ⇒ OCR value plus flag; annotations never enter fields.

| Guard | Rule |
|---|---|
| MG-1 | Provenance on every field and line cell: reader id (`llm:<provider>:<model>:<prompt>`), schema, reader-params digest, `ai_request` id, page, box (HZ-6) |
| MG-2 | An LLM-only value is capped below the review floor and is never verified except through consensus (D42 + O-2) |
| MG-3 | A `page_conflict` value is a suggestion; it never leads the merge and is skipped by consensus (D77) |
| MG-4 | The review flag is cleared only when Σ line amounts = taxable (±₹1) **and** a taxable value exists; a missing taxable never clears it (D75(e); the S08-INV case) |
| MG-5 | A `po_refs` set that grew across pages is not accepted automatically (D75: v5 wrong refs on S01/S02) |
| MG-6 | Descriptions never auto-accepted (RULE-DESC) |
| MG-7 | A manual correction or verified field is never replaced; the merged version is stored as an alternative (D19 guard, B9) |
| MG-8 | Flag off (`RB_AI_LLM_MERGE` disabled, `apply_mode` suggest) ⇒ the default bench report hash is identical to the pre-merge hash; one switch rolls back; "restore OCR version" appends a copy |
| MG-9 | Handwritten and stamped marks are annotations; their references stay hints |
| MG-10 | DP-L1 thresholds per field type and a completed shadow period before any field type is enabled for acceptance |
| MG-11 | Never take a field value from a page classified as belonging to another document (a page kind or warning such as `ATTACHED_EWAY_BILL_OTHER_DOCUMENT`); the S05 page 3 case is the test (D89: the stored Qwen v4 answer took the taxable total from that page on the original copy) |

### 4.11 Integration ladder (a feature is done only when every rung is verified)

| Rung | Evidence required | Status (20 Oct) |
|---|---|---|
| RG-1 Code in the tree | files present, offline suites green | verified in tree (1090 passed per D77; not re-run) |
| RG-2 Config | compose forwards `RB_AI_VLLM_CALLS`, `RB_AI_VLLM_ENDPOINTS` (and `RB_AI_VLLM_BASE_URL` if still needed), `RB_VISION_READER` | **missing** (`compose.yaml` forwards none) |
| RG-3 Server reachable from the running ai-worker | `GET /models` 200 from `docker exec ai-worker` | reachable from a `docker run` of the worker image (D68); compose container unverified |
| RG-4 Enabled | gate mode, endpoint allowlist, workspace card, reader strategy, consent current | no |
| RG-5 Deployed image runs that code | image digest, file presence, migrations at head | **no** (§3.6) |
| RG-6 Qwen output reaches the app | `reader_comparison` rows in shadow; then suggestions | no |
| RG-7 Validated values persisted | merged `document_version` rows with provenance | no |
| RG-8 Reconciliation uses them | recompute after the new version; status/link deltas reported | no |
| RG-9 Excel export correct | workbook cells equal expected on Tier S | never measured on real data |

Shadow before merge, always; a one-switch rollback at every step; consent, endpoint allowlist and network safeguards stay (SSRF policy `endpoint_policy`, exact origin + path; redirects never followed).

### 4.12 Versioning rule (O-5)

| Change | Version that must change | Check |
|---|---|---|
| Recon-path rules (align, mapping, normalize, recon, schema, seed) | `ENGINE_VERSION` patch | `scripts/check_engine_version.py` |
| OCR/rules extraction output | `PARSER_VERSION` | new parser-hash guard (WI-D3) |
| Vision check or consensus logic in core (`extraction/vision.py`, `extraction/consensus.py`) | `CONSENSUS_VERSION` (or a new `VISION_CHECK_VERSION`), stamped on every `consensus_run`, `reader_comparison` and evidence row | extended guard (WI-D3) |
| Prompt / schema / render / mask | prompt version, schema version, `reader_params` (these already change cache keys and consent) | existing tests |
| Any core `.py` change | `RULES_HASH` file | `scripts/check_rules_hash.py` |

The guards compare the working tree with the **last release tag**, not HEAD (HEAD is `0.1.4` while the tree is `0.1.7`, so a HEAD comparison is vacuous). No release tag exists today; the owner creates the first at AC-INT1b. **D77 resolution:** D77 changed vision conflict resolution and the consensus skip without a version bump, which is consistent with the old script (it does not watch `extraction/`) but not with this rule; WI-D3 bumps `CONSENSUS_VERSION` to `consensus.v2` retroactively and records it. Recorded in D78.

### 4.13 Findings carried from the LLM audit (16 Oct; the audit document is retired, §14)

Where a correct LLM value can be lost today (P1–P10) and the plan's answer:

| # | Drop point | Plan answer |
|---|---|---|
| P1 | Never enters the document (suggestion only) | WI-BE7 |
| P2 | Not requested: `ai_assisted` skips documents that do not need verification; > 8 pages skipped | eligibility reviewed in WI-BE7b; page cap in D-8 terms for Gemini only |
| P3 | A strict parse failure used to lose the whole answer | fixed by the tolerant per-item parse (D73) |
| P4 | Core drops per item (control run: duplicate 170, conflict 58, bad_value 41, gstin_invalid 29, bad_cell 10 …); correctness of dropped values unknown | WI-EV0-style attribution of drops on Qwen answers inside WI-EV3 |
| P5 | All-or-nothing `ungrounded` when every item is dropped | WI-BE7a keeps per-item suggestions |
| P6 | "Leave out what you cannot read" makes omission and absence look equal | `unreadable` exists (v4) but is never used by Qwen (0 of 35–57); not a lever |
| P7 | Consensus keeps OCR when readers disagree (L3) | arbitration §4.3 |
| P8 | Cache expiry after 30 days hides suggestions | accepted; shadow comparison rows persist |
| P9 | A stale source (new version, consent or settings change) discards the answer | accepted by design; re-read on demand |
| P10 | Handwritten marks never become fields | by design (MG-9) |

Coupling gaps found and where each is handled: one provider instance per worker and one provider/model/key per workspace (done D62, D65); consent text and preview assumed Gemini (done D65); the cache key omitted provider and reader params (done D63); LLM arithmetic was only checked against consistent OCR totals (done D63, mark-only; gate in MG-4); review UI shows model only (WI-FE2); no mock OpenAI-compatible server beyond `httpx.MockTransport` (accepted, D64); **text hooks H1–H4 are bound to Gemini and `HOOKS_BY_MODE` keeps H2–H4 in both active modes (new finding; with the 20 Oct requirement that Gemini is not turned off, the work is now provider-agnostic hooks, WI-BE9a/b/c)**. Facts relied on from external documentation (checked 16 Oct, sources named in the retired audit): Qwen3.5-9B is multimodal, Apache-2.0; thinking is on by default and is disabled per request with `chat_template_kwargs {"enable_thinking": false}`; card sampling for non-thinking is temperature 0.7, top-p 0.8, top-k 20, min-p 0, presence penalty 1.5; structured outputs through `response_format json_schema`; `--api-key` protects `/v1` only; quantised variants exist from RedHat/community, none official; image limits and `--limit-mm-per-prompt` are **unverified** (check `vllm serve --help` on the server version). Gemini 3: keep the default temperature; thinking levels low/medium/high; `media_resolution` high recommended.

### 4.14 Roles of the providers

Qwen and Gemini are both product features. Each request goes to exactly one provider, chosen by the resolution order of §4.15.5; answers of the two providers are never merged, voted or compared inside the app (`dual_agree` stays excluded). One **live** provider and at most one **shadow** provider may run for the same document version; the shadow reading writes only comparison rows. There is no silent automatic failover (O-6). Gemini is not turned off: AC-LIVE-S keeps it the live default while Qwen runs in shadow.

### 4.15 Provider platform: connect, select, secure, measure (D80)

**4.15.1 Reuse map (verified 20 Oct by reading code and D64–D66; nothing here is built twice).**

| Need | Existing code | Gap | Task |
|---|---|---|---|
| Provider port, adapters, registry | `ai/provider.py`, `ai/worker/gemini.py`, `ai/worker/openai_compat.py`, `ai/worker/providers.py`, `VisionReaderInfo` (D62, D64) | `RB_VISION_READER` default is still `vision_gemini` (D64 deferred `all`) | WI-PV5 |
| Per-workspace provider card | table `ai_provider_config` (provider, enabled, model, base_url, key_secret_id, limits, last_test_*, last_health_*, version; unique per workspace and provider; D65) | no connection state or error reason, no consent per card, no cost cap, Gemini key lives on `ai_setting`, per-card `limits` stored but not applied | WI-PV1, WI-OPS2 |
| Credentials at rest | `ai/key_seal.py`: X25519 public key in the API, AES-256-GCM, additional data binds workspace, row and purpose; private keyring only in the ai-worker (mounted `/run/ai-keys`, not the database); `secret` table; write-only, masked hint (last 4); session or saved storage; worker refuses `GEMINI_API_KEY`/`GOOGLE_API_KEY` | none for the mechanism; keyring rotation procedure **unverified** | reuse as is |
| Settings API | `GET/PUT /ai/settings` with `providers[]`, `POST /ai/test/provider {provider, kind}`, `GET /ai/consent-preview`, `GET /ai/usage?provider=`, `GET /ai/requests/{id}` (D65) | connect/disconnect/rotate are not separate verbs; Gemini and vLLM differ in shape | WI-PV2 |
| Test connection | queued by the API, executed by the ai-worker (the API never calls a model); vLLM text test; Gemini synthetic text test; saving a changed card clears `last_test_*` | `Connected` is not a derived state; `kind=image` returns 422 | WI-PV2, WI-OPS1 |
| Allowlist / SSRF | `ai/endpoint_policy.py` (http/https only, no user info, query, fragment, dot or encoded segments; exact origin + path prefix; redirects never followed) and `DeploymentGate.endpoint_allowed`; checked on save and before every request | env-only list; no scheme rule beyond http/https; no explicit loopback/metadata block | WI-PV4 |
| Consent | `ai/consent.py` fingerprints per provider and data destination text; preview endpoint | one workspace consent, not accepted per provider at connect | WI-PV2 |
| Deployment gate / kill switch | `RB_AI_EXTERNAL_CALLS` (+ approval ref) for Gemini, `RB_AI_VLLM_CALLS` for Qwen; `disabled` is the default and wins over any setting | two flags, no single master | documented in 4.15.4 |
| Permission and audit | capability `ai.manage` on every AI settings route; audit events `ai.provider_config_save`, `ai.reader_strategy_change`, `ai.apply_mode_change`, `ai.test_requested`, `ai.key_remove` (before/after without keys) | **`resolve_effective_role` maps every persisted user to `manager` ("P0 … P1 checks stay off", `authorization.py:93-99`), so any logged-in user holds `ai.manage` today** | WI-PV3 (O-18) |
| Health | `sweeps.provider_health` every third heartbeat, vLLM only | Gemini not covered; status not shown next to a selector | WI-PV2, WI-OPS2 |
| Cost cap and usage | `ai_setting.budget_cap_usd`, price file, ledger `reserve/finish`, `GET /ai/usage?provider=` | cap not tied to a provider card or shown against usage; no handling of 429 / daily quota (free tier, D83) | WI-PV7 |
| Reader choice and shadow | `ai_setting.reader_strategy` (off/shadow/single), `primary_reader`, `apply_mode`; shadow requests and `reader_comparison` (D66) | cannot say "Gemini live and Qwen shadow"; strategy replaces the live reader | WI-PV1, WI-PV5 |
| Text hooks | `HOOKS_BY_MODE`, H1–H4 use `ai_setting.provider/model/key` (Gemini) | not provider-agnostic | WI-BE9a/b/c |
| UI | Home `AiAssistCard` dialog, Gemini-only | provider cards, selectors, provenance | WI-FE1a/b, WI-FE2 |

**4.15.2 Connection lifecycle.** States per workspace and provider: **Not connected** (no credentials, or credentials changed or disconnected and not yet tested), **Connected** (the last server-side test after the last change succeeded and the latest health check did not fail), **Error** (last test or health check failed; reason code and time shown). The card shows the state, the last-checked time and the reason. Only a successful test makes a provider Connected, and only Connected providers appear in the selectors. Changing the URL, model or key resets the card to Not connected until it is tested again (this already happens for `last_test_*`). **Rotate = test then swap**: the new key is tested first and replaces the old one only on success. **Disconnect** deletes the key and records who and when; the workspace selection keeps naming the provider (reconnecting restores it) but the effective provider is none, so new documents take rules + OCR with the visible flag `PROVIDER_DISCONNECTED`. **In-flight jobs** finish or fail cleanly: the runner re-checks settings before sending and before applying (an answer for a disconnected provider is discarded, never half-applied). A periodic health check keeps the status current. Gemini connect requires accepting the egress notice ("documents leave the company network"); Qwen shows the company-network notice; acceptance is stored with the consent fingerprint, who and when. No environment edit, container rebuild or restart is needed to connect, switch or disconnect once the deployment gate and allowlist ceiling are set (4.15.4).

**4.15.3 Security requirements and where each is met.** Credentials encrypted at rest with a key from a server-side secret, not the database (met by the sealing design above); never returned after save (masked last 4); never in logs, errors, reports, exports, audit rows or decision-log entries (WI-PV8 scans each); only the admin capability connects, rotates or disconnects, and every change is audit-logged with who, when and which provider (WI-PV3); base URL validated by the allowlist, loopback, link-local and cloud-metadata addresses refused unless the deployment ceiling lists them, https or an approved internal host only, no URL outside the company network (WI-PV4, O-20); a deployment-level kill switch overrides every UI setting (4.15.4).

**4.15.4 Environment versus database.**

| Setting | Today | New role |
|---|---|---|
| `RB_AI_EXTERNAL_CALLS` (+ `_APPROVAL_REF`) | gate for every Gemini call; `disabled` default; `enabled` needs an approval reference | **kill switch** for Gemini plus the approval record; unchanged |
| `RB_AI_VLLM_CALLS` | gate for every Qwen call | **kill switch** for Qwen; unchanged |
| both flags `disabled` | — | the documented **global kill switch**: no provider receives a request even if connected and selected (tested, WI-PV8) |
| `RB_AI_VLLM_ENDPOINTS` | exact allowlist of Qwen base URLs | **ceiling**: the origins or URLs the deployment permits, with an explicit marker for approved plain-http internal hosts; the per-workspace list in the database must fall inside it (O-19) |
| `RB_AI_VLLM_BASE_URL` | worker-level default URL | **deprecated** if per-workspace URLs make it redundant (D65); confirm in AC-INT1b, otherwise keep as a fallback default |
| `RB_VISION_READER` | `vision_gemini` by default; `off` disables page images | default `all`; `off` stays a deployment off-switch for page images |
| `RB_AI_KEYRING_FILE`, `RB_AI_SEAL_PUBLIC_KEY_FILE` | server-side secret files for credential sealing | unchanged: the encryption key never lives in the database |
| `RB_AI_LLM_MERGE` (planned, WI-BE7c) | — | deployment switch for merge, default `disabled` |
| Database (per workspace) | provider cards, credentials (sealed), connection state, consent, defaults, shadow provider, fallback setting, workspace allowlist entries, cost cap, per-provider auto-verify types and breaker state | **connections**: changed only by the admin through the API, audited |

**4.15.5 Choosing the provider.** Resolution order at enqueue time: per-upload (batch) override → workspace default for that path → none (rules + OCR, flagged). Two workspace defaults exist: `extraction_provider` (page-image extraction and H1) and `assist_provider` (H2–H4, matching/reconciliation assist); both are chosen only from Connected providers. The resolved provider is written on `ai_request.provider`, so each reading is attributable. Scope: page-image extraction and the AI text hooks H1–H4. **Re-run with the other provider** creates a new reading for the current version; it never replaces the current version or reading (4.15.6, WI-PV6). Reconciliation arithmetic, validators and matching rules stay deterministic and provider-independent; they consume the values of whichever version is current, after validation and merge. A provider that is disconnected or down is never replaced silently (O-6).

**4.15.6 Provenance and traceability.** Every AI-sourced value carries provider, model, prompt version, schema version and reader-params digest (MG-1). The document page, the review screen and the Excel export show them (WI-FE2, WI-EXP1). Switching the default, rotating a key or re-reading a document never alters an existing reading or version; a re-run reading is stored as an alternative and becomes current only through the existing audited correction save. Reconciliation results are traceable: a run's manifest names the document versions it used, each version names its extraction origin, and the export carries an additive Provenance sheet (existing sheets unchanged; confirm against the export contract first, O-23).

**4.15.7 Measuring both.** WI-EV2 and WI-EV3 report Qwen and Gemini side by side on the same truth set and splits, with per-field tables and n. A counts-only measurement file is imported by an admin into a settings table, and Settings shows a short note on which provider measured better per field (with n and the date). The note informs; the user decides. DP-L1 numbers, arbitration and the circuit breaker are keyed by provider and field type.

**4.15.8 Availability.** If the selected provider is down or disconnected, new documents fall back to rules + OCR with a visible flag, the UI shows the provider's status and offers a manual switch (admin: default; any user: re-run), and nothing switches by itself.

**4.15.9 Gemini free tier: quota, rate limits and data use (D83).** Gemini runs on the free tier (`gemini-3.5-flash-lite`, no billing on the Google project). The API cannot tell the app which tier a key is on, so the admin **declares the tier at connect** (`free` or `paid`; until stated, the stricter free-tier text applies); changing it is audited and re-asks consent.
- *Cost.* A free tier costs ₹0. The O-24 mechanism (₹2,500 a month, warning at 80%, ₹20 per document) stays built and tested but is **dormant**: it evaluates only for a card declared paid, and starts by itself when a billed key or project is connected. No price check is needed for free; a card cannot be declared paid until the price entry exists (§3.7).
- *Rate limits are the constraint.* HTTP 429 `RESOURCE_EXHAUSTED` is handled with backoff and jitter, honouring the retry delay in the error when present. The worker classifies each 429 from the error body (the quota-violation and retry entries Google returns) as **per-minute** (short wait, bounded retries) or **daily** (the project's daily quota is used up; it resets at midnight Pacific time, about 12:30–13:30 IST depending on US daylight saving, computed from the `America/Los_Angeles` zone and never hard-coded). The error layout is documented knowledge that is **not yet verified in this tree**: the first live 429 is logged (codes and counts only) to confirm it; an unclassifiable 429 is treated as per-minute, and N consecutive ones within M minutes mark the day exhausted (N and M are configuration). Request limits per minute and per day are never hard-coded: they come from the error, the response headers when present, or configuration.
- *Daily quota used up.* The card's quota state becomes `daily_exhausted` until the reset time; new Gemini requests are not sent (no wasted calls); each affected document takes rules + OCR with a visible **"Gemini quota reached"** flag (`GEMINI_QUOTA_REACHED`); **nothing switches to Qwen** (O-21 stays off). After the reset new documents use Gemini again; affected documents can be re-read with WI-PV6.
- *Quota status on the Gemini card:* OK / rate-limited until t / daily quota reached (resets at t, shown in IST), time of the last 429, and requests made by the app today (from `ai_call_log`). The API does not report the remaining quota, so none is shown.
- *Data use.* On the free tier Google may use prompts and responses to improve its products, and human reviewers may read them. For a card declared free, the consent notice at connect says this explicitly (the notice text is part of the consent fingerprint). **Owner decision (O-25, D84): accepted for real company documents; no restriction to test/sample documents.** The Gemini card shows "free tier: Google may use the data sent" while the card is declared free.
- *Bench.* E1/E2 run on a **separate** free-tier Google project and key that the owner supplies per run through `BENCH_GEMINI_KEY` (WI-EV2). The app's quota is never used by the bench, and the bench never reads the sealed app key.

---

## 5. Evaluation framework

### 5.1 Datasets and splits (O-1: two tiers)

| Set | Content | Role | Size now → target |
|---|---|---|---|
| **DEV-CORE** | the existing truth set: 26 truth documents in 35 files (39 PDFs, XML/CSV, 2 complete sets) | tuning, prompt and rule experiments, regression baseline | as is |
| **Tier H** | header-only truth for **invoices**: document no., date, supplier GSTIN, buyer GSTIN (if printed), taxable, tax amounts, grand total, printed PO refs | **held-out evidence** for per-field claims | 0 → **80 invoices** (zero errors on 80 values gives a 95.4% lower bound; one error needs 110, so an escalation batch of +30 is pre-planned) |
| **Tier S** | complete PO + GRN + invoice sets with lines, links and expected status | linking, reconciliation, export, end-to-end | 2 → **8–10 sets**; split by set: dev = the existing 2 + 2 new, **held-out ≥ 4 new sets** |

Rules:
- Tier H and the held-out sets of Tier S are **never used for tuning**. They are scored only at gates (PH4 and PH7) and for the escalation decision; each look is recorded in the decision log. A held-out item that influenced a rule or prompt is retired from evidence.
- Split by vendor as well as by document: at least one third of Tier H invoices come from vendors absent from DEV-CORE, and reports show seen-vendor and unseen-vendor rows separately.
- **Split unit = document identity (D83).** One truth `document` entry per physical document; every copy, re-scan, re-upload or photo of it is a `source` of that entry and inherits its `split`. The split is assigned to the document before any system run, so copies of one document can never fall on both sides; the loader rejects a document whose sources disagree, and a pre-freeze identity check (system fingerprint, D55, plus a manual look at near matches) merges entries that turn out to be the same physical document. Per-field `n`, Wilson bounds and gates use unique documents (§2.5); "with copies" counts are printed only as extra information.
- Composition quotas for Tier H (minimums, owner may adjust, O-12): digital text PDF 10; clean scan 15; tilted or rotated scan 10; phone photo or low-DPI 5 if they occur in production; invoices with handwritten marks 10; marketplace 5; three or more pages 10; dense tables (≥ 15 rows) 10; supplier without GSTIN 3; carrying a printed PO ref ≥ 73 if a per-field claim on references is wanted (otherwise references are reported as counts only). Tier S must contain: a partial delivery, two GRNs for one PO, two invoices for one PO, an exact duplicate and a re-scan, an invoice without a PO number, a cross-vendor same-reference collision, a handwritten-reference case, a bundle file, and a marketplace invoice.
- **Targeted fill (owner, D84).** Beyond the quotas, batches target invoices with a printed PO ref and with a printed buyer GSTIN until each reaches 73 unique documents; per-field unique `n` is tracked after every batch (WI-D5 step 8).
- Labelling protocol is in WI-D5. Labels are produced blind to the system output for Tier H.

### 5.2 Metric definitions

"Exact match" never applies look-alike folding: `O` ≠ `0`, `I` ≠ `1`. The harness (WI-EV3) aligns its scoring with these definitions and records any difference it finds in the existing bench.

| ID | Metric | Numerator / denominator | Matching and normalisation | Where |
|---|---|---|---|---|
| MT-1 | Field accuracy (per field, per document type) | correct / truth-present fields (key present, not null); report wrong and missing separately | IDs: canonical form, uppercase, no folding; GSTIN 15 chars exact; dates ISO; refs: canonical PO number | offline + e2e |
| MT-2 | Required-field completeness | critical fields with any value / critical truth-present fields | presence only | offline + e2e |
| MT-3 | Document exact-correct | documents with every critical header field exactly correct (header variant) / documents; full variant also needs every truth line matched and 0 spurious lines | as MT-1, MT-4 | offline + e2e |
| MT-4 | Line-item accuracy | per cell (amount, quantity, rate, description, HSN, UOM) correct / truth cells; line recall = truth lines matched / truth lines; spurious = extracted lines unmatched | row match by amount, then quantity/rate tie-break (bench rule); description ratio ≥ 0.85 after casefold and whitespace collapse | offline + e2e |
| MT-5 | Numeric accuracy | money values equal / money truth values | Decimal equality to 0.01; quantity exact after UOM canonicalisation; a second row reports "within ±₹1" (arithmetic-consistent) | offline + e2e |
| MT-6 | Link precision / recall | precision = correct automatic or confirmed links / automatic links; recall = expected links found (auto or suggested) / expected; auto and suggested reported separately | truth pair (source, PO) | offline + e2e |
| MT-7 | Reconciliation decision accuracy | cases whose status equals truth / scored cases, with a confusion matrix | Tier S only | e2e |
| MT-8 | Critical false-accept | auto-accepted (reached L1 without a person) values that are wrong / auto-accepted values; also per document | critical fields; HZ-5 | offline + shadow + e2e |
| MT-9 | False-reject | (a) correct candidates dropped or excluded by a check / correct candidates; (b) documents sent to review whose critical fields are all correct / documents | review burden | offline + e2e |
| MT-10 | Unsupported-value rate | answered values not found in folded OCR text / answered values (an upper bound on invention: OCR can miss real values) | D74: 11/256 for v4 | offline |
| MT-11 | Duplicate detection | duplicates caught / expected duplicates; false duplicates; extra documents | existing bench | offline + e2e |
| MT-12 | Excel export correctness | workbook cells equal expected / cells checked (Summary, case rows, ledger columns) | Tier S expected values; Home KPI = Summary parity (exists, D28) | e2e |
| MT-13 | End-to-end app accuracy | MT-1/4/6/7/12 measured on the **persisted** current version through the running stack | upload → ingest → Qwen → merge → map → reconcile → export | e2e |
| MT-14 | Trusted-but-wrong | wrong values at confidence ≥ 0.98 | exists | offline |
| MT-15 | LLM coverage | documents with a usable Qwen answer / eligible documents | includes unavailable and cap failures | offline + e2e |
| MT-16 | Latency and tokens | p50/p95 per call and per document; tokens in/out | recorded, not judged (D72) | offline |
| MT-17 | Repeat and epoch variance | fields whose normalised value changes between reads on the same server epoch | cross-epoch changes reported separately | offline |

*Offline* = bench from stored or fresh server reads. *E2E* = the running stack after AC-INT1 (RG-9).

### 5.3 Regression gate (run on every change that can touch extraction, merge, linking or reconciliation)

1. **Flag-off identity.** Default bench `report_hash`, `vision_hash` and `consensus_hash` equal the frozen pre-change values (synthetic guard `d054b29d…/822e366e…/cca27c5e…` and the real-set hash `09115aa2…` for the rules path).
2. **Hard zeros** HZ-1 … HZ-6: bench exits non-zero on any.
3. **Offline suites**: unit, property, scenarios, parsers; `lint-imports`, core purity, rules hash, engine/consensus/parser version guards (§4.12).
4. **DEV-CORE paired comparison** against the frozen baseline: any critical-field group lower by ≥ 2 fields, or any new accepted-wrong value, blocks the change until explained.
5. **Held-out** only at PH4/PH7 or the escalation decision.
6. Server-dependent runs carry a same-day control and the canary digest; runs from different server epochs are not compared.
7. The report prints counts with denominators, never document text.
8. Reports carry the provider and print Qwen and Gemini side by side on the same truth set and splits (per-field table with n); a provider without its own measured numbers is shown as "suggestions only".

### 5.4 What can be proven at each truth-set size

| Claim | ~5 complete sets only | + Tier H (80) | + Tier S (8–10 sets, ≥ 4 held-out) |
|---|---|---|---|
| Hard zeros hold | point evidence; "0 in N" with bound 3/N | same, N larger | same + links |
| Per-field accuracy ≥ 95% (critical IDs, totals) | no (unique n ≤ 24; n ≤ 59 only if copies were counted) | yes at zero errors (n = 80), else escalate to 110 | yes |
| Pooled critical-field ≥ 97% | no | yes if ≥ 125 pooled values with zero errors, or ≥ 185 with one | yes |
| Auto-accept thresholds (DP-L1) | no | per field type once n ≥ the DP-L1 requirement | yes |
| Line accuracy ≥ 95% | only the dev lines (n ≈ 110–120 per cell) | not covered (header-only) | dev + Tier S lines |
| Link recall ≥ 95% | no (10 pairs) | no | indicative (≈ 20 pairs); counts reported |
| Reconciliation and export accuracy | no | no | yes, on Tier S |

---

## 6. Phases

Phases PH1, PH2, PH2b, PH3 … PH7 (PH2b was inserted by D80 so the numbering used in D78 and D79 stays valid). Each phase lists entry and exit criteria. Interim numbers marked *pre-registered* were accepted by the owner on 20 Oct, before any PH2 run, and are never fitted afterwards; the contract numbers (§2.2) are the final gate. Every phase ends with: the regression gate (§5.3), a decision-log entry, and an update of §0.

| Phase | Entry | Tasks | Exit criteria (numbers) |
|---|---|---|---|
| **PH1 Measurement and integrity** | this plan approved | WI-EV3, WI-D5 (protocol + first batch), WI-T1, WI-INT0 | (1) one gate command reproduces 302/373 (rules, `scanfix-truth-2.json` hash `470682236b6d`) and 275/313 (Qwen, `ev2-e5-v4-ctl.json` answers, `--network none`) with identical hashes; (2) MT-1 … MT-17 implemented with unit tests on synthetic truth; (3) WI-T1 closed; (4) WI-INT0 root cause written with a reproducible check; (5) labelling protocol accepted by the owner and the **first 25** Tier H invoices double-labelled with ≥ 95% agreement on critical fields (disagreements adjudicated); the remaining Tier H collection is staged with no date commitment (O-12) |
| **PH2 Reading quality and provider evaluation** (dev set) | PH1 gate command; server reachable; per-run approval recorded; for Gemini arms: the owner's go, a separate free-tier bench key (`BENCH_GEMINI_KEY`) and a requests-per-day plan (D83); the masked dev set may go to the free bench key (owner, D84) | WI-BE8, **Gemini arms E1–E4 (moved from PH6)**, WI-V1, WI-V2, WI-Q1, WI-Q2, WI-Q3, close WI-EV2 (side by side), WI-OPS1 | **pre-registered 20 Oct (Qwen)**: all 39 dev PDFs have a usable answer (0 lost to the output cap); dev headers ≥ 92% (≥ 288/313) with accepted-wrong 0; scan-page line amount ≥ 85% (≥ 105/123), quantity and rate ≥ 80%; WI-Q1 verdict recorded against its target; marketplace 5/5 with 0 side effects; handwritten recall reported; WI-EV2 matrix filled **for Qwen and Gemini side by side with n**; arbitration `arb.v1` drafted from dev per provider. The pre-registered numbers are Qwen's; Gemini arms are reported against the same thresholds and need no pass for a provider to stay selectable (it then gives suggestions only); the owner may pre-register Gemini-specific thresholds before the first Gemini run. **Restated on unique documents (D83, §3.2a): the percentages are unchanged, the counts follow the new unit.** Dev headers ≥ 92% means ≥ 126/136 unique header values (was ≥ 288/313 with copies) and "accepted-wrong 0" is read as 0 in 136 (rule-of-three bound ≤ 2.2%, was ≤ 1.0% on 313); scan-page line amount ≥ 85% means ≥ 40/47 (was ≥ 105/123), quantity ≥ 80% means ≥ 36/44 (was ≥ 92/114), rate ≥ 80% means ≥ 36/45 (was ≥ 95/118); marketplace is 2/2 documents (was 5/5 reads); handwritten recall is reported over 5 documents (was 18 reads; D87: 10 documents and 57 reads after the typed references were added, gate not re-registered). A (document, field) counts correct only if correct in every copy read (§2.5). These stay point gates on dev: no lower bound is claimed from dev (the best possible is 92.4%, §3.2a). Observed values on the unique basis (D84, §3.2b): headers 101/136 (74.3%) against ≥ 126/136, line amount 27/47 against ≥ 40/47, quantity 23/44 against ≥ 36/44, rate 25/45 against ≥ 36/45; accepted-wrong 0 in 101 accepted values |
| **PH2b Provider platform** (connect, select, secure) | PH1 done; WI-PV1 design reviewed against the reuse map (§4.15.1) | WI-PV1 … PV8, WI-BE9a, WI-EXP1, WI-FE1a, WI-FE1b, WI-FE3 | on a disposable stack (PG + mocked providers): an admin connects, tests, rotates and disconnects each provider from the UI; Connected only after a passing test; a non-admin is refused; the secret appears in no API response, log, error, report, export or audit row; SSRF/allowlist suite passes; disconnect mid-run leaves no partial rows; switching the default or rotating a key leaves existing readings unchanged; provenance shows on the document page, review screen and export; the kill switch test passes; reuse check: no second sealing, consent or allowlist mechanism exists |
| **PH3 Reliability and deployment ladder** | PH2b exit; AC-INT1a complete (backup, tested restore, dry-run 0024→head on a copy, head including the provider-platform migration); **WI-PV5 done and verified (D83 deployment gate)** | WI-OPS2, WI-OPS3, WI-BE9b, WI-BE9c, AC-INT1b, AC-LIVE-S, WI-D3 | RG-1 … RG-6 verified on the running stack in shadow; WI-INT0 closed; canary running ≥ 7 days with an epoch record; Qwen shadow ≥ 14 days **and** ≥ 100 shadow comparisons (a back-fill of the 40 live documents counts); shadow writes 0 rows to documents (test); **the live Gemini behaviour is unchanged while Qwen shadows (before/after counts of its requests and no Gemini setting change in the audit log)**; outage drill (provider blocked ≥ 10 min): documents flagged, no queue pile-up, hard zeros 0 |
| **PH4 Merge** (per provider) | PH2 and PH3 exits; DP-L1 numbers decided; Tier H ≥ 80 labelled and frozen | WI-G3, WI-BE7a/b/c, WI-FE2, AC-LIVE-M | for **each provider whose merge is to be enabled**, on held-out Tier H: each claimed critical field has n ≥ 73 and a 95% lower bound ≥ 95% (zero errors, or the +30 escalation); pooled critical ≥ 97%; `critical_false_accept` 0; flag-off bench hash identical; merge and auto-verify enabled only for the provider and field types that pass, everything else stays suggestions; the default provider after the merge stage is chosen by the owner from the side-by-side numbers (O-22); lines and links: target met or gap reported with n. **`n` is unique invoices (D83):** Tier H means 80 *distinct* documents (a copy or re-scan adds nothing), and the pooled-critical gate counts values from unique documents and also reports the share of documents with every critical field correct (MT-3) with its own n. At the presence rates seen in the current 10 invoices (**very uncertain**: about 40% carry a printed PO ref and about 60% a buyer GSTIN), `n ≥ 73` for those fields would need about 183 and 122 invoices unless collection is targeted (quotas in §5.1) |
| **PH5 Matching and identity** (parallel with PH2–PH4) | none | WI-D4, WI-D6, AC-8b, AC-9b, AC-10a/b | links ≥ 7/10 on dev with 0 false auto and 0 false suggestions (WI-D4); then on held-out Tier S: recall reported with counts (target 95%), false auto 0; AC-8b extras and missed duplicates at an owner-accepted level; B6/B10 closed |
| **PH6 Hygiene and optional** | none | WI-EV0, AC-12, WI-QR1, AC-7c, FP DP-3, optional manual "compare both" view (two readings side by side, never merged or voted) | each item closed or recorded as skipped by the owner |
| **PH7 = AC-11 Real-set E2E and Phase 4 exit gate** | PH4 and PH5 exits | AC-11 | hard zeros 0 at end-to-end level; a full real month or Tier S through the running app with MT-13 reported; export parity MT-12; the main plan's Phase 4 exit gate items (SCN-01 … 20, `make demo`, real-month demo, carried decisions, [`gates/phase-4.md`](gates/phase-4.md)) closed or carried with named owners; every target met or its gap reported per field with n |

---

## 7. Task cards

Card fields: **Status** · **Size** (one session unless split) · **Deps** · **Owner approval** (Y = needs an explicit go, N = no). Done work is not carded; it is in §13 with its decision ID. Every card that calls the server needs a go and a budget logged in the decision log first (vLLM budgets are set by the engineer, one go per run; Gemini budgets stay with the owner). No task commits, pushes or opens a PR unless the owner asks.

### 7.1 Measurement

#### WI-D5 — Truth set v3: Tier H and Tier S, labelling protocol, dev/held-out split
**Open** · L (owner time; several sessions of tooling) · Deps: none · Approval: **Y** (collection and labelling are the owner's)
- **Problem / evidence:** 26 truth documents, 2 complete sets: four critical fields (doc no., date, GSTIN, refs) cannot be proven ≥ 95% (§3.2, §3.4); no held-out data exist; the old targets (15–20 sets, then ~5) conflict (O-1 replaces both).
- **Approach:**
  1. Counts-only tally of truth entries by type (closes the "~18 invoices" unverified item).
  2. Truth format `reconbench-accuracy-truth/v3`: v2 plus `tier` (`H`/`S`), `split` (`dev`/`heldout`), per-source `scan_type` (`digital|searchable_scan|scan_clean|scan_tilted|scan_rotated|phone_photo|low_dpi`), `handwritten`, a pseudonymous `vendor_ref`, per-line `uom` (Tier S); Tier H documents are header-only (lines not scored). The bench accepts v2 and v3.
  3. Collect to the quotas in §5.1 (80 invoices; 8–10 sets).
  4. **Labelling protocol.** Labeller A (Finance) enters each Tier H field from the page image in a form that shows no extraction output (no prefill; the older "prefill then correct" habit invites automation bias and would inflate accuracy). Allowed entries: value as printed, "not present", "illegible". Labeller B independently labels the critical fields of the first 25 invoices in full, then a random ≥ 25% sample, plus every field where the system and label A disagree. A–B disagreements are adjudicated by the owner and the agreement rate is recorded (target ≥ 95% on critical fields). "Illegible" fields leave the denominator and are counted separately. A label error found later is corrected by a logged change and every dependent result is re-run. Tier S keeps the v2 process (links, status, lines) with the same two-person review (main plan 6.1).
  5. **Split.** Held-out membership is drawn by vendor and a recorded random seed **before** any system run; ≥ 1/3 of held-out invoices come from vendors absent from DEV-CORE; Tier S held-out ≥ 4 new sets. The file SHA-256 is written to the decision log when frozen; a later change needs the owner's approval and retires dependent evidence. **Identity rule (D83):** the split belongs to the document, not the file; copies, re-scans, re-uploads and photos are `sources` of one `document` entry and share its split; the loader rejects a document whose sources disagree. One audit of the existing 26 entries for the same physical document entered twice comes before the freeze.
  6. **Escalation.** If a claimed field shows ≥ 1 error on held-out, collect +30 invoices and re-evaluate (n = 110 for a 95% bound with one error).
  7. **Counting (D83).** Every per-field `n` is counted on unique documents; "with copies" is reported only as extra information. A (document, field) is correct only if correct in every copy read. PH2/PH4 gates restated on this basis in §6.
  8. **Targeted collection and tracking (owner, D84).** Tier H batches deliberately include invoices that carry a printed PO ref and invoices that print the buyer GSTIN (the two fields that would otherwise need about 183 and 122 random invoices, §6 PH4), in addition to the §5.1 quotas, so those fields reach `n ≥ 73` unique documents (110 with one error) without collecting 120–180 random invoices. After every batch the unique `n` per claimed field (doc no., date, supplier GSTIN, buyer GSTIN, taxable, each tax, grand total, printed PO refs) is recomputed with `scripts/truth_tally.py` (extended to print header presence per document type and tier) and written to the decision log with the date; collection continues until each claimed field reaches its `n` or the owner stops it. Targeted batches skew presence rates, so they are labelled as targeted and never used to estimate how often a field occurs.
- **Files:** `scripts/accuracy_bench.py` (`load_truth`), new `scripts/truth_tally.py` (counts only), `work/accuracy-truth/` (private).
- **Acceptance:** loader tests on synthetic v3 files; Tier H ≥ 80 invoices with quotas met; double-label agreement report; split frozen with its hash; Tier S ≥ 8 sets with the §5.1 scenarios. The loader rejects a document whose sources carry different splits; the tally prints unique-document counts per field beside the with-copies counts (D83).
- **Risk / rollback:** labelling effort; truth stays in `work/`. **Impact:** makes every target claimable (§5.4).
- **Progress (20 Oct):** step 1 done (`scripts/truth_tally.py`, tested on synthetic truth; the real tally is in §3.4). Prepared for step 4: the private protocol `work/accuracy-truth/tier-h/PROTOCOL.md`, the label-sheet template `labels-template.csv` and `scripts/truth_agreement.py` (labeller A versus B, counts only, `--min-critical 0.95`; unit-tested). Steps 2 (truth format v3), 3 (collection), 5 (split) and 6 (escalation) are still open; PH1 collects the first 25 invoices.
- **Progress (21 Oct, D86, D87):** the 15 new invoice PDFs added **no new unique invoice** (all 8 originals and 7 re-scans were already S01 to S08 and RESCAN-1 to 7). Tier S additions (owner-confirmed): pool links invoice to PO 6, invoice to GRN 7, GRN to PO 6 (+1 GRN citing an external PO); **complete sets 8 with the pool**; **7 re-scan pairs**; **16 typed handwritten references** (truth fields for WI-Q2; S01's second one `unverified`); S06 handwritten PO corrected to 08-081. **Tier H still needs about 80 distinct invoices**; the dumps cover August 2026, so **new August invoices from vendors that appear in the dumps** are the most useful next collection. The stored reports are stale for the handwritten metric only (D87).

#### WI-EV3 — Metric suite and regression gate
**Open** · M–L (split in two sessions if needed: metrics, then gate/export) · Deps: none · Approval: **Y** (running local benches, no network)
- **Problem:** MT-2, MT-3, MT-5, MT-8, MT-9, MT-12, MT-13, MT-15, MT-17 are not computed; per-field tables with Wilson bounds are hand-built; no single gate command.
- **Approach:** implement MT-1 … MT-17 as defined in §5.2; add `critical_false_accept` (HZ-5) and server-epoch fields to report meta; add `scripts/accuracy_gate.py` (+ `make accuracy-gate`) that runs §5.3 steps 1–4 and prints the §3.2 table; export metric reads the generated workbook and the Tier S expected values; record any difference between the bench's current scoring and §5.2.
- **Files:** `scripts/accuracy_bench.py`, `scripts/accuracy_vision_eval.py`, new `scripts/accuracy_gate.py`, `Makefile`, tests under `backend/tests/unit/test_accuracy_*.py`.
- **Acceptance:** PH1 exit (1) and (2); default report hash unchanged when no new flag is given; synthetic tests for every metric (including Wilson, rule of three).
- **Risk / rollback:** none to runtime (scripts only); revert files. **Impact:** a trustworthy pass/fail for every later task.
- **Both providers (D80):** every table carries a provider column and is printed side by side for Qwen and Gemini on the same truth set and splits, per field with n; `accuracy_gate.py` prints "suggestions only" for a provider without its own held-out numbers; it also writes a counts-only `provider-measurements.json` that an admin-run command (`python -m reconbench_app.ai.import_measurements`) loads into `provider_measurement`, which Settings reads for its note.
- **Spend cap (owner, 20 Oct; dormant on the free tier, D83):** the bench gets `--max-spend-inr` (reads the configured price and INR/USD rate), stops when the next call would pass the cap, and logs actual spend from returned token counts per run. It applies only to a billed bench key and is not used while the bench key is free-tier.
- **Request budget (D83, free tier):** `--max-requests N` and `--max-requests-per-day N` (the daily value comes from configuration or from the first daily-quota error, never a hard-coded number). Before a run the bench prints the estimated requests (pages × arms × repeats × calls per page) and stops cleanly on a daily-quota error; the next day's run continues from the answer cache. Each read records its date and the model string returned.
- **Unique-document counting (D83):** every per-field row prints `n_unique` (used for the Wilson bound, the max-LB column, every gate and DP-L1) and `n_copies` (extra information); per (document, field) the strict rule of §2.5 applies; `accuracy_gate.py` refuses to state a claim on `n_copies`. Tests: a synthetic truth with one document read in three copies (one wrong) proves `n_unique`, the strict rule and that the Wilson bound uses `n_unique`.

#### WI-EV0 — Offline re-score of stored Gemini answers (optional)
**Open, low priority** · S–M · Deps: none · Approval: Y (local run)
Fills the Gemini per-scan-type and per-cell gaps (audit §4) from `work/accuracy-cache/vision`. Useful as a cheaper Gemini baseline before the fresh E1–E4 reads (PH2); the stored answers need `--vision-cache-compat v1` (D63) and may no longer map after D59–D61. Stop and report if 0 answers map.

### 7.1a Provider platform: connect, select, secure (new, D80; runs before AC-INT1)

Reuse first: every card below builds on the code in the reuse map (§4.15.1). If a card finds that an existing mechanism already covers its need, it records that and builds nothing.

#### WI-PV1 — Connection model, encrypted credentials, per-provider settings (migration)
**Done 20 Oct (D82), pending owner review** · M · Deps: none (design reuses D65) · Approval: Y (schema change; applied to the live database only inside AC-INT1)
- **Reuse:** `ai_provider_config`, key sealing (`ai/key_seal.py`), the `secret` table, masked hints, session or saved storage, consent fingerprints. No second secret mechanism.
- **Gaps:** the Gemini key sits on `ai_setting`; no persisted connection state or error reason; no consent, cost cap, auto-verify list, breaker or `apply_mode` on the card; `reader_strategy`/`primary_reader` cannot express "Gemini live and Qwen shadow"; no per-upload override; the allowlist is env-only; no place for counts-only provider measurements.
- **Approach:** one migration (next free revision after `0027`; re-check at the start that no other session added one): on `ai_provider_config` add `connected_at`, `last_error_code`, `last_error_at`, `consent_fingerprint`, `consent_accepted_by`, `consent_accepted_at`, `monthly_cost_cap_inr`, `cap_warn_pct`, `doc_cost_flag_inr` (Gemini; owner values 2,500 / 80 / 20, O-24), `auto_verify_field_types text[]`, `apply_mode` (suggest/merge), `breaker_state`, `breaker_changed_at`, `key_rotated_at`; move the Gemini key reference to its card (keep `ai_setting.key_secret_id` readable until WI-BE9a lands); on `ai_setting` add `extraction_provider`, `assist_provider`, `shadow_provider`, `allow_provider_fallback boolean default false`; `provider_override` on the upload batch (or file; chosen after reading `ingestion.py` and the metadata); table `ai_endpoint_allow` (workspace, normalised URL, added_by, added_at; append-only); table `provider_measurement` (provider, field type, n, correct, wrong, lower bound, split, report hash, epoch, date; counts only). Backfill preserves today's behaviour: Gemini rows keep `extraction_provider` and `assist_provider` = gemini; an old `shadow` strategy becomes `shadow_provider` = the old shadow reader; `reader_strategy`/`primary_reader` stay readable for one release.
- **Behaviour change to record:** the live `auto_verify_field_types` (five types, workspace-wide, set before any measurement) becomes per provider and starts **empty**. No auto-verified value exists today (0 `consensus_run` rows), so no document changes.
- **Files:** migration, `db/metadata.py`, `db/schema_contract.json`, `schema-errata.md`, `ai/settings_service.py`, `ai/consent.py`.
- **Acceptance:** PG-SAFETY set on a fresh cluster (migration safety from the previous revision, drift, catalog, indexes, decisions, table count); a migration test rewinds to `0027` with legacy rows and checks the backfill, byte-identical legacy consent fingerprints, grants (the app role cannot update or delete the append-only table), and that no new column can hold key material.
- **Risk / rollback:** schema surface; fixed-forward, rollback = restore (AC-INT1a). **Impact:** the data model for everything in PH2b.
- **As built (D82):** migration `0028_provider_connections` (53 tables after it), metadata mirror, regenerated schema contract, errata entry, `test_ai_provider_connections_migration.py` (10 tests), CI previous-schema revision `0027`. **Scope notes:** (a) the cap fields are `monthly_cost_cap_inr`, `cap_warn_pct`, `doc_cost_flag_inr` (rupees, `d_amount`); the Gemini cards were backfilled with the owner's 2,500 / 80 / 20; (b) the CHECK that only a `vllm` card may reference a key is dropped, but the Gemini key has **not** moved: `ai_setting.key_secret_id` stays in use until WI-PV2 moves it, so no secret is referenced twice; (c) no application code reads the new columns yet, so `auto_verify_field_types` on the card is empty data: the code that decides auto-verification still reads the workspace-wide list, and the switch to the per-provider list is part of WI-PV5 (acceptance added there); (d) no API or OpenAPI change.

#### WI-PV2 — Connect, test, disconnect, rotate, health
**Done 20 Oct (D85), pending owner review** · M · Deps: WI-PV1 · Approval: N (code); Y for any real provider test (none run)
- **Reuse:** `POST /ai/test/provider {provider, kind}`, the ai-worker as the only caller of providers (the API never calls a model), `GET /ai/requests/{id}`, `sweeps.provider_health`, `remove_key`, `request_blocker`'s re-check before send and before apply, the audit events, consent preview.
- **Approach:** (1) connect saves credentials (sealed on receipt) and queues a server-side test (Qwen: `GET /models` plus a tiny JSON request with thinking off; Gemini: the existing minimal synthetic text request); (2) state Not connected / Connected / Error with last-checked time and a generic reason code (`bad_key`, `MODEL_UNAVAILABLE`, `PROVIDER_UNAVAILABLE`, `endpoint_not_allowed`, `timeout`; never provider text); Connected only after a passing test, reset by any change; (3) **rotate = test then swap**: the new key is tested first, the **old key stays active until the test succeeds**, and a failed test changes nothing; (4) disconnect deletes the key, records who and when, leaves the selection naming the provider, effective provider none (flag `PROVIDER_DISCONNECTED`); (5) in-flight requests finish or fail cleanly, answers for a disconnected provider are discarded, never half-applied; (6) health covers both providers; **the Gemini health check must neither create billable calls nor waste free-tier quota**: it uses a models call that is not billed or counted against the quota if the pinned SDK offers one (**unverified**, check first); if none exists it runs **at most once per hour**, is counted in the day's request count (and against the cap when the card is declared paid) and keeps the status current; a quota error during a health check updates the quota state (§4.15.9) and is not reported as a broken connection; (7) the egress notice (Gemini) or company-network notice (Qwen) is accepted at connect and stored with the fingerprint, who and when; **for Gemini the admin declares the key's tier (free or paid) and, for a free card, the notice states that Google may use prompts and responses to improve its products and human reviewers may read them (§4.15.9, O-25)**.
- **Files:** `ai/settings_service.py`, `api/ai.py`, `ai/worker/sweeps.py`, `ai/consent.py`, OpenAPI and generated types.
- **Acceptance:** API tests: Connected only after a passing test; failing test → Error with reason; rotate with a bad key keeps the old connection; disconnect mid-run leaves no partial rows; health flips the status within one cycle; consent required before the first send; both providers behave the same through the same endpoints.
- **As built (D85):** migration `0029_provider_key_move` (`ai_provider_config.key_tier` free/paid default free, `pending_key_secret_id`; the Gemini key pointer moves to the card with verify-then-clear and a tested `downgrade()`; the live Gemini card migrates as tier = free). Reading stays dual for one release (`ai_setting.key_secret_id` is read when the card has no key). New `ai/connection_state.py` (state derived from stored facts: Not connected / Connected / Error, generic reason codes, test-result and rotation transitions shared by the API and the worker), `ai/provider_notice.py` (per-provider notice and fingerprint; the free-tier notice states the data use), `ai/provider_connection.py` (service). Endpoints, all `ai.manage`, same for both providers: `GET /ai/providers/{provider}/connect-notice`, `POST …/connect` (202), `POST …/rotate` (202), `POST …/disconnect`; state and tier appear in `GET /ai/settings` under each provider (`connection`, `tier`, `consent`, `health`). Worker: a test result updates the card (`connected_at` / `last_error_*`); rotation tests use the candidate key and swap on success (a failure removes the candidate); `provider_health` updates the state for the company server; a new `gemini_health` sweep sends at most one synthetic test per hour per tested Gemini card; `sweep_once` drops candidates no test settled after an hour. Tests: `test_ai_provider_key_move_migration.py` (4), `test_ai_provider_connections_api.py` (22), `test_ai_provider_connection_state.py` (4).
- **Not done in WI-PV2 (by design or deferred):** the card-level consent is recorded and compared with the current notice but is **not yet a send gate** (the workspace AI consent still authorises sends; per-provider enforcement is WI-PV5 and WI-PV8 item 9); no admin check (WI-PV3); no UI (WI-FE1a/b); a test that is *skipped* (gate off, settings changed) leaves the card Not connected rather than Error; the Gemini health check is a synthetic request because a non-billed models call is **unverified**, and test/health requests are ledger rows without `ai_call_log` rows, so the day's request count for the quota (WI-PV7) must read `ai_request`; quota state storage is open (WI-PV7); for Gemini with AI on, connecting with another model is refused (`model_change_via_settings`) so the workspace consent is asked again through the settings route.
- **Risk / rollback:** revert the endpoints; cards fall back to D65 behaviour. **Impact:** an admin can connect either provider without touching the environment.

#### WI-PV3 — Admin permission and audit
**Done (D91), awaiting owner review** · S–M · Deps: WI-PV1, O-18 · Approval: Y (decision O-18; it departs from the P0 policy that every user is a manager)
- **Verified gap:** `ai.manage` belongs to role `manager`, but `resolve_effective_role` maps every persisted user to `manager` (`authorization.py:93-99`), so today any logged-in user can connect, rotate or disconnect a provider.
- **Approach (O-18):** enforce the stored role for `ai.manage` only; the bootstrap user is the first manager. Routes keep `@requires("ai.manage")`. Audit events: existing `ai.provider_config_save`, `ai.test_requested`, `ai.key_remove`, `ai.reader_strategy_change`, `ai.apply_mode_change`; new `ai.provider_connect`, `ai.provider_disconnect`, `ai.provider_rotate`, `ai.default_provider_change`, `ai.shadow_provider_change`, `ai.fallback_setting_change`, `ai.allowlist_change`, `ai.breaker_trip`. Each records who, when, provider and before/after state, never a secret. **Placement (D84):** this card builds it. It is a PH2b task, so it lands before AC-INT1b, the first live change (AC-INT1a changes nothing live). Until it lands every logged-in user can change AI settings, and the WI-PV2 endpoints (`ai.manage`) are equally open.
- **Acceptance:** a non-admin is refused (403) on every write route and sees a read-only view; an audit row exists for each action; scanning the audit payloads for the posted key finds nothing.
- **As built (D91):** `authorization.py`: `STORED_ROLE_CAPABILITIES = {"ai.manage"}`, `stored_role_grants()`, `visible_capabilities()`; the P0 `resolve_effective_role` is unchanged, so no other P1 check is switched on. `require_ai_manage()` (in `ai/settings_service.py`) reads `app_user.role` and `is_active` on every write and refuses with 403 `ai_manage_required`; it runs first in settings update, key removal, provider test, acknowledge, connect, rotate, disconnect and the offline `POST /ai/test`. Each refusal writes `ai.manage_denied` (actor, stored role, what was attempted; never a value from the request) in its own transaction. Login and session payloads omit `ai.manage` for a non-manager, so the existing Settings and AI cards open read-only (`hasCapability("ai.manage")`); read routes stay open. **Bootstrap user first:** the seed stores the bootstrap user as `manager`. **Last-manager lock:** migration `0030` (deferred constraint trigger on `app_user`): at COMMIT a workspace that had an active manager keeps one (multi-row demotion refused, a swap inside one transaction allowed, whole-workspace removal allowed, a per-workspace advisory lock serialises concurrent demotions). Contract: 53 tables, 40 enums, **31 triggers, 38 routines**, 176 FKs, 268 ACLs, 7 default privileges.
- **Not built:** audit events for writes that do not exist yet (default/shadow provider, fallback, allowlist, breaker: WI-PV4 … PV7 add them with their endpoints); a user/role management UI or API (`users.manage`); the web is unchanged (no read-only banner beyond the existing gating).
- **Deployment note (D91):** every existing user's stored role defaults to `manager` (column default), so enforcement changes nothing on a database until non-admin users are given another role; there is no role-management path yet. Setting the live roles is an owner step inside AC-INT1b, after backup.

#### WI-PV3b — Role assignment (new, D92)
**Open, do not build yet** · S · Deps: WI-PV3 · Approval: Y (build) · Placed before AC-INT1b
- **Problem:** WI-PV3 enforces `ai.manage` from the stored role, but every existing user is stored as `manager` (column default) and there is no way to change a role except raw SQL.
- **Approach:** a management CLI command (not raw SQL) with which an admin sets one user's role (`manager`, `ap_executive`, `auditor`) and active flag in one workspace. It runs through the last-manager lock (migration 0030; a refused change prints the `last_manager` reason and changes nothing), writes an audit row (`user.role_change`: actor, target user, before and after role and active flag; never a password or hash) and switches on no other P1 check. A dry-run mode prints what would change.
- **Tests:** set a role; deactivate and reactivate; refusal on the last manager; the audit row and its contents; unknown user or workspace; dry run changes nothing.
- **Use in AC-INT1b:** after the backup, AC-INT1b uses this command to give non-admin users their real roles; the live roles are the owner's list.

#### WI-PV4 — Endpoint allowlist: deployment ceiling plus workspace entries
**Open (new)** · S–M · Deps: WI-PV1 · Approval: Y (O-19, O-20)
- **Reuse:** `ai/endpoint_policy.py` and `DeploymentGate.endpoint_allowed`.
- **Approach (O-19):** `RB_AI_VLLM_ENDPOINTS` becomes the ceiling; the admin adds workspace entries in the UI (`ai_endpoint_allow`, audited); an entry outside the ceiling is refused. Loopback, link-local, private-metadata and cloud-metadata addresses are refused unless the ceiling lists them; the scheme must be https unless the ceiling marks the host as an approved internal host (the company server is plain http on the VPN); a URL outside the company network is refused (O-20). The check runs on save and again before every request; redirects are never followed.
- **Acceptance:** SSRF suite (metadata IP, loopback, link-local, IPv6 literal, user info, query, longer and shorter path, other port or scheme, dot and encoded-dot segments, a redirecting server) all refused; an entry outside the ceiling refused; an entry inside accepted and audited.

#### WI-PV5 — Provider registry, workspace defaults, per-upload override, live plus shadow
**Open (new)** · M · Deps: WI-PV1, WI-PV2 · Approval: N
- **Reuse:** `reader_for_provider`, `VisionReaderInfo`, `AiHookEnqueuer`'s per-provider gate and key checks, `ai_request.provider`, cache key v2, shadow requests (D66).
- **Approach:** resolution at enqueue: per-upload override → `extraction_provider` / `assist_provider` → none (rules + OCR, flagged). The selected provider must be Connected, consented, inside the deployment gate and have a registered reader for the hook; otherwise the document takes rules + OCR with a visible flag. `allow_provider_fallback` (O-21, default off) is the only route to the other provider and needs consent for that provider's data destination. One live provider and at most one shadow provider can run for a document version: live writes suggestions or versions per the provider's `apply_mode`; shadow writes only `reader_comparison`; their answers are never merged or compared in the app. `RB_VISION_READER` default becomes `all`. The upload API accepts `provider` for a batch; a provider that is not Connected is rejected at upload.
- **Acceptance:** precedence tests; override to a disconnected provider rejected; live + shadow coexistence (shadow writes 0 document rows, live unchanged); changing the default leaves existing readings and versions unchanged.
- **Added by D82:** consensus and the consent fingerprint read the **per-provider** `auto_verify_field_types` (starting empty); the workspace-wide `ai_setting.auto_verify_field_types` is no longer read for acceptance. Acceptance: a Gemini request in a workspace whose legacy list names five types produces no auto-verified field until the Gemini card's own list is set. **Deployment gate (D83):** AC-INT1b waits for this card. Until it is done the code reads the workspace-wide list (live: five types), so deploying earlier would let five field types auto-verify before any measurement.

#### WI-PV6 — Re-run with the other provider
**Open (new)** · M · Deps: WI-PV5 · Approval: N
- **Approach:** `POST /documents/{id}/ai-reread {provider, hook}` (any user who may upload) queues a request for the **current version** with the chosen provider; the answer is stored as an additional reading (request + cache entry) listed by provider; in merge mode it becomes a candidate stored as an alternative, never current. "Use this reading" is the existing versioned correction save (audited, D19 guard) with the usual downstream recompute. Nothing is overwritten.
- **Acceptance:** the current version id is unchanged after a re-run; both readings are visible with provenance; consent, gate, budget and status checks apply to the chosen provider; a manual correction blocks "use" unless confirmed.

#### WI-PV7 — Gemini quota handling, usage and cost cap (cap dormant on the free tier)
**Open (reworked by D83: free tier)** · M · Deps: WI-PV1 · Approval: given for the O-24 values, which stay dormant while the card is declared free; the price must be verified before a card is declared paid
- **Reuse:** `ai_setting.budget_cap_usd`, the price file `RB_AI_PRICE_FILE`, the ledger (`reserve` / `finish`, `budget_stopped`), `ai_call_log` token counts, `GET /ai/usage?provider=`. The cap moves to the Gemini card (WI-PV1: `monthly_cost_cap_inr`, `cap_warn_pct`, `doc_cost_flag_inr`).
- **Cost-cap rules (O-24; dormant unless the card is declared paid, D83):** monthly cap per workspace **₹2,500**; warning in the UI at **80% (₹2,000)**; at **100%** new Gemini calls stop (`budget_stopped`), the document takes rules + OCR with a visible flag, the card shows "cap reached", and **nothing switches to Qwen**; the month is the calendar month in **IST** (reset on the 1st). Usage = input and output token counts returned by the API × the price of the **pinned model `gemini-3.5-flash-lite`**, computed and stored per call (`ai_call_log`); thinking tokens count as output if the API reports them as such (verify with the price page). Another Gemini model is refused for the card until the owner approves it and the cap is re-checked. A single document whose estimated cost exceeds **₹20** is flagged.
- **Prices in configuration (needed only for a paid card, D83):** a price entry per model (input and output per 1M tokens, effective date, source note) and the INR/USD rate live in configuration, never in code, so a price change needs no code change. **The current price must be verified against the official pricing page and recorded in this plan with its date before enforcement is enabled** (§3.7: not yet verified).
- **Quota handling (D83, §4.15.9):** classify 429 `RESOURCE_EXHAUSTED` as per-minute or daily from the error body; per-minute: backoff with jitter, bounded retries; daily: quota state `daily_exhausted` until the next midnight Pacific (computed from the zone), new requests not sent, documents take rules + OCR with the visible `GEMINI_QUOTA_REACHED` flag, **nothing switches to Qwen**; quota status on the Gemini card; limits read from the error, headers or configuration, never hard-coded; the first live 429 confirms the assumed error layout (**unverified**).
- **Tier declaration and notice (D83):** `free`/`paid` declared at connect (the API cannot tell), audited, part of the consent fingerprint; the free-tier notice states the data use (O-25). The tier is stored in `ai_provider_config.key_tier` (migration 0029, D85); where the quota state is stored is still to be decided here.
- **Qwen card:** calls, tokens and latency; USD/INR only if a price is entered.
- **Acceptance:** cap enforcement with a fake ledger and a fake clock: calls stop at 100%, the fallback flag is shown, nothing is routed to Qwen; the warning appears at 80%; the month boundary in IST resets usage; per-call cost equals tokens × configured price; a document estimated above ₹20 is flagged; usage per provider equals `ai_call_log`; an unapproved model is refused. Quota acceptance (D83): a fake provider returning per-minute 429s is retried with backoff and then flagged, not routed to Qwen; a daily 429 stops sending until the computed reset (fake clock across the Pacific midnight, both daylight-saving offsets), flags documents `GEMINI_QUOTA_REACHED` and shows the state on the card; an unclassifiable 429 follows the N-in-M rule; for a card declared free the cap logic never evaluates, and declaring it paid activates it.

#### WI-PV8 — Provider safety test suite
**Open (new)** · M · Deps: WI-PV2 … PV6 · Approval: N
Tests, each on synthetic data: (1) the posted key appears in no API response, log record, error, report, export or audit payload; (2) the allowlist/SSRF set; (3) a non-admin is refused on every write route; (4) disconnect between prepare and apply leaves no partial rows and a consistent state; (5) changing the default, rotating a key or re-reading never alters an existing reading or version; (6) reconciliation results are traceable: every case's AI-sourced inputs resolve to provider, model and prompt through version evidence and the export Provenance sheet; (7) with both gates `disabled` no provider receives a request although connected and selected; (8) a breaker trip on one provider leaves the other untouched; (9) consent is required per provider before the first send; (10) legacy consent fingerprints unchanged.
11. For a card declared paid: at 100% of the Gemini cap new Gemini calls stop, the visible fallback flag is shown on the affected documents, nothing is routed to Qwen, and the 80% warning appears earlier (fake clock, IST month boundary). For a card declared free the cap never evaluates.
12. The Gemini health check makes no billable call and no unneeded quota call, or (if none exists) runs at most hourly and is counted in the day's requests (and against the cap on a paid card).
13. Rotate with a failing new key keeps the old key active; with a passing new key the swap is atomic.
14. A daily-quota 429 stops sends until the computed reset, flags the documents "Gemini quota reached" and routes nothing to Qwen; a per-minute 429 is retried with backoff (D83).
15. The free-tier consent notice states the data use and is part of the fingerprint; declaring the tier is audited; the key never appears in the quota or tier records.
(Items 1 – 10 together cover the secret never appearing in API responses, logs, errors, exports, reports or audit events; refusal of a non-admin; allowlist/SSRF; disconnect mid-run; switching provider never altering existing readings; traceability.)

#### WI-EXP1 — Provenance on the document page, review screen and Excel export
**Open (new)** · S–M · Deps: WI-PV5 · Approval: Y (O-23)
Show provider, model and prompt version for each AI-sourced value (chip on the page and the review screen); add an **additive** `Provenance` sheet to the workbook (run id; one row per AI-sourced field: document, field, provider, model, prompt version, reader-params digest, version id; existing sheets and their cells unchanged). First step: check the export contract in the read-only specifications and stop if an extra sheet would break it. **Acceptance:** export parity (Home KPIs = Summary) unchanged; the Provenance sheet reconciles to the run manifest.

### 7.2 Integration, diagnosis and the live switch

#### WI-T1 — `test_ai_h1::test_ai_first_suggests_for_a_clean_pdf…` fails
**Open** · S · Deps: disposable PG · Approval: N (test-only)
- **Problem / evidence:** failing since before D62 and still open at D66 (D62: "rules-only clean PDF now `needs_verification`"); the test asserts `expected["version"]["needs_verification"] is False` for the rules-only world (`backend/tests/integration/test_ai_h1.py:858`).
- **Hypothesis (unverified):** the test world has no buyer entities, so WI-3 (D59/D60) marks the supplier GSTIN unverified and the document `needs_verification` (`BUYER_ENTITIES_NOT_CONFIGURED`), which is the intended behaviour.
- **Approach:** run the test on a disposable PG; print the document's warning codes and review fields; confirm or refute. If confirmed, either give the fixture's workspace a buyer entity that differs from the synthetic supplier (preferred) or change the expectation with a comment naming D59; no production change. If refuted, find the root cause before touching anything.
- **Files:** `backend/tests/integration/test_ai_h1.py` (and its fixtures).
- **Acceptance:** `test_ai_h1.py` passes (14/14) on PG; root cause and the chosen fix are in the decision-log entry; default bench hash unchanged.
- **Risk:** hiding a real rules-path regression; mitigated by the root-cause step and the bench hash. **Impact:** removes a standing red test from the integration group.

#### WI-INT0 — Why `consensus_run` = 0 on live data
**Open** · S–M · Deps: none for the read-only part · Approval: Y for any run on a copy; **AC-INT1 cannot be called done until this is closed**
- **Problem / evidence:** live DB has 26 `h6_vision` requests with `apply_outcome = applied` (9 Oct), `auto_verify_field_types` set, the consensus code present in the ai-worker image (`ai/consensus.py`, wired at `ai/worker/main.py:83`), and 0 `consensus_run` rows; all 47 page-image rows are purged.
- **Hypotheses:** (a) the 9 Oct containers ran code older than the wiring (images were rebuilt on 16 Oct); (b) `consensus_enqueue` raised inside `begin_nested` and was swallowed (`ai/worker/hooks/vision.py:152-…`); (c) `apply` returned `applied` through the `subject is None or consensus_enqueue is None` branch (`:146-151`); (d) `enqueue_consensus_run` found an existing row or returned early; (e) rows were never created because the answers were not `accepted` (but `applied` was recorded).
- **Approach (read-only first):** read the exact `apply` branches; read `ai_request`/`ai_call_log` columns for the 26 rows (counts and states only); check container log retention; then on a **disposable copy** (AC-INT1a) replay one stored answer through `VisionReadHandler.apply` with consensus wired and observe the run row. Add a regression test and a monitor ("vision answers applied > 0 and `consensus_run` = 0 after N minutes" raises a status).
- **Acceptance:** one hypothesis proven by evidence (not by elimination); a test or monitor in place; in live, the first applied answer after AC-LIVE-M produces a `consensus_run` row (RG-6/RG-7).
- **Risk:** none read-only. **Impact:** tells us whether AC-6 works in the app at all (auto-verified rate on live data is unknown).

#### AC-INT1a — Backup, tested restore and dry-run migration
**Open** · M · Deps: WI-PV1 (so the dry run covers the whole migration chain) · Approval: **Y** (O-8 conditions)
- **Approach:** (1) full backup of the live Postgres (`pg_dump -Fc`) and a copy of the object-store data; (2) **tested restore** into a disposable Postgres (port ≠ 15432) with row counts per table, `alembic_version`, and an application read check compared to live; (3) **dry-run `0024 → head`** (0025–0027 plus the provider-platform migration and any later one) on that copy with the tree's migrations (safety, drift, catalog, index, decision, table-count checks; contract counts: 51 tables, 40 enums, 28 triggers, 170 FKs, 264 ACLs, 7 default privileges at `0027` per D66; the new counts after WI-PV1 are recorded); (4) record durations, the fixed-forward rule (rollback = restore the dump) and the exact image digests to tag as the previous state; (5) read the effective `RB_AI_EXTERNAL_CALLS` value.
- **Acceptance:** restore matches live counts; dry-run passes all checks; written runbook in the decision log. No live change.
- **Deployment gate (D83):** this card changes nothing live. The deployment itself is AC-INT1b, which does not start until WI-PV5 is done and verified.
- **Risk:** a backup taken while another session writes; mitigated by stating the time and re-checking counts. **Rollback:** discard the copy.

#### AC-INT1b — Rebuild, migrate, configure, verify the ladder in shadow
**Open** · M · Deps: AC-INT1a, WI-INT0 root cause, WI-OPS1, **PH2b exit (WI-PV1 … PV8, WI-BE9a, WI-EXP1, WI-FE1a/b, WI-FE3)**, **and, as a hard deployment gate (D83), WI-PV5 and WI-PV3 done and verified (D84)** · Approval: **Y**
- **Deployment gate (D83):** no rebuild, no migration of the live database and no container swap before WI-PV5 (auto-verification reads the per-provider lists, which start empty) **and WI-PV3 (admin enforcement, O-18, D84)** are done. The live workspace-wide list holds five types; the code in the images now reads that list.
- **Approach:** announce a window (other sessions share the containers); tag the previous image IDs; build from the approved tree and record its snapshot hash; the `setup` service applies the pending migrations (`0025–0027` and the provider-platform one); set the deployment gates once (`RB_AI_EXTERNAL_CALLS` already set for Gemini; `RB_AI_VLLM_CALLS=synthetic_only`, then `enabled` after the real-page go) and the allowlist **ceiling** `RB_AI_VLLM_ENDPOINTS`; forward both gates, the ceiling and `RB_VISION_READER=all` in `compose.yaml` (they are not forwarded today); confirm whether `RB_AI_VLLM_BASE_URL` is still needed; **connect the Qwen provider from the Settings UI** (no environment edit for the connection itself); the owner creates the first release tag (§4.12); verify RG-1 … RG-5 with commands and file evidence; hold the shadow switch for AC-LIVE-S.
- **Acceptance:** all five rungs evidenced; `lint-imports`/schema checks green on the live DB; row counts of documents and versions unchanged; Gemini's live behaviour unchanged (its card, key, consent and defaults carried over by the migration); previous images still tagged for rollback.
- **Risk:** migrating the live DB; compose edit; **Rollback:** restore the dump and retag the previous images. **Impact:** the first time any D62–D80 code runs in the app.
- **Roles (D92):** after the backup and the migration, set the live users' roles with the WI-PV3b command (the owner supplies the list); WI-PV3b is a dependency of this step.

#### WI-BE9a — Provider-agnostic hooks: dispatch, consent, settings
**Open (reworked D80; replaces "Gemini off while Qwen vision is on", O-13 superseded)** · M · Deps: WI-PV1 · Approval: N
- **Problem / evidence:** `HOOKS_BY_MODE` keeps H1–H4 in both active modes (`ai/consent.py:44-49`) and the text hooks read `ai_setting.provider/model/key`, i.e. Gemini only; the rule `gemini_required` (`ai/settings_service.py:943-953`) refuses to disable Gemini while a mode is active. With both providers supported and Gemini staying on, the hooks must follow the selected provider.
- **Approach:** H1 follows `extraction_provider`, H2–H4 follow `assist_provider`; the consent hook list, data-destination text and fingerprint are per provider; the `gemini_required` rule becomes "the selected provider must be Connected"; the legacy `ai_setting.provider/model/key` reads stay for one release. Legacy Gemini-only workspaces behave and fingerprint exactly as before (test).
- **Files:** `ai/consent.py`, `ai/settings_service.py`, `ai/enqueue.py`, `ai/runtime_checks.py`, hook handlers.
- **Acceptance:** with `assist_provider = vllm` a H3 request names provider `vllm`; with Gemini selected nothing changes; byte-identical legacy fingerprints; hooks refuse a provider that has no registered text reader.

#### WI-BE9b — Qwen text variants for H1–H4
**Open** · M–L · Deps: WI-BE9a, WI-BE2 (done) · Approval: Y (server run)
Prompt variants and OpenAI JSON-schema dialects for H1 (extraction suggestions from OCR text), H2 (item opinion), H3 (re-rank) and H4 (vendor merge), the same strict validators (H1 grounding: the quote must exist in the text; returned PO numbers must exist in the pool), thinking off, length limits in the schema, golden JSON tests with made-up values. Qwen becomes selectable for assist only after these tests pass.

#### WI-BE9c — Measure the text hooks per provider
**Open** · M · Deps: WI-BE9b, WI-D5 (Tier S) · Approval: Y (server runs)
H1 on Tier H (suggested header values vs truth); H2, H3 and H4 on Tier S (suggestion precision on expected line alignments, links and vendor merges, with n). These hooks never auto-accept (main plan Task 4.17: AI-suggested links default to Needs Review), so DP-L1 does not apply; results feed the Settings comparison note, which says "not enough data" where n is small.

#### AC-LIVE-S — Qwen in shadow while Gemini stays the live default
**Open** · S–M · Deps: AC-INT1b, WI-PV5, WI-OPS2, WI-FE1b (or API-only) · Approval: **Y** (consent, back-fill budget)
- **What changes:** an admin connects Qwen in the Settings UI (allow-listed URL, model `qwen3.5:9b`) and accepts its company-network notice; `shadow_provider = vllm`; `extraction_provider` and `assist_provider` **stay `gemini`**. Gemini page-image reading and the Gemini text hooks continue exactly as today; **nothing about Gemini is turned off or changed**. Qwen shadow requests write only comparison rows (D66); their answers are scored against OCR, never against Gemini's.
- **The 40 live documents:** untouched. A new back-fill command (this task builds it, `python -m reconbench_app.ai.backfill_shadow --workspace <uuid> --provider <p>`; O-14 applies per provider) renders pages from the stored source file and enqueues shadow-only reads for the current versions of the chosen provider; no re-parse, no new `document_version`, `PARSER_VERSION` unchanged. Budget: the number of page images the dry run reports (≈ 67 if the live files equal the bench PDFs: unverified), one read, concurrency 1, logged before the go.
- **Evidence:** `reader_comparison` rows and the shadow report (labels and counts only); MT-15 coverage; outage behaviour.
- **Acceptance:** PH3 shadow exit; `document_version`, `doc_line`, `consensus_run`, `vendor`, `link` row counts unchanged by shadow (D66 test, plus a live before/after); the audit log shows no Gemini setting change; Gemini request counts per hook on fresh uploads match the pre-shadow behaviour.
- **Rollback:** clear `shadow_provider`. **Impact:** real-traffic evidence for Qwen with zero risk to documents or to the live Gemini flow.

#### AC-LIVE-M — Merge, enabled per provider; choosing the default provider
**Open** · M · Deps: PH4 prerequisites (WI-BE7a/b/c, WI-G3, WI-FE2, DP-L1 numbers **per provider**, shadow criteria met, WI-BE9a/b/c) · Approval: **Y**
- **What changes:** deployment flag `RB_AI_LLM_MERGE=enabled`; for each provider whose held-out numbers pass DP-L1, its card gets `apply_mode = merge` and the passing field types in `auto_verify_field_types` (all other types stay suggestions); a provider without numbers stays suggestions only. **The default provider after this step is an owner decision (O-22) taken from the side-by-side numbers**; the other provider stays Connected and selectable (per-upload override, re-run). Gemini is never turned off by this task.
- **The 40 live documents:** keep their current OCR/rules versions; nothing re-merges automatically. A per-workspace, owner-triggered "re-read existing documents" merges the stored readings of the chosen provider into **new versions only where** the current version has no manual correction and no verified field the merge would change (MG-7); otherwise the reading is stored as an alternative. A before/after report (counts: fields filled, changed, flagged `LLM_DISAGREEMENT`, vendors/links/cases changed) goes to the decision log.
- **Acceptance:** RG-7 and RG-8 on the 40 documents; the first applied answer produces a `consensus_run` (WI-INT0); end-to-end bench hard zeros 0 for each enabled provider; export parity MT-12 and the Provenance sheet reconcile.
- **Rollback:** per provider `apply_mode = suggest`, `RB_AI_LLM_MERGE=disabled`, and "restore OCR version" (appends a copy; history is never deleted).

#### WI-IMP1 — Native Tally import (new, D86; product task, before AC-INT1)
**Parser part built and measured (D95, 22 Oct; owner's queue)**; file-size limit not changed · M-L · Deps: none · Approval: **Y**
- **Problem / evidence:** the owner's full August dumps are native Tally "Import Data" exports. The app parser rejects both whole files (`XML_PARSE_ERROR`: every voucher holds the invalid character reference `&#4;`; the masters hold stray control characters). The two files are 23.1 and 23.4 MB against a 25 MB upload limit. With `&#4;` removed and one voucher wrapped in the envelope the parser reads 251/251 and 250/250, but no grand or taxable total is parsed and the GRN's PO reference (`REFERENCE`, also line `ORDERNO`) is not mapped to `po_refs`.
- **Approach:** import multi-voucher `ENVELOPE` files; handle the `&#4;` marker and stray control characters safely and **log** each removal, never silently changing a value; ignore the masters cleanly; set a file-size limit that fits real exports; map Receipt Note `REFERENCE` / line `ORDERNO` to `po_refs` by default and flag a multi-PO GRN (6 of 250 in the dump); parse PO and GRN totals where Tally provides them; follow the version rule O-5 (engine version and rules hash change, golden outputs re-verified).
- **Files:** `reconbench_core/parsers/`, `reconbench_app/ingestion.py` (limits), tests with synthetic Tally envelopes (no real data).
- **Acceptance:** to be written when the owner approves the build; includes the D86 dumps ingested without a custom layout and the same pool results as WI-D4f.
- **Risk / rollback:** parser change, behind the version rule. **Impact:** real Tally exports become usable without a split script.
- **As built (D95):** `reconbench_core/parsers/tally.py` (new: `sanitize`, `voucher_facts`, `tally_native.v1`) and `parsers/xml.py` (sanitize-and-retry after a strict parse fails; per-voucher removal warnings, scope-aware level; native voucher enrichment: description, stock item extra, full GST rate, sign normalisation with a `TALLY_SIGN_NORMALISED` log, ledger totals with `TALLY_TOTALS_FROM_LEDGERS` or `TALLY_TOTALS_NOT_CLOSED`, GRN `po_refs` from `REFERENCE` and line `ORDERNO` with `TALLY_GRN_MULTIPLE_PO_REFS`; "Receipt Note" → GRN); `PARSER_VERSION` phase4-parser-7; reconciliation fixes in `reconciliation_service.py` (PO edges only) and `recon/engine.py` (blocked case at-risk 0). Patch `work/patches/t3-imp1-tally-native.patch` (+ follow-up hunks, see D95). Not done: the upload size limit (the dumps are 23.1 and 23.4 MB under 25 MB; a UTF-16 month may exceed it).
- **Results (D95):** whole-file parse 251/251 and 250/250; against the old split parse the same documents, lines, quantities and rates, amounts now positive, arithmetic QA failures 248 → 14 (PO) and 247 → 15 (GRN); dev bench unchanged; pool as in §0 queue task 3 (`work/accuracy-intake/q3/t3-pool-arm{A,B}.json`).
- **Line-alignment evidence (D94):** see §0 queue task 2: `BASICUSERDESCRIPTION` aligns 16/19 PO and 16/29 GRN truth invoice lines where `STOCKITEMNAME` aligns 0; 5 PO and 11 GRN pool lines (of 455 and 456) have no user description and keep the item name.
- **Required item (owner, D89): sign normalisation of Tally amounts.** Tally exports purchase line amounts as negative numbers (all 455 PO and 456 GRN lines in the D86 dumps); the import stores positive magnitudes and logs each changed field with its original text, never silently. Evidence (D89): the harness-only normalisation changed no link, case or ledger figure in the pool run, so this is a data-correctness requirement (stored, displayed and exported amounts), not the linking fix.

### 7.3 Qwen reading quality

#### WI-BE8 — Cropped two-pass reading for dense pages
**Measured and rejected as the default (D97, 22 Oct); kept as a bench variant** · M (split: crop render + core mapping; then bench arm) · Deps: none (design in §4.5) · Approval: **Y** (server run)
- **As built (D97, bench only):** `reconbench_app/ai/vision_crops.py` (crops 0–400, 300–700, 600–1000 of the page scale with masks moved; crop answers moved back to the page; fields and notes added once, so the core D77 rule decides conflicts; rows deduplicated by amount/quantity/rate, a crop row overlapping another with different values removes both (`region_conflict`), rows at a cut edge dropped; reasons `cap`, `fewer_rows`, `totals_open`); bench `--vision-strategy crops` / `crops-control` with `--vision-crops-send` (pass 1 = the newest repeat). Simpler than §4.5: fixed horizontal bands, no layout-driven table bands, no repeated column-heading strip. Patch `work/patches/t6-be8-crops.patch`.
- **Result (D97):** same-day pass 1 (Q2.1c, 21 Oct) vs pass 1 + crops on the dev set, unique documents, strict rule: headers 101/136 → 99/137, header amounts 27 → 24/46, line amount 28/47, quantity 24/44, rate 26/45 (no change; +8 each with copies), references 3 → 4/6, HSN 13 → 14/23, accepted-wrong **0 → 1**, documents lost to the cap 1 → 0; 84 Qwen requests (72 + 6 failed `PROVIDER_UNAVAILABLE` + 6 retried once). Crops on pages with open totals added 190 header items, and the extra values turned correct single values into conflicts or a wrong accepted value; the cap cases were the only clear gain. A narrower variant (crops only for pages lost to the cap, header items not merged) is the candidate if the owner wants a second try.
- **Problem / evidence:** dense pages hit the answer cap under every prompt (16 failed calls: S06-INV, S08-INV, S09-PO, S10-PO, S02-INV recurring); line qty/rate/amount each miss ≈ 33 rows; §4.5.
- **Approach:** per §4.5. The prompt stays `vision.prompt.v4.qwen`; the band context ("page p, band k of m, kind") goes in the per-call user text, not the system prompt, so only the strategy changes.
- **Files:** `extraction/vision_pages.py` (crop render from masked bytes; mask boxes inside a crop stay black), `document_page_image.render.crop`, `reconbench_core/extraction/vision.py` (`map_crop_box`, `merge_regions`), `ai/worker/hooks/vision.py`, `scripts/accuracy_vision.py` (`--vision-strategy crops` is accepted by argparse and refused today).
- **Acceptance:** §4.5 (a)–(d); dense subset all usable; scan-page line amount +5 points over the same-day control; non-dense documents not lower by > 1 field in total; wrong-when-answered not above the control; unit tests for `map_crop_box`, overlap dedupe, the no-heading fallback, and masked-crop integrity.
- **Risk:** latency and call count; boxes mapping errors; **Rollback:** strategy `full`. **Impact (hypothesis):** recovers the lost rows; target line amount ≥ 105/123.

#### WI-EV2 — Decision matrix, Qwen and Gemini side by side
**Open** · M · Deps: WI-BE8 result, D77 control, **Gemini arms E1–E4 (now PH2)** · Approval: Y for any further server run; the Gemini bench key and the request plan are the owner's (free tier, D83)
- **Arms:** E0 (rules + OCR); **E1–E4 Gemini** (`gemini-3.5-flash-lite` and `gemini-3.8-flash`, thinking low and medium, `media_resolution` high, no temperature sent; model availability **unverified**: one probe call first); E5 Qwen (v4, v5, v6, v7 history); E7 presence penalty 0 (optional single-variable); E8 crops (WI-BE8). Fresh Gemini reads under `vision.prompt.v4.gemini` and schema v4 are needed: the stored Gemini answers are v3 (D41) and not comparable. Reference sizes (D41, flash-lite low): about 2.6k tokens in and 2.1k out per document, 67 page images per arm on the dev set; medium thinking tokens unknown; the free-tier quota is low, so runs are paced by requests per day (WI-EV3 `--max-requests-per-day`), one arm per day or less, spread over days if needed; reads from different days carry their date and the returned model string, and an arm compared across days needs a same-day control (the Qwen rule of §2.5 applies: drift per run date is recorded). Policies P1–P4 and `dual_agree` stay out of scope.
- **Same truth set and splits for every arm.** The matrix has one row per arm and the columns: header and line accuracy per field with n, agree-wrong, wrong-when-answered, unreadable→null, latency, cost (USD for Gemini; GPU time/tokens for Qwen), variance within an epoch, verdict.
- **Rule for being the *default* provider for merge (pre-registered, applies to every arm):** (a) scan-page headers ≥ OCR + 5 points and lines ≥ OCR + 10 points; (b) simulated false-safe 0 and LLM×OCR agree-wrong ≤ 1; (c) wrong-when-answered ≤ 2% of answered critical fields; (d) latency not judged until accuracy is decided (D72); (e) cost within the owner's cap (Gemini) or GPU-time budget (Qwen); (f) variance within one epoch ≤ 2%. Selectability does not depend on this rule: a provider that fails it gives suggestions only.
- **Current verdict (E5 v4 + D77):** (a) headers +10.6 points, lines mixed (amount −3.3, quantity +12.3, rate +15.3, description −11.1, HSN −20.0), so **not yet met**; (b) 0 and 0; (c) 3/278 = 1.1% (critical 0/141); (f) 0 within an epoch. **Gemini v4: not measured.**
- **Acceptance:** matrix filled for both providers; counts-only measurement file written for the Settings note (WI-EV3); the choice and the numbers recorded in the decision log; the default provider itself is O-22, decided at AC-LIVE-M.
- **Gemini bench on the free tier (D83; replaces the ₹2,000 budget, which applies only to a billed key):** the budget is **requests**, not rupees. Before each run the engineer estimates requests (pages × arms × repeats × calls per page; D41 sizing: 39 documents, 67 page images per arm and repeat) and logs it with the date; the run is paced inside the daily limit read from configuration or the first daily-quota error (never hard-coded), stops cleanly on a daily-quota error and resumes the next day from the answer cache. E1 and E2 use the pinned `gemini-3.5-flash-lite`. **E3/E4 (`gemini-3.8-flash`) are not approved.** **Key route (resolved):** the bench uses a **separate free-tier Google project and key**, so it never uses the app's quota. The owner exports `BENCH_GEMINI_KEY` in their own shell for the run only (read with `read -s`; not in `.env`, shell history, files or logs); the bench reads only that variable and **never the sealed app key** (a guard test asserts the bench imports no key-seal or secret-store code); the key is never printed, logged or written into a report. **Data (O-25, D84):** the owner approved the masked dev set for the free bench key (20 Oct); masking per D-1 applies.
- **Note (21 Oct, D88; no decision):** on stored answers re-scored against the D87 truth, Gemini 3.5 flash-lite reads handwritten references **24/57** against Qwen v4 **9/57**, with similar header scores (273/313 and 275/313; copies counted, the stored Gemini answers are the older v3 prompt). A per-field provider choice for handwritten references is a candidate row for the side-by-side matrix after E1/E2 and WI-Q2. No setting is changed.
- **Queue task 5 (D97):** the Gemini side-by-side runs (about 117 requests) were skipped again: the bench key file in the Desktop folder was empty. E1/E2 are not done.
- **WI-Q2 Gemini runs (D92):** approved (117 requests on the free bench key) but **skipped**: `BENCH_GEMINI_KEY` was not set in the session that ran the bench. On stored v3 answers with the new code Gemini types 6/16 references (13/16 with notes) against Qwen v4's 3/16 (4/16).

#### WI-V1 — Validators for Qwen values: HSN shape and reference existence
**Open** · M · Deps: WI-EV3 · Approval: N (code), Y (bench)
- **Problem:** 3 wrong Qwen HSN values and 3 missing/wrong refs have no independent check; V-HSN exists only on the OCR table path.
- **Approach:** `check_reading` marks `check_failed:hsn_shape` (4/6/8-digit HSN, 6-digit SAC starting `99`); V-REFEXIST: the app layer supplies the workspace's open same-vendor PO/GRN numbers to consensus, which records "reference exists" as validator evidence (never creates a link). Bump `CONSENSUS_VERSION` (O-5).
- **Files:** `reconbench_core/extraction/vision.py`, `consensus.py`, `reconbench_app/ai/consensus.py`, tests.
- **Acceptance:** synthetic unit/property tests; dev: no correct HSN flagged (false-reject 0), references ≥ 13/15 with 0 wrong accepted; flag-off hash identical.
- **Risk:** over-flagging; **Rollback:** revert the two checks. **Impact:** a deterministic gate for HSN and references; precondition for refs reaching AA.

#### WI-V2 — Marketplace order number post-check (D72 lifted for this task only, O-4)
**Open** · S · Deps: none · Approval: given (O-4); Stage 4 authorisation needed
- **Problem / evidence:** v4 misses 5/5 marketplace order numbers (they come back as `po_ref`/`sales_order_ref`); the v7 prompt sentence fixed them but caused 4 accepted-wrong values on S03-INV (D77).
- **Approach:** in the pure check, rename a `po_ref`/`sales_order_ref` to `marketplace_order_ref` when its label contains "Order" **and** the value matches `^\d{3}-\d{7}-\d{7}$` (pattern generalised only if Tier H shows other marketplace formats). No prompt change.
- **Acceptance:** dev 5/5; the field-level diff against the v4 control equals exactly the 5 renames; property test: never renames without both conditions. Bump `CONSENSUS_VERSION`.
- **Risk:** a rule on extracted values (the freeze concern); bounded by the two conditions. **Rollback:** remove the rename.

#### WI-Q1 — Line description lever (RULE-DESC stays until this passes)
**Open** · M · Deps: WI-BE8 (table crops) · Approval: Y (server run)
- **Problem:** 40 of 108 descriptions are wrong, not missing (extra text merged, 34); v5's wording made it worse (47 → 39).
- **Approach:** hypothesis: reading the description column band per row removes the merged text. Single variable on top of the BE8 table crop: description band instruction. Control: same-day crops run without it.
- **Acceptance (pre-registered):** correct ≥ 65/108 and wrong ≤ 20 on dev; confirmation on held-out Tier S lines before RULE-DESC can be lifted (and then only through DP-L1).
- **Impact:** description is the weakest field (43.5%); also feeds linking by item similarity.

#### WI-Q2 — Handwritten-reference lever (hints only) — built and measured (D92)
**Open** · M · Deps: WI-BE8 (optional margin band) · Approval: Y
- **Problem:** 1/18 found on v4; 17 are not in the OCR text; v6 found 6/16 but lost CGST/SGST.
- **Approach:** annotations-rule-only variant on v4 (single rule), then a margin-band crop if BE8 passed. Output is `annotations[]`, never a field (MG-9); used as link hints.
- **Acceptance:** recall ≥ 6/16 with no header group net < −1 and accepted-wrong 0.
- **Reference match rule (owner, 21 Oct, D88):** short-form scoring (for example `08-081`) is fine for **reading** accuracy. For **linking**, a reference is **type-aware**: a PO-type reference may link only to a PO and a GRN-type reference only to a GRN. Both `PO/26-27/08-081` and `GRN/BCSL/25-26/08-081` can exist, so **a short number alone never auto-links**; a reference whose type is unclear stays a suggestion. Common misreads are normalised before matching (for example `26-27108-081` to `26-27/08-081`, the slash read as "1"), and each normalisation is logged with the original text.
- **Output-type fix (owner, D89; do not build yet):** handwritten PO and GRN numbers are emitted as **typed references** (`po_ref` / `grn_ref` annotations with their type) and never only as untyped notes, so the matcher can use them under the D88 type-aware rule. Evidence: on S04 the stored Qwen v4 answer wrote the handwritten PO `08-227` and GRN `08-210` only as free-text notes, which the matcher ignores; 12 of the 13 expected pool links carry their number only in handwriting, and S06's correct PO sits at 0.77 for want of exactly this reference signal (weight 0.40). Acceptance adds: typed handwritten references reach `annotation_refs` on the dev set; on the pool run, links found from handwritten numbers are suggestions unless the D88 rule and the amount check both pass, with 0 false auto-links.
- **Design (D91, 21 Oct; not built, no model call):**
  1. **Typed output.** The vision answer schema gains, per annotation, `ref_type` (`po`, `grn`, `service_po`, `other`) and `ref_text` (as written). Typed references go to `extras.annotation_refs` as objects `{type, text, normalised, page, box}` (the flat string list stays readable for one release); MG-9 holds: never a header field. New prompt variants `vision.prompt.v4.q2.qwen` and `.q2.gemini` (one rule each: "a handwritten or stamped PO/GRN number is an annotation with its type"); schema and prompt versions bump under O-5, so cache keys change.
  2. **Misread normalisation (deterministic, logged).** Applied to typed references before matching, each change logged as `{rule, before, after}` with the original text kept: prefix `P0`/`Po`/`0` before the year to `PO/`; a slash read as `1` between the financial year and the month (`26-27108-081` to `26-27/08-081`, only when the result is a valid month); `/108-` to `/08-`; a split last group (`08-08-1` to `08-081`) only when the result matches the pattern. A normalised value must match `PO/YY-YY/MM-NNN` or `GRN/…/YY-YY/MM-NNN` or the short form `MM-NNN`, with the financial year of the invoice date and a month within one month of it; otherwise it stays as written and untyped.
  3. **Margin/header crop.** One extra read of a band of page 1: the top 35% of the orientation-corrected page (the rules reader's page rotation; S06 is a rotated scan), full width, upscaled 2×. Built as a new module `reconbench_core/extraction/vision_bands.py` that WI-BE8 later reuses; WI-Q2 and WI-BE8 both touch `extraction/vision.py`, so they stay sequential (§8).
  4. **Matching (D88).** A PO-type reference may only point to a PO, a GRN-type reference only to a GRN (a new invoice-to-GRN hint path); a short number or an untyped reference never auto-links; in WI-Q2 every link that rests on a handwritten reference is a **suggestion** (auto from handwriting needs DP-L1 numbers and the owner's decision). Still needs the same-vendor and amount corroboration of §4.9.
  5. **Truth and scoring.** The 16 typed handwritten references on S01 … S08 (S01's `08-031` counts for reading, not for linking), unique documents, strict copy rule (a reference is read only if read in every copy: 27 copies, 55 pages); per type; wrong-type count; false references on the 12 dev documents without handwriting (target 0). **Baselines (stored answers, D91):** typed references **Qwen v4 1/16, Gemini 3.5 flash-lite (prompt v3) 6/16**; counting untyped notes too, 4/16 and 11/16; links in the v2 pool from handwriting **0 of 12**.
  6. **Experiments (each run approved separately; request counts before any call).** Q2.0 offline on stored answers: the typed classifier on existing notes, the normalisation and the D88 rule in the v2 pool: **0 requests**. Q2.1 prompt variant on the whole dev set (regression guard for every field): **Qwen 67 requests** (one page per call), **Gemini 39** (one document per call). Q2.1c same-day control with the current prompt: **Qwen 67, Gemini 39** (the stored Gemini answers are prompt v3 and not comparable). Q2.2 band crop of page 1 on the dev set: **Qwen 39, Gemini 39**. Totals **Qwen 173, Gemini 117** (one repeat; Qwen answers identically within a day, D76). A reduced plan on S01 … S08 only (27 copies, 55 pages) would be Qwen 55 + 55 + 27 = 137 and Gemini 27 + 27 + 27 = 81, but it cannot check the other fields for regressions. Gemini runs on the free-tier bench key, paced inside the daily limit (WI-EV3, D83); E1/E2 approval applies.
  7. **Success metric (owner, D90):** handwritten-reference reads (unique documents, per provider) and invoice links unlocked in the v2 pool from handwritten numbers, with **0 false auto-links**; guard: no header group net below −1 and accepted-wrong 0 on the dev set.
- **As built (D92):** `reconbench_core/extraction/handwritten_refs.py` (typed references `po`/`grn`/`service_po`/`other`; normalisation rules `spaces_around_separators`, `fy_space`, `slash_read_as_1`, `stray_1_after_slash`, `split_serial`, `po_prefix`, `fy_completed_from_invoice_date`, each logged with the text before and after; pattern, financial-year and month checks; version `handwritten_refs.v1`); `vision_document` adds `typed_refs` beside the annotations (never a field); the type is carried by the existing `looks_like` (`po_number`, `grn_number`, new `service_po_number`) instead of separate `ref_type`/`ref_text` keys (same information, smaller answer); `mapping/links.py`: a PO-type reference names only POs and a GRN-type reference only GRNs (whole number or `MM-NNN`), a new handwritten-GRN hint path (needs part or amount corroboration, always a suggestion), typed references left out of reverse matching; prompt variants `vision.prompt.v4.q2.qwen`/`.gemini` (the current v4 prompts plus one typing rule) with `vision.schema.v5`, while every v4 request and its cache key stay unchanged; `reconbench_app/ai/vision_bands.py` (top 35 % of the upright page 1, masks moved into band coordinates; WI-BE8 reuses it); bench `--vision-strategy band`. `ENGINE_VERSION` 0.1.7 → 0.1.8, `RULES_HASH` `70d529d1…`. Patch `work/patches/wi-q2.patch`.
- **Results (D92; dev set, Qwen3.5-9B on the company server; unique documents, strict copy rule, 16 references on 8 invoices; S01's `08-031` counts at any type):**

| Arm | Typed correctly | Typed, unread copy = miss | With notes | Normalised refs | Requests |
|---|---|---|---|---|---|
| Stored v4 answers, new code (Q2.0) | 3/16 | 3/16 | 4/16 | 9 of 26 | 0 |
| Stored Gemini flash-lite v3 answers, new code (Q2.0) | 6/16 | 6/16 | 13/16 | 21 of 45 | 0 |
| `v4.q2` full pages (Q2.1) | 12/16 | 8/16 (4 documents failed) | 13/16 | 2 of 49 | 43 |
| `v4.q2` header band (Q2.2) | 11/16 | 11/16 | 13/16 | 14 of 50 | 29 |
| **v4 full pages + band** | **12/16** | **12/16** | 13/16 | 23 of 76 | (29) |

- **Field guard (same-day control, Q2.1c, 44 requests):** today's v4 read equals the 16 Oct read on 722/725 field outcomes (headers 270/273): the server is stable. On the 29 copies both read, `v4.q2` against same-day v4: headers 217 vs 229/246, taxable 17 vs 23 (wrong 4 vs 1), CGST/SGST 21 vs 23, rate 64 vs 78, amount 79 vs 83 of 94, **accepted wrong 3 vs 0**. The full-page `v4.q2` prompt fails the guard and is **not adopted**; the band read does not touch the full-page fields. Failures: Q2.1 three answers at the output cap (two copies of S05, the S09 PO) and one `PROVIDER_UNAVAILABLE` (not retried); Q2.1c one at the cap; Q2.2 none.
- **Pool (v2, D92):** stored v4 + typed refs: 1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false; stored Gemini + typed refs: 1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false; `v4.q2` + band: 1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false; v4 + band: 1/13 found (0 auto, 1 suggested), false auto 0, false suggested 0, links resting on handwriting 0 correct / 0 false. Typed PO references do name the right POs (S03, S04, S06, S07), but the matcher counts a handwritten reference only with same-vendor amount corroboration, and that needs aligned lines: the invoice descriptions do not align with the generic Tally stock-item names (0 aligned rows), so the reference signal stays 0 (diagnostic `work/tools/q2_link_trace.py`). This moves to WI-D4.
- **Recommendation (D92):** keep the v4 full-page prompt for fields and add the header-band read for handwriting; the remaining gain is in the matcher (WI-D4) and in PO/GRN totals (WI-IMP1).
- **Adopted (D93, 22 Oct):** v4 full pages plus the band read is the default shape of the app's Qwen reader for invoices (`extraction/vision.py` `band_prompt_variant`, `BAND_DOC_TYPES`; `ai/vision.py` `band_prompt`, `merge_answers_with_band`; `ai/worker/hooks/vision.py`). Bench arms read full pages only (`--vision-strategy band` stays the separate band arm), so stored answers replay unchanged. Patch `work/patches/t1-q2-band-default.patch`. Gemini keeps one call per document without a band (not measured; WI-EV2).

#### WI-Q3 — Own-number lever for `doc_no`
**Open** · S–M · Deps: none · Approval: Y
- **Problem:** 7 of the 12 pre-D77 doc-no nulls were the document's own number returned as `po_ref`/`grn_ref` (D75).
- **Approach:** single-rule prompt variant on v4 ("`doc_no` is this document's own number; on a PO its PO number, on a GRN its GRN number"), same-day control.
- **Acceptance:** doc no. ≥ 36/39, other groups net ≥ −1, accepted-wrong 0 (§4.7 rule).

### 7.4 Server reliability

#### WI-OPS1 — Company vLLM server checklist and smoke test
**Open** · S + owner · Deps: none (smoke 3 needs the v4 prompt/schema, available) · Approval: Y (server calls, synthetic data)
- **Deliver:** `docs/ops/vllm-server.md` (facts of §3.5 kept current, plus the checklist answers: vLLM version, reasoning parser, default thinking, JSON-schema support, max images per prompt, pixel limit, request logging and retention, shared-GPU rules) and `scripts/local_llm_smoke.py` (synthetic page only; counts printed). Smoke: models, text JSON with thinking off, synthetic image at 1,600/1,280/1,024 px (decides `box_order` and image size), and reachability from the compose `ai-worker` container.
- **Acceptance:** every checklist item answered or marked UNKNOWN; smoke 2–4 pass; chosen image size and `box_order` written into the `vision_openai_compat` registry entry.

#### WI-OPS2 — Health, backoff, fallback in operation (both providers)
**Open** · S–M · Deps: WI-PV2, AC-INT1b for the live check · Approval: N
- **Approach:** §4.8 for each connected provider: status and last-checked time next to the provider selectors; `PROVIDER_DOWN` generalises `LOCAL_PROVIDER_DOWN`; one deferred retry after 60 s; new requests skipped while health has failed > 5 min; document-level flags `LLM_UNAVAILABLE` / `PROVIDER_DISCONNECTED` visible in review; **no automatic failover**: the UI offers a manual switch (admin changes the default; any user re-runs a document with the other provider) and the optional `allow_provider_fallback` setting (O-21, default off, consent required, affected documents flagged); per-card limits (concurrency, timeout) applied; ledger in-flight per provider.
- **Acceptance:** unit tests with a fake clock; integration with a mock server returning 503 then 200; outage drill in PH3 for each provider; with fallback off nothing ever routes to the other provider.

#### WI-OPS3 — Canary and server-epoch detection
**Open (new)** · S · Deps: WI-OPS1 · Approval: Y (synthetic server calls)
- **Problem:** the server changed once (16 → 17 Oct) without notice; cached answers and comparisons would silently mix epochs.
- **Approach:** a daily job reads 3 synthetic pages; stores the answer digest, `/version`, `/models` fingerprint in `ai_call_log` provenance; a change opens a new "epoch" record; the gate (§5.3 step 6) and reports refuse cross-epoch comparisons.
- **Acceptance:** unit tests; ≥ 7 days of canary records; an induced digest change opens an epoch.

### 7.5 User interface

#### WI-FE1a — Settings → AI providers: cards
**Open** · M · Deps: WI-PV2, WI-PV3 · Approval: N
Two cards: **"Qwen (internal vLLM, OpenAI-compatible)"** and **"Gemini (Google)"**. Each shows the status (Not connected / Connected / Error), the last-checked time and the error reason. Qwen fields: base URL (chosen from or added to the allowlist), model name (dropdown from the test result), optional API key, timeout. Gemini fields: API key, tier (free or paid), model, monthly cost cap (shown only for a paid card), quota status (OK / rate-limited / daily quota reached with the reset time in IST; D83), and the egress-notice checkbox at connect (for a free card the notice states the data use). Buttons: Test connection, Disconnect, Rotate key; the key is write-only, shown masked (last 4), never pre-filled and never rendered after save. Only an admin sees the controls; others get a read-only view. React Hook Form + Zod + TanStack Query, types from `generated.ts`, 375 px layout, tap targets ≥ 44 px. The Home card links here.

#### WI-FE1b — Provider selection, fallback setting, usage, comparison note
**Open** · M · Deps: WI-FE1a, WI-PV5, WI-PV7, WI-EV3 · Approval: N
Default selectors for extraction and for AI assist (only Connected providers listed, with each provider's status beside the selector); a manual switch; the per-upload override in the upload dialog; the "allow fallback to the other provider" setting (off); Gemini usage this month against its cap; the shadow-provider selector and the allowlist editor (admin); a short note "which provider measured better per field" from the imported measurements (counts, n and date; the user decides); states for loading, error and read-only.

#### WI-FE3 — Frontend tests and type-drift guard
**Open** · M · Deps: FE1a/b · Approval: N
Vitest: Zod rules; the key never rendered (DOM and network log after save); non-admin read-only; Connected only after a passing test; disconnect and rotate flows; selector lists only Connected providers; per-upload override; the re-run button does not change the current version; strategy and fallback options with reasons. Playwright with mocked APIs for both providers at 375 px. Replace the hand-written `web/src/api/ai.ts` interfaces with aliases of `generated.ts` so `make check-static` covers them.

#### WI-FE2 — Review UI: provenance, disagreement and re-run
**Open** · M · Deps: WI-BE6 (done), WI-BE7, WI-PV6 · Approval: N
Suggestions carry `origin {provider, model, prompt, schema}` with a chip; readings are listed by provider; the button "Read again with <other provider>" (WI-PV6); side-by-side OCR/provider values with "use this reading/value" (draft only; saved through `PUT /documents/{id}/correction`, versioned and audited); filter "LLM disagreement"; no route can set `verified`. **Acceptance:** Vitest and a Playwright disagreement and re-run case.

### 7.6 Merge

#### WI-G3 — Regression guards and rollback switches
**Open** · S–M · Deps: WI-BE7a design · Approval: N
Scenario tests in `tests/scenarios/test_hard_zero_guards.py`: an LLM-only value is never verified; two LLM readings never verify; a merged version never replaces a manual correction (MG-7); a non-allow-listed endpoint never receives a request; shadow writes no document rows; `page_conflict` never leads (MG-3); the review flag stays unless Σ lines = taxable (MG-4); a grown `po_refs` set is not auto-accepted (MG-5); descriptions never auto-accepted (MG-6). Property test on `merge_readings`. Switches default off: `RB_AI_VLLM_CALLS`, `RB_AI_LLM_MERGE`, `reader_strategy`, `apply_mode`; `RB_VISION_READER=vision_gemini` restores the old reader set.
- **Provider platform (D80):** the WI-PV8 suite runs in CI; the kill-switch test (both gates `disabled` ⇒ no request to any provider) is a scenario test.
- **Explicit coverage (owner, 20 Oct):** WI-G3 and WI-PV8 together cover: the secret never in API responses, logs, errors, exports, reports or audit events; a non-admin refused; allowlist/SSRF; disconnect mid-run safe; switching provider never altering existing readings; the cap stopping calls at 100% with the fallback flag shown.

#### WI-BE7a — Merge core (pure) with the D77 guards
**Open (Stage 4 third)** · M · Deps: WI-EV2 decision, WI-V1 · Approval: N (code)
`reconbench_core/extraction/llm_merge.py`: `merge_readings(ocr, readings, policy) -> MergeResult` per §4.10 with guards MG-1 … MG-9 and `arb.v1` as data. The consensus side implements the narrowed validator-only path exactly as in §2.3 (GSTIN: checksum + not the buyer entity + equals the vendor-master GSTIN; amounts: Σ lines = taxable and taxable + tax = grand; anything less is agreement-only) and the O-11 circuit breaker. **Acceptance:** unit test per rule; property tests (no `source=AI` field with only LLM readers has confidence ≥ 0.70 or `verified`; values of manual/verified fields never change); the S08-INV pattern (all critical accepted, lines wrong) keeps its review flag; the bench `--vision-primary-sim` equals the app merge on synthetic documents.
- **Provider-agnostic (D80):** the reader id is `llm:<provider>:<model>:<prompt_version>`; `arb.v1`, the DP-L1 numbers and the breaker are keyed by provider and field type; the same code and guards serve Qwen and Gemini; a provider without numbers produces suggestions and alternatives only.
- **Guard MG-11 (owner, D89):** a value whose evidence page is classified as another document's page is dropped before the merge and recorded as `foreign_page`; test fixture modelled on S05 (3 pages, page 3 the e-way bill of another invoice); acceptance: no value from such a page reaches a candidate document, and the rules + OCR warning stays.

#### WI-BE7b — Merge job and persistence
**Open** · M · Deps: BE7a, AC-INT1b (integration) · Approval: N
Procrastinate task `merge_llm_reading` in the **general** worker (no SDK import; import-linter test) queued from the ai-worker's `apply` when `apply_mode = merge`; appends the new `document_version` with per-field evidence `{origin, schema_version, reader_params_digest, ai_request_id, page, box}`; becomes current only under MG-7; vendor/link/recon recompute as for any version. **Acceptance:** integration tests on PG: cached answer → new flagged version; manual correction present → alternative only; consensus raises verification only for opted-in field types; flag off → bench hash identical.

#### WI-BE7c — Flag, rollback and restore
**Open** · S–M · Deps: BE7b · Approval: N
`RB_AI_LLM_MERGE` deployment flag (default `disabled`) so `apply_mode = merge` stops returning 422 `merge_not_enabled` only when enabled; "restore OCR version" action; audit events. **Acceptance:** tests for each switch; a restore appends a copy and deletes nothing.
- **Per provider (D80):** `apply_mode` is read from the provider's card; the circuit breaker (O-11) acts per provider and field type and writes `ai.breaker_trip`.

### 7.7 Matching and identity

#### WI-D4 — Link-recall regression (AC-9a)
**No self-support and header-level handwritten references built (D96, 22 Oct); dev recall ≥ 7/10 not yet re-measured as a separate item** · M · Deps: none · Approval: N
- **Problem:** recall fell 7/10 → 3/10 with identical extraction; lost: S09-GRN→PO, S14/S15/S16-GRN→PO (diagnosis §5.7). Cause in `reconbench_core/mapping/links.py` unknown.
- **Approach:** add `--link-trace` to the bench (labels per expected pair: candidate generated, vendor gate, anchor type, verified, ambiguity count, final state); find the failing stage; fix there; keep D-6.
- **Acceptance:** links ≥ 7/10 on dev, `FALSE_AUTO_LINKS` 0, `false_suggestions` 0.
- **Pool evidence (D89):** a GRN's `po_refs` (Tally `REFERENCE`) becomes a verified, auto/labeled link only after a ledger rebuild: the reference system check needs an independent part match (Tally stock items carry no part numbers) or the amount signal, and the amount signal uses the PO ledger's received quantity, which on the first pass is 0. The ledger counts suggested non-AI links (`_ACTIVE_LINK_STATES`), so on the second pass the GRN's own receipt supports its own reference check (in effect GRN qty × PO rate against GRN qty × GRN rate); in this pool all 191 resulting auto links agree with `REFERENCE`, but the evidence is partly self-supporting and needs a design decision. Invoice content: S06's correct PO scores 0.77 (amount pass, part 0.61, reference 0) and stays below threshold for want of a reference; S02's correct PO is not the top candidate (CONTENT_AMBIGUOUS, the wrong candidates are not linked); S07 has no candidate (invoice descriptions do not align with the generic Tally stock-item names).
- **No self-support (owner, D90):** a GRN's own link (suggested or any other state) never counts as evidence in its own reference check. Ledger support used by a check comes only from **other** links that are already verified or auto, or from independent evidence. After the change, re-run the v2 pool (disposable database) and report how many of the 191 auto GRN to PO links stay auto, whether any outcome changes, and the hard zero (0 false auto-links). Part of WI-D4, not built yet.
- **As built (D96):** `mapping/links.py`: `MappingPO.grn_support_by_source` and `_grn_support` (a check never counts its source's own receipt; with per-source support only other auto/confirmed links and opening balances, key `OPENING_SUPPORT`, count; an older snapshot keeps its ledger figures); `_direct_grn_po_amount` (every goods line of the GRN aligned to a PO line, GRN quantities at PO rates equal the GRN amounts within tolerance) added as independent evidence in the header and line `po_ref` system checks; `_handwritten_header_links` (typed PO/GRN references → `inv_po`/`inv_grn` suggestions under the owner's four conditions, evidence names the basis; extra evidence when the handwritten GRN cites the handwritten PO), run after the manual pass and before the period-wide stage. `mapping_service._support_by_source` reads `ledger_entry` receipts per source and keeps only sources whose current GRN→PO link is auto or confirmed. `ENGINE_VERSION` 0.1.8 → 0.1.9. Patch `work/patches/t4-d4-matching.patch`.
- **Results (D96; v2 pool, native import, `work/accuracy-intake/q3/t4-pool-arm{A,B}.json`):** arm B (Qwen v4 + band) **11/13** (PO 6/6, GRN 5/7: S02 and S03 GRNs missed), 0 auto, false 0; arm A **4/13**, false 0; truth GRN→PO **6/6 auto** (S04 now auto through the direct line check); pool GRN→PO 217 auto, 9 suggested, 24 unlinked, all agree with `REFERENCE`; against the self-supported run of the same data 216/217 stay auto (GRN 08-133 becomes a suggestion; GRN 08-210 becomes auto). Re-scans 5/7. Mapping 401 + 293 s (two passes; three before).
- **Handwritten references (D92):** typed handwritten PO references name the right POs in the pool, yet no link follows: the amount corroboration a handwritten reference needs is computed only through aligned lines, and Tally stock items carry generic names. Candidate change (owner decides): accept a header-level amount check (invoice total against the PO or GRN total, or against the open balance) as corroboration for a typed reference, still as a suggestion; PO/GRN totals need WI-IMP1.

#### WI-D4f — Full-pool linking arm (new, D86; run D89)
**Run done (D89); detour closed** · M · Deps: Docker, a disposable PostgreSQL, the August dumps in `work/accuracy-intake/{po,grn}` · Approval: **Y** per run (the owner's go; any new Qwen read needs a request count and approval first)
- **Problem / evidence:** the dev bench resolves links only among the 26 truth documents. The matcher has never run against a realistic candidate pool: 251 POs and 250 GRNs, and one vendor alone has 18 POs and 18 GRNs (D87 check of S02). WI-D4 and AC-9b need that evidence.
- **Approach (D86):** a **separate bench path and report** (the default bench output and hash unchanged). Ingest the pool into a disposable database with the approved split approach (each voucher wrapped in the Tally envelope, byte-exact except the `&#4;` references and stray control characters, which are counted and logged) and the GRN `po_refs` layout mapping (`custom_xpaths={"po_refs": "REFERENCE"}`, disposable workspace only). One **primary** PDF per invoice; the matcher (`MappingService.resolve_workspace`) runs over the whole pool. **Arm A rules + OCR**: stored readings where they exist, otherwise the offline rules + OCR path (local Tesseract, no network). **Arm B Qwen stored**: stored v4 answers plus the D77 vision-alone document, labelled **harness substitution** (the app merge WI-BE7 is not built). The pool is described by the `pool` block of `truth.json` (files, hashes, layout hints, coverage), so more dumps (July POs, service-purchase vouchers) are added as data, not code.
- **Metrics:** per truth invoice, the correct PO and GRN as **auto / suggested / missed**; false links (auto and suggested); ambiguous candidates; precision and recall; runtime; the number of correct links possible **only** from the handwritten numbers. **Hard zero: 0 false auto-links.**
- **Limits:** a private, dev-set tool; **never held-out evidence**. August only: S08's PO is a July PO and S01 is a service voucher, neither can be linked from these dumps. **Service-purchase exports (SERVICEPUR vouchers, SER/GRN receipts) are needed for service invoices.** S01's `08-031` stays out of the link metrics while `unverified`.
- **Acceptance:** the report for both arms with the metrics above, 0 false auto-links, a stated reason for every missed link, the default bench report hash unchanged, no model call without approval. Tests that travel with it: duplicate detection on the 7 re-scan pairs (all copies, AC-8b), the S05 partial delivery (2 undelivered PO lines shown as pending, not as a mismatch and not as fully matched) and the S05 stray e-way-bill page (page 3, another invoice's, not read into this invoice).
- **Risk / rollback:** scripts only, disposable database; delete the report. **Impact:** the first link precision and recall measured on a realistic pool; evidence for WI-D4, AC-8b and AC-9b.
- **Reference match rule for the pool run (owner, 21 Oct, D88):** short-form scoring (for example `08-081`) is fine for **reading** accuracy. For **linking**, a reference is **type-aware**: a PO-type reference may link only to a PO and a GRN-type reference only to a GRN. Both `PO/26-27/08-081` and `GRN/BCSL/25-26/08-081` can exist, so **a short number alone never auto-links**; a reference whose type is unclear stays a suggestion. Common misreads are normalised before matching (for example `26-27108-081` to `26-27/08-081`, the slash read as "1"), and each normalisation is logged with the original text.
- **Test case (owner, 21 Oct, D88):** the STEP 3a report lists every invoice where a short number matches both a PO and a GRN in the pool, and what the matcher did with it (auto, suggested or nothing).
- **Results (D89, 21 Oct; disposable database `rb-d4f-pg`, no model call; harness `work/tools/d4f_*.py`, reports `work/accuracy-intake/d4f/`, private).** Hard zero: **0 false auto-links, 0 false suggestions** in every arm run (v1 A and B, v1 B sign-normalised, v2 A and B with and without normalisation). Per invoice (both arms, both harness versions): S01 unlinked (service voucher, correct); S02 PO and GRN missed (arm A: 7 candidates, CONTENT_AMBIGUOUS); S03, S04, S07 missed (no candidate); S05 PO **suggested** (printed PO, reverse), GRN missed; S06 missed (correct PO 0.77, below threshold); S08 GRN missed (July PO outside the pool). Invoice links: 1/13 found (suggested), precision 1/1. Truth GRN to PO: v1 6 suggested; **v2 4 auto + 2 suggested** (S05, S07 suggested; S08 has no PO). Pool GRN to PO, v2: 191 auto/labeled + 35 suggested, all agree with `REFERENCE`; 24 unlinked. Ambiguous short number (D88 test): S05 `08-222` names both PO/26-27/08-222 and GRN/BCSL/25-26/08-222 (a GRN of another PO); the matcher linked only the PO (suggested). Handwritten-only links: 12 of 13 expected numbers; 0 found (OCR reads no handwriting; Qwen v4 typed one reference, S08's July PO, and wrote S04's PO and GRN only as untyped notes). Re-scans (arm A): **5/7** caught, 2 second invoice documents (S07: the case is then marked `duplicate`; S04: the re-scan's invoice number was not read, the original gets `DUP_SUSPECTED`), 0 links from them. After the re-scans S05's current version (its re-scan) reads the printed PO cleanly and S05 gets 3 correct auto/labeled line links to PO 08-222, the only auto invoice links in the run. S05 partial delivery: the 2 undelivered PO lines show ordered 1, received 0, billed 0, open 1 (pending, not a mismatch, not fully matched); the 3 delivered invoice lines do not align (freight loaded, WI-R2) and PO line 4 shows received 2 against ordered 1 (WI-R1). S05 stray page: rules + OCR take no value from page 3 and raise `ATTACHED_EWAY_BILL_OTHER_DOCUMENT` on both copies; Qwen classifies page 3 as an e-way bill but on the original copy takes **taxable total from page 3** (the other invoice's value, unverified; MG-11). Runtime: mapping 18.6 min per pass (v1, two runs in parallel), v2 about 24 + 8 + 8 min plus 12 min ledger rebuild per arm (four runs in parallel); pool ingest about 42 s; vendor resolution about 5 s.
- **Sign-normalised diagnostic (D89; "harness sign-normalisation, not app behaviour"):** the harness turned 455 PO and 456 GRN negative line amounts into positive magnitudes after parsing (logged per field: `line.amount` only). Every table above is identical with and without it (v1 arm B; v2 arms A and B). The earlier hypothesis that the sign blocked the GRN reference check (D89 records the correction) did not hold; the blocker was the single mapping pass of harness v1 and the ledger dependence described in WI-D4.

#### WI-D6 — AC-3 page-split integration defect
**Open** · S–M · Deps: disposable PG · Approval: N
`MultipleResultsFound` in `_current()` with duplicate PO rows (D57: 2 failures in `test_page_split.py`). **Acceptance:** `test_page_split.py` all pass; no other integration group regresses.

#### AC-8b — Identity and re-scan guard: close or accept
**Implemented, gate open** · M–L · Deps: better extraction (PH2/PH4) · Approval: Y (accept a residual)
Bench: 8 extra documents (GRN 3, PO 3, invoice 2), 0 wrong merges, duplicates 0 of 2 caught (D55). Re-measure on Qwen-merged documents; close the gap or record the owner-accepted residual (the exit says "re-scans make 0 new documents").
- **Pool evidence (D95, native import):** re-scan pairs still **5/7** caught (S07 and S04 missed as in D89); the two new invoice documents now get content links (7 rows, suggestions), and after the re-scans the reconciliation gives 10 cases (6 unlinked, 3 needs review, 1 duplicate).
- **Pool evidence (D89):** re-scan pairs **5/7** caught (the re-scan becomes a version of the original document; the bench caught 0 of 2). The 2 misses: **S07** (RESCAN-4) made a second invoice document and the reconciliation then marked a case `duplicate`; **S04** (RESCAN-7) made a second document because OCR did not read its invoice number, and the original only gets `DUP_SUSPECTED`. No link came from either new document.

#### AC-9b — Matching v2 app/UI
**Not started** · M · Deps: WI-D4 · Approval: N
Score explanation in `mapping_service.py`, one-click confirm of suggestions (a generic Confirm exists, `MappingPage.tsx:244`); fixes B6 (confirmed content link becomes suggested again after a changed input hash) and B10 (manual evidence text differs).

#### AC-10a/b — Vendor profile (learning loop)
**Not started** · L → **AC-10a** core profile (number pattern, where the PO ref sits, tax type, usual items; V-DOCNO-PATTERN) and **AC-10b** table/app/UI · Deps: AC-9b, AC-7 corrections, Tier H · Approval: N
Migration for `vendor_profile`; used as validators and hints; the look-alike exception for a document number only when the vendor pattern accepts it. **Acceptance:** repeat-vendor documents need fewer touches (MT, measured on Tier H unseen-vs-seen vendors).

#### AC-12 — GSTR-2B cross-check (optional)
**Not started, optional** · M–L · Deps: D-10, AC-6b · Approval: Y (D-10)
Import the monthly GSTR-2B JSON; a matching row makes supplier GSTIN, number, date and values L0; also lists invoices never received.

#### WI-R1 — PO-ledger over-receipt from charge lines (new, D89)
**Open, do not build yet** · S · Deps: none · Approval: N (code)
- **Problem / evidence:** on S05 (PO 08-222) the GRN's courier-charge line, which is not on the PO, was aligned to a PO goods line: the PO ledger shows received 2 against ordered 1 on that line (D89 pool run, both arms).
- **Approach:** charge lines (courier, freight, packing; `line_type = charge` or a charge keyword) never count as received or accepted quantity on a PO goods line; they stay separate charges. **Test:** a synthetic PO with one goods line and a GRN with the goods line plus a courier line; the ledger shows received = ordered, the charge is recorded separately.
- **Acceptance:** the test passes; on the pool run S05's PO lines show received ≤ ordered; no other ledger figure changes.

#### WI-R2 — Freight-loaded invoice lines (new, D89)
**Open, do not build yet** · M · Deps: WI-R1 · Approval: N (code)
- **Problem / evidence:** S05 and S08 spread freight onto each invoice line (line amount = PO/GRN amount + freight share) while the GRN carries a separate courier line; on S05 the 3 delivered invoice lines do not align (`not_on_po`/`unsure`).
- **Approach:** align on quantity and rate, and on amount net of the freight share when the invoice's freight column or the GRN's charge line explains the difference; record the freight as a charge variance, never as a price variance.
- **Acceptance:** S05's 3 invoice lines align to PO lines 1, 4 and 5 with the freight explained; S08 likewise; no new false alignment on the dev set.

#### WI-PERF1 — Matcher runtime (new, D89)
**Done (D98, 22 Oct; owner's queue): targets met on the D86 pool with identical outputs** · M · Deps: none (profiling is read-only) · Approval: N (code)
- **Profile (D98, `work/accuracy-intake/q3/perf1-*.txt`):** mapping pass 1,053 s under cProfile, of which 1,048 s in `_reverse_matches` → `_po_matches` (every text window compared with every PO by canonicalising both: 98 million `canonicalize_po_number` calls); ledger drain 362 s for 251 POs, each reloading all current documents (125,751 `_make_document` calls) and recomputing the governing POs (about 68,000 queries).
- **As built (D98):** `mapping/links.py`: `_po_index` (POs by canonical number, snapshot order kept) used by reverse matching; `_canonical_text` and `_without_leading_label` memoised (pure functions of the text, `lru_cache` 65,536). `ledger_service.py`: `_CurrentSnapshot` loaded once per `drain_dirty_scopes` batch (documents and governing POs do not change with ledger writes; a single `rebuild_po` still loads its own; the documents are read once before the per-PO locks instead of after each). No version change: outputs identical (rides on 0.1.9 of D96, unreleased). Patch `work/patches/t7-perf1.patch`.
- **Result (D98):** pass 1 mapping 26 s and ledger 5 s, pass 2 mapping 18 s, converged; whole arm A run including ingest (116 s) and re-scans 215 s (was about 2,800 s); current links 595/595 (arm A) and 589/589 (arm B) and ledger rows 455/455 identical to the task 4 run, compared by document numbers, states, methods and confidences.
- **Problem / evidence:** `MappingService.resolve_workspace` took **18.6 min** for one pass over the 509-document pool (251 POs, 250 GRNs, 8 invoices; D89), and the worker sequence needs at least two passes plus a ledger rebuild (about 12 min). The worker runs a whole-workspace mapping after every uploaded file, so one real month would repeat this hundreds of times.
- **Approach:** profile one pass (likely hot spots: the source-text scans in reverse matching and `_contains_canonical_reference`, the all-peers loop in via-GRN resolution, per-PO content scoring); index candidates by vendor and canonical number; resolve only documents whose inputs changed; keep the outputs identical.
- **Target (owner, D90):** one mapping pass **under 2 min** and the full worker sequence (mapping, ledger rebuild, mapping) **under 5 min** for one real month, the D86 pool as the benchmark, with **no change to link results**. (Earlier proposal: one real month (the D86 pool) in **under 2 minutes** per mapping pass on the development machine, with byte-identical link output against the D89 run; the pool run is the benchmark.)

### 7.8 Hygiene, owner items and the gate

#### WI-D3 — Version guards and bulk re-parse
**Open** · S–M · Deps: none · Approval: N
Parser-hash guard (`PARSER_VERSION` must change when extraction code changes; `scripts/check_parser_version.py`, `PARSER_HASH`), extended to vision-check/consensus code (`CONSENSUS_VERSION`), comparison base = last release tag (§4.12), retroactive `consensus.v2` bump for D77, `reparse_outdated` CLI (dry run by default, owner-triggered per workspace, FP DP-8). **Acceptance:** CI fails when guarded code changes without a bump; an integration test proves a re-parse keeps manual corrections and verified refs.
- **WI-Q2 (D92):** the vision check now accepts `service_po_number` and `vision_document` adds `typed_refs` (`handwritten_refs.v1` stamped per reference); the mapping change bumped `ENGINE_VERSION` to 0.1.8. The vision-check version itself is not stamped on rows yet: this card does that.

#### WI-QR1 — E-invoice NIC public key (owner)
**Open** · S · Approval: Y — signed QR is L0 only when `RB_EINVOICE_PUBLIC_KEY_FILE` is supplied (bench shows `signed 0`, D34). Check whether the app has it.

#### AC-7c — Verify-screen timing (owner)
**Open** · S · Approval: Y — time the "verify a document in under 30 s" target (D50: never timed).

#### AC-11 — Real-set end-to-end and the Phase 4 exit gate (= PH7)
**Not started** · L + owner · Deps: PH4, PH5 · Approval: Y
Runs the full path in the live stack on Tier S (and a full real month if available); reports MT-13; closes or carries B4 (payment terms not applied to the due date), B12 (Vendor Summary counts Needs Review inside Exceptions; auth tests not in hosted CI; flaky 10k-row frame budget) and the main plan's exit-gate items; updates [`gates/phase-4.md`](gates/phase-4.md).

### 7.9 Frozen work (D72) and its unfreeze condition

| ID | Content | Status | Unfreeze condition |
|---|---|---|---|
| WI-D1 (= fix-plan WI-4 … 7) | doc-no/PO-ref choice, vendor name, totals/tax, line tables for the rules reader | **frozen** | owner reopens, with Tier H showing OCR-side misses that Qwen cannot cover |
| WI-D2 (= fix-plan WI-8 steps 3, 4, 5) | force OCR on poor/tiled text layers; > 20-page warning; UTF-8 decode | **frozen** | same |
| WI-D2b (= fix-plan WI-8 steps 1, 2) | low-confidence header/table re-OCR; GSTIN checksum-guided repair (target: 4 GSTINs missing in both readers) | **frozen** | same; step 2 first if GSTIN misses persist on Tier H |
| WI-D2c (= fix-plan WI-8 step 6) | skew > 5°, dewarp, upscale | **frozen, conditional** | ≥ 5 documents of that scan type in Tier H with OCR-absent misses |

---

## 8. Dependencies, critical path, parallel tracks

```
WI-D5 (owner, longest) ───────────────────────────────────────────────────────────────┐ held-out for PH4, PH7
WI-EV3 ─► gate command for everything
WI-T1, WI-INT0 (read-only part)
WI-D4f (full-pool linking arm; dev tool; needs the owner's go) ─► WI-D4, AC-8b, AC-9b evidence
WI-IMP1 (native Tally import; product task, before AC-INT1; not approved to build)
WI-D4 ─► WI-PERF1 (same file area) ;  WI-R1 ─► WI-R2 ;  WI-Q2 output-type fix ─► WI-BE8 (both touch vision.py)
WI-PV1 ─► WI-PV2 ─► WI-PV3 (O-18) ─► WI-PV5 ─► WI-PV6, WI-PV7 ─► WI-PV8        (PH2b: connect, select, secure)
WI-PV3 ─► WI-PV3b (role assignment command) ─► AC-INT1b (roles set after backup)
        └► WI-PV4 (O-19/O-20) ─┘               └► WI-EXP1, WI-BE9a, WI-FE1a ─► WI-FE1b ─► WI-FE3
WI-BE8 ─┐
Gemini arms E1–E4 (owner go/key/budget) ─┴► WI-EV2 side by side ─► WI-BE7a ─► BE7b ─► BE7c ─► WI-G3
WI-V1, WI-V2 ─► WI-BE7a ;  WI-Q1/Q2/Q3 (after BE8) ─► arb.v1 per provider
PH2b exit + AC-INT1a (dry-run to the new head) + WI-INT0 ─► AC-INT1b ─► AC-LIVE-S (Qwen shadow, Gemini stays live)
WI-BE9b ─► WI-BE9c ;  WI-OPS1 ─► WI-OPS3 ;  WI-OPS2 ;  WI-D3 (release tag base)
shadow ≥ 14 d and ≥ 100 ─┐
DP-L1 numbers per provider, WI-FE2, WI-G3, WI-BE7 ─► AC-LIVE-M (default provider = O-22) ─► AC-11 (PH7)
WI-D4 ─► AC-9b ─► AC-10a/b ;  WI-D6 ;  AC-8b (after PH2/PH4) ──────────────────────────► AC-11
```

- **Connect/selection work comes before AC-INT1** (PH2b), because AC-INT1b deploys the migration and connects Qwen through the UI, and AC-LIVE-S needs the live + shadow model.
- **Critical path:** WI-PV1 → WI-PV2/3/5 → WI-PV8 (PH2b) → AC-INT1a → AC-INT1b → AC-LIVE-S → shadow period → AC-LIVE-M → AC-11, with WI-BE8 + Gemini arms → WI-EV2 → WI-BE7a/b/c converging on AC-LIVE-M. The longest chain outside engineering is WI-D5 (80 labelled invoices), which gates every per-field claim at PH4.
- **Parallel tracks:** A truth (WI-D5); B reading quality and provider evaluation (BE8, Gemini arms, V1, V2, Q1–Q3); P provider platform (PV1 … PV8, BE9a, EXP1, FE1a/b, FE3); C integration (T1, INT0, INT1a, OPS1–3); D matching (D4, D6, 9b, 8b, 10); E hygiene (D3, QR1, 7c).
- **One writer per file area.** BE8, V1, V2 and BE7a all touch `reconbench_core/extraction/vision.py`; WI-PV1, PV2, PV3 and BE9a all touch `ai/settings_service.py` and `ai/consent.py`; each group runs one task after another. A task that shares a file with another running session's work stops and reports.
- **Why AC-INT1 sits before WI-BE7b:** shadow mode and the merge job cannot run on images that lack the code; WI-BE7a (pure core, flag off) can proceed first.

---

## 9. Owner decisions

### 9.1 Decided on 20 Oct 2026 (recorded in D78)

| ID | Decision |
|---|---|
| O-1 | Two-tier truth: Tier H ≈ 80 invoices (header-only), Tier S 8–10 complete sets; ~5 sets = indicative only until then; labelling protocol and split in WI-D5. Arithmetic note: 80 error-free values give a 95.4% lower bound; one error needs 110 (escalation batch planned). |
| O-2 | Auto-accept thresholds accepted, plus: critical-field auto-accept only when (Qwen and OCR agree) or (a deterministic validator confirms), in addition to the lower-bound threshold, 0 critical wrong and a shadow period. DP-L1 arithmetic corrected (§9.3). |
| O-3 | (amended D80) D-8 cost caps are required per provider before that provider's merge is enabled: the Gemini monthly cap (O-24; dormant while the Gemini key is free-tier, D83) is a required field at connect and Qwen has a GPU-time budget. D-8 no longer "gates Gemini production only", because Gemini stays connectable and selectable. |
| O-4 | D72 lifted for WI-V2 only; WI-D1/D2 (and D2b/D2c) stay frozen. |
| O-5 | Version rule: `CONSENSUS_VERSION` (or a check version) for vision/consensus changes; guards compare against the last release tag, not HEAD (§4.12). |
| O-6 | (rewritten D80) No silent automatic failover. If the selected provider is down or disconnected, new documents fall back to rules + OCR with a visible flag, the UI shows the provider's status and offers a manual switch, and routing to the other provider happens only with the explicit setting `allow_provider_fallback` (default off, consent required, every affected document flagged). |
| O-7 | `audit-llm-extraction.md` is retired after its findings are merged (§4.13, §12, §13); archive copy per Option B. |
| O-8 | Live rebuild/migration approved only inside AC-INT1: full DB backup, a tested restore, and a dry-run 0024→0027 on a copy first. No rebuild in Stages 2–3. |
| O-9 | Carried items keep their existing defaults (B4, B12, FP DP-6/7/8, FP DP-4). |
| DP-L1 | Option (A) for first enablement: Wilson lower bound ≥ 95% with n ≥ 73 error-free per field type, 0 critical wrong, plus the O-11 circuit breaker. Option (B) (lower bound ≥ 98%, n ≥ 189 error-free, precision ≥ 99.5%) stays the target for wider auto-accept. Arithmetic corrected: the retired text asked for a 98% bound on "≥ 100 values", which is impossible (100 error-free values give 96.3%). Shadow agreement is not accuracy: correctness evidence comes only from labelled sets and verify-screen corrections. |
| O-10 (narrowed) | The validator-only path is allowed only for (i) supplier GSTIN: checksum passes and not the buyer entity's GSTIN and equals the vendor-master GSTIN for that vendor, otherwise agreement-only; (ii) amounts: the full chain closes within rounding (Σ lines = taxable and taxable + tax = grand total), partial closure is agreement-only. It amends D42 rule 1 for these two cases only (§2.3). |
| O-13 | **Superseded by D80.** Gemini is not turned off and there is no `vision_only` mode. WI-BE9 is reworked as provider-agnostic hooks (WI-BE9a/b/c). |
| O-14 | (amended D80) Shadow-only back-fill of the 40 live documents, applied **per provider** (the back-fill command takes the provider in shadow). AC-LIVE-S does **not** turn off Gemini page-image reading; Qwen shadows while Gemini stays the live default. |
| O-15 | The first release tag is created at AC-INT1b. |
| O-17 | AC-8b: close the gap or accept the residual (8 extra documents, 2 missed duplicates): decide after PH4. |
| PH2 interim targets | Dev headers ≥ 92%, scan-page line amount ≥ 85% (quantity and rate ≥ 80%), 0 documents lost to the output cap, accepted-wrong 0: pre-registered in §6 before any PH2 run. |
| Change of requirement (D80) | Both providers are supported and selectable. An admin connects each from the frontend (credentials per workspace, encrypted, never returned, admin-only, audited, allowlist-validated); a provider is Connected only after a successful server-side test; workspace defaults for extraction and for assist, a per-upload override, and re-run with the other provider (never overwrites); provenance (provider, model, prompt version) on the document page, review screen and Excel export; scope: page-image extraction and the text hooks H1–H4; one safety contract measured per provider (DP-L1, circuit breaker, hard zeros); Gemini arms E1–E4 move from PH6 into PH2 and are reported side by side with Qwen; Gemini is not turned off; no silent failover; `dual_agree` stays excluded; a manual "compare both" view is an optional PH6 item; environment = kill switch + allowlist ceiling, database = per-workspace connections. |
| O-18 (accepted) | `ai.manage` is enforced from the stored role, the bootstrap user first. This departs from the P0 policy that maps every user to manager (`authorization.py:93-99`); the departure is recorded in the decision log (D81) only; the main plan is not edited. |
| O-19 (accepted) | Endpoint allowlist: environment ceiling plus admin-editable workspace entries inside it, audited. |
| O-20 (accepted) | No Qwen URL outside the company VPN. |
| O-21 (accepted) | Fallback to the other provider is off; if ever enabled: consent for the other provider's data destination and a flag on every affected document, never silent. |
| O-22 (accepted) | Default provider after AC-LIVE-M: the provider with the higher lower bound on critical fields and 0 critical wrong; within 2 points the owner chooses. |
| O-23 (accepted) | Additive trailing `Provenance` sheet in the workbook, after checking the export contract in the read-only specifications. |
| Per-provider auto-verify (accepted) | Each provider's auto-verify list starts empty. |
| O-24 / D-8 Gemini cost caps (accepted, values set by the owner 20 Oct) | **Dormant while the Gemini key is free-tier (D83, O-25):** the mechanism stays built and tested and starts when a card is declared paid. Live monthly cap per workspace **₹2,500** (about US$30); the UI warns at **80% (₹2,000)**; at **100%** new Gemini calls stop and documents fall back to rules + OCR with a visible flag, **never a silent switch to Qwen**; the cap resets on the **1st of each month (IST)**. Usage = token counts returned by the API × the pinned model's price, stored per call. **Pinned model `gemini-3.5-flash-lite`** (the one live today); any other Gemini model needs the owner's approval and a cap re-check. Prices live in configuration, not in code. A single document whose estimated cost exceeds **₹20** is flagged. Working price assumption (unverified, see §3.7): US$0.30 per 1M input tokens, US$2.50 per 1M output tokens, about US$0.006–0.010 per page. |
| Gemini bench arms E1–E4 (approved, budget set by the owner 20 Oct) | **Changed by D83 (free tier):** the budget is requests per day, a separate free-tier bench key from `BENCH_GEMINI_KEY`, E3/E4 not approved (WI-EV2); the rupee figures below apply only to a billed key. Total budget **₹2,000** (about US$23) for all Gemini bench runs combined. Before each run the cost is estimated (pages × arms × repeats × per-page cost) and logged; if one run's estimate exceeds **₹500**, or the total would pass ₹2,000, the run stops and the owner is asked; actual spend per run is logged from returned token counts and the run stops at the cap. E3 and E4 name `gemini-3.8-flash`, which is not the pinned model: they need the owner's separate approval and a cap re-check before they run. The Gemini key is entered by the owner through the connect flow, never in chat, files or committed environment; the bench key route is an open question (WI-EV2). |
| O-25 (D83, changed by D84) Gemini tier and data use | Gemini runs on the **free tier** (`gemini-3.5-flash-lite`, no billing on the Google project): cost ₹0, the O-24 cap is dormant, rate limits are the constraint (§4.15.9). **The live sealed key is free tier (owner checked in Google AI Studio, 20 Oct); D-1's "paid" was wrong, and real documents on the live stack have been sent under free-tier data-use terms.** On the free tier Google may use prompts and responses to improve its products and human reviewers may read them. **Owner decision (20 Oct, D84): free-tier data use is accepted for real company documents, including live invoices; there is no restriction to test/sample documents** (this replaces the D83 default). The key's tier is declared at connect (the API cannot tell). The free-tier consent notice at connect stays and states the data use explicitly; it is part of the consent fingerprint. The masked dev set may go to the free bench key for E1/E2 (owner, 20 Oct). The live stack is not changed by this decision. |
| O-26 (D83) Unit of evidence | Every per-field `n`, Wilson bound and gate counts **unique documents**; copies and re-scans of one document share its split and count once; "with copies" is extra information only (§2.5, §3.2a, §5.1, WI-D5, WI-EV3). PH2/PH4 gates restated in §6. The strict aggregation across copies (correct only if every copy read is correct) is my proposal in D83; the owner may change it. |
| Recoverability | Option A′ (docs-only commit, explicit pathspecs, tag `pre-plan-merge-20261020`) **and** Option B (copy to `work/plan-archive-20261020/`, tarball `~/reconbench-archive/plan-archive-20261020.tar.gz`). Nothing else is committed. |

### 9.2 Old owner decisions D-1 … D-10 (status)

| ID | Decision | Status |
|---|---|---|
| D-1 | Masked page images to Gemini | decided 7 Oct (assumed paid, `store=False`; **the live key is in fact free tier, D84**); D39 allows the free tier for testing only; Gemini stays connectable (D80), so any production use on real pages needs the egress notice accepted at connect; the owner accepted free-tier data use for real documents (O-25, D84) |
| D-2 | Machine-verified without a person | decided D42; amended in scope by O-2 |
| D-3 | AI extraction for PO/GRN | decided 7 Oct (vision reader; suggestions until corrections exist) |
| D-4 | `buyer_entities` | resolved: configured in the live workspace (1 entity, count only); warning exists (`SettingsPage.tsx:240`) |
| D-5 | Marketplace vendor rule | decided D36: seller GSTIN is the vendor |
| D-6 | Non-PO matching | decided: suggestion-only; enforced in AC-9a |
| D-7 | Truth set 15–20 sets | **superseded by O-1** |
| D-8 | Cost caps (Gemini monthly USD; GPU-time budget for Qwen) | open; **required per provider before that provider's merge**; the Gemini cap is a required field at connect (O-24; dormant on the free tier, D83) and its value is the owner's |
| D-9 | Bundles: suggest the split | decided D35 |
| D-10 | GSTR-2B verifier (AC-12) | open, optional |

### 9.3 Open decisions (each with options, recommended default and what stays blocked)

DP-L1 and O-10 … O-16 were decided on 20 Oct, and O-18 … O-24 on 20 Oct (second answer, D81); all moved to §9.1. O-17, the numeric value of the Gemini price (§3.7) and the carried items remain open.

| ID | Decision | Options | Recommended default | Blocked until decided |
|---|---|---|---|---|
| O-17 | AC-8b: close the gap or accept the residual (8 extra documents, 2 missed duplicates) | close / accept with numbers | decide after PH4 measurement | AC-8 phase closure, AC-11 |
| DP-L4 | Approvals per run | standing approval vs one go per run | one go and a log entry per run (vLLM budgets set by the engineer; Gemini budgets by the owner) | each server run |
| DP-L5 | Gemini tier for production | paid key / free tier / not used | **decided:** free tier accepted for real documents (O-25, D84) | — (closed) |
| DP-L6 | Pin vLLM image and Qwen revision | pin / record fingerprint | the server is shared, so record `/version`, `/models` and the canary digest (WI-OPS3); pin only if the admin agrees | none |
| DP-L7 | Default provider for new workspaces | none / Gemini / Qwen | none (rules + OCR) until an admin connects and selects; a provider is merge-enabled only after ≥ 14 days of shadow | AC-LIVE-S |
| DP-L8 | Local model for PO/GRN | yes / no | yes in shadow, same gates | AC-LIVE-M for PO/GRN |
| DP-L9 | Budgets: GPU time (Qwen) and monthly cap (Gemini) | owner sets | before a provider's merge enablement | AC-LIVE-M |
| DP-L10 / FP DP-4 | Is "correctly flagged for a person" acceptable where no reader is sure | yes/no per class | yes for handwriting; decide printed fields after WI-EV2 | AC-11 wording |
| FP DP-3 | Delete `scripts/measure_extraction.py` (deprecated stub, exit 2) | delete / keep | delete after WI-EV3 gate exists | none |
| FP DP-6 | Files over 20 pages | support / explicit warning | warning (frozen with WI-D2) | WI-D2 |
| FP DP-7 | GST slab list (40% missing; seed replaces the parser default; 12/28 kept for old documents) | owner tax policy | owner supplies | GST slab validators |
| FP DP-8 | Bulk re-parse after a parser bump | per file / bulk per workspace | bulk, owner-triggered | WI-D3 CLI use |
| B4 / B12 | Payment terms not applied to the due date; Vendor Summary counts Needs Review in Exceptions; auth tests not in hosted CI; flaky 10k-row frame budget | carry to AC-11 | carry | AC-11 |

Answered or superseded earlier decision points: DP-L2 No (two LLMs are one reader kind); DP-L3 resolved (company vLLM server; the owner approved real invoices on 16 Oct, first run recorded in D68); FP DP-1 (Gemini experiment) → PH6 optional arms; FP DP-2 done (buyer entities set); FP DP-5 → O-1; FP DP-9 = D-8.

---

## 10. Risks, red-team results, operations

### 10.1 Red-team findings (Pass 4) and what changed in the plan

| ID | Attack | Finding | Fix in this plan |
|---|---|---|---|
| RT-1 | A wrong value silently accepted through the validator-only GSTIN path (a checksum-valid GSTIN of the ship-to or a transporter) | real risk | the validator-only GSTIN path requires checksum, not the buyer entity, and equality with the vendor-master GSTIN (O-10, narrowed 20 Oct); crop re-read and the lower-bound gate still apply; all other cases are agreement-only |
| RT-2 | Qwen and OCR agree and are both wrong (shared look-alike) | agree-wrong 0 in 243 on the dev set but D41 had one (Gemini vendor name) | look-alike guard stays; crop re-read; agree-wrong tracked on Tier H; DP-L1 bounds apply to accepted values |
| RT-3 | Review cleared because the rules' lines are wrong (S08-INV) | seen (D75(e)) | MG-4 |
| RT-4 | Order date taken as document date | seen under v5–v7 | OCR leads dates; disagreement never auto-accepts |
| RT-5 | Hallucinated buyer GSTIN (v7, S03-INV) | seen | V-NOTINOCR + crop re-read; MT-10 tracked |
| RT-6 | Cached answers from before a server change mixed with later ones | one drift event documented | WI-OPS3 epochs; no cross-epoch comparisons |
| RT-7 | Merged description text accepted | 40 wrong of 108 | RULE-DESC, MG-6 |
| RT-8 | A change regresses the rules/OCR path | the conflict rule (D77) also changes Gemini suggestions; WI-V1/V2 act only on LLM values | flag-off hash identity (§5.3); version guards |
| RT-9 | Targets that cannot be measured with the current truth set | per-field proof for 4 critical fields plus vendor name; links (10 pairs); reconciliation and export accuracy; descriptions on held-out; auto-verified rate in the app (consensus never ran) | §5.4, WI-D5, WI-INT0, MT-12/13; shadow agreement is not accuracy |
| RT-10 | A task too big for one session | WI-BE7, WI-FE1, AC-10, AC-INT1, AC-LIVE, WI-EV3, WI-BE8 | split into BE7a/b/c, FE1a/b, AC-10a/b, INT1a/b, LIVE-S/M, EV3 (two sessions), BE8 (two steps) |
| RT-11 | Qwen down for a week | no fallback signal today | §4.8: documents processed on rules + OCR and flagged; queue skip after 5 min; `llm_coverage` metric; owner chooses back-fill |
| RT-12 | An old item dropped | fix-plan WI-8 steps 1, 2, 6; WI-3 steps 4–5; AC-6c exit; DP-L1 arithmetic; D43; AC-L labels | all homed in §13 (steps 1, 2, 6 → WI-D2b/D2c frozen; WI-3 steps 4–5 → done and verified) |
| RT-13 | Migrating the live database; shared containers | real | O-8 conditions, AC-INT1a, announced window, previous images retagged |
| RT-14 | Privacy: pages leave the machine | masked images go to the company server over the VPN; owner approved real invoices on 16 Oct (retired LLM plan §0.5; D68 records the first run) | consent re-fingerprint, endpoint allowlist, no key in responses |
| RT-15 | Truth labels biased by the system's output | prefill invites agreement | blind labelling for Tier H (WI-D5) |
| RT-16 | Held-out contamination | any tuning on it | O-16, look log, retirement rule |
| RT-17 | DP-L1 internally inconsistent | confirmed | corrected (§9.3) |
| RT-18 | Version guard vacuous (HEAD 0.1.4 vs tree 0.1.7) | confirmed | release-tag base (§4.12, WI-D3) |
| RT-19 | "Gemini off" impossible while Qwen vision is on | confirmed in code; **superseded 20 Oct (D80): Gemini is not turned off** | provider-agnostic hooks, WI-BE9a/b/c |
| RT-20 | Consensus never ran on live data | confirmed | WI-INT0 |
| RT-21 | A standing red test hides a rules-path regression | open since D62 | WI-T1 |
| RT-22 | Credentials leak through the new surfaces (logs, errors, exports, audit, reports) | more endpoints and a UI multiply the paths | sealed storage reused; WI-PV8 scans every surface for the posted key |
| RT-23 | "Admin only" is not enforced | verified: every persisted user is a manager (`authorization.py:93-99`) | O-18, WI-PV3 |
| RT-24 | SSRF through an admin-editable allowlist | database entries widen the attack surface | environment ceiling, loopback/metadata refusal, https-or-approved-internal rule, audit (WI-PV4, WI-PV8) |
| RT-25 | Disconnect or rotate mid-run corrupts state | answers could be applied after the key is gone | request re-check before apply, discard, test-then-swap rotation (WI-PV2, WI-PV8 test 4) |
| RT-26 | Switching provider silently changes stored data | a re-run could overwrite a reading or version | re-run stored as an alternative, current changes only by the audited correction save (WI-PV6, WI-PV8 test 5) |
| RT-27 | Auto-verify enabled for a provider without numbers | the live workspace-wide list names five types, set before any measurement | per-provider lists start empty (WI-PV1, §2.6) |
| RT-28 | Failover sends documents to an unexpected destination | Gemini egress versus company network | no failover; the optional setting is off, needs consent and flags documents (O-21) |
| RT-29 | Live plus shadow coexistence breaks the live flow or writes documents | shadow was designed to replace the live reader (D65) | D66 invariants kept, WI-PV5 coexistence test, AC-LIVE-S acceptance compares Gemini behaviour before and after |
| RT-30 | The Settings "measured better" note misleads | small n, different prompts or server epochs | counts, n, date and epoch shown; the user decides |
| RT-31 | Provenance breaks the Excel contract | the workbook layout comes from the read-only specifications | additive sheet only, contract check first (O-23) |

### 10.2 Risks

| Risk | Mitigation |
|---|---|
| Shared vLLM server is slow, changes or disappears | canary and epochs; fallback; health banner; no dependence on latency for correctness |
| Qwen gains come with hidden regressions on another field | one variable per run, same-day control, paired field-group comparison (§4.7) |
| Over-flagging from new validators raises review load | MT-9(b) tracked; false-reject must be 0 on dev for V-HSN |
| Small samples mislead | counts with denominators; Wilson bounds; claim rules (§2.2) |
| Many uncommitted changes lost | Option A′ snapshot + Option B archive for plan docs; the code tree still needs an owner decision to commit |
| Consent text drifts from behaviour | fingerprint tests pinned per variant (D65–D77) |

### 10.3 Operations and monitoring (from AC-LIVE-S onwards)

Metrics (each per provider): `llm_coverage`, share of documents flagged `LLM_UNAVAILABLE`, vLLM health and last-failure code counts, cap-hit rate, drop reasons, per-field Qwen×OCR agreement, p50/p95 latency, tokens, canary digest and epoch, consensus runs vs applied vision answers (must be equal), `critical_false_accept` (a person correcting an auto-accepted field is an incident), reviews cleared without Σ lines = taxable (must be 0). Status codes appear in Settings and Home; counts only, never values.

### 10.4 Stale artefacts to know about

- `CODEBASE_CONTEXT.md` (repo root, untracked) is a **9 Oct snapshot** and predates D59–D78 (it still says link recall 3/10 as its P1 item and does not mention the vLLM path). It is not edited by this consolidation; regenerate it or treat it as history.
- [`gates/phase-4.md`](gates/phase-4.md) was last edited 5 Oct and does not reflect the accuracy work; Stage 3 adds a pointer section only.
- The auto-memory note for D77 ("in progress") was stale; D77 is complete.

### 10.5 Accepted gap

B11: same-type scanned documents inside one file split only on "Page 1 of N" (D35). Not planned; revisit if Tier H contains such a file.

---

## 11. Session protocol

- Each session reads §0, §2 and the task card; does one task (or a documented split); updates §0 (status line, last decision) and appends **one** entry to [`decision-log.md`](decision-log.md) (counts only, no real values); the plan never copies the main plan's task list.
- Never `git add`, commit, push, stash, reset, clean, switch branches or open pull requests unless the owner asks; the tree holds weeks of uncommitted work shared with other sessions.
- [`ReconBench_*.md`](ReconBench_Implementation_Plan.md) are read-only. `work/` is private: read bench reports for counts and labels only; never print or copy truth, samples, cached answers or page images; tests use synthetic data only.
- Never edit `.env` or `deploy/secrets/**`; read `.env` key names only. No model call or benchmark without the go and budget logged in the decision log first. Stop and report when a check fails for an environmental reason, an owner decision is needed, or a flag-off hash changes unexpectedly.
- Verification order: static checks, offline suites, migration safety (if a migration), disposable-PG integration groups, OpenAPI regeneration (if an API changed), web checks, bench. Never claim a check that was not run.

**Runbook (Windows host; PowerShell has no `&&`; `uv` is not on PATH; use the venv tools; Node 24 for web via `C:\Users\jainm\AppData\Local\nvm\v24.19.0`).**

| Purpose | Command (from the repo root) |
|---|---|
| Static | `backend\.venv\Scripts\ruff.exe check reconbench_core reconbench_app tests ..\scripts` (from `backend\`); `mypy.exe --strict`; `lint-imports.exe`; `python scripts\check_core_purity.py`; `python scripts\check_rules_hash.py`; `python scripts\check_engine_version.py --base-ref <release-tag>` |
| Offline tests | `backend\.venv\Scripts\python.exe -m pytest backend\tests\unit backend\tests\property backend\tests\scenarios backend\tests\parsers -q -p no:cacheprovider` (never collect `unit` and `integration` together) |
| Disposable PG | `docker run -d --rm --name rb-ephemeral-pg -e POSTGRES_PASSWORD=phase2-ephemeral-local-only -p 127.0.0.1:15499:5432 --tmpfs /var/lib/postgresql/data postgres:16.15-bookworm`, then `alembic upgrade head`, `seed_database.py`, queue schema; migration-safety checks on a second cluster at 15497. **Never point anything at port 15432 (live).** |
| Rules hash update | after any core `.py` change: write `calculate_rules_hash()` to `backend/reconbench_core/RULES_HASH` (64 hex + LF) |
| Bench (no network) | `MSYS_NO_PATHCONV=1 make accuracy-bench BENCH_ARGS="--workers 1 --out work/accuracy-reports/<name>.json"`; cached vLLM replay needs `--vision-provider vllm --vision-prompt-variant qwen` and a model matching the stored answers |
| Bench (company vLLM, network on) | `RB_AI_VLLM_ENDPOINTS=http://43.242.226.63/vllm/v1 make accuracy-bench-vllm BENCH_ARGS="--vision-base-url http://43.242.226.63/vllm/v1 --max-provider-pages <N> --vision-eval --workers 1 --out /app/work/accuracy-reports/<name>.json"` (needs the go first) |
| OpenAPI + types | `scripts\export_openapi.py`, `npm run api:generate --prefix web`, `git diff --stat -- backend/openapi.json web/src/api/generated.ts` |
| Migration checklist | revision file with fixed-forward `downgrade`; mirror in `db/metadata.py`; workspace-owned tables get `workspace_id`; append-only triggers where required; CI previous-schema revision = head − 1; add the head to the phase 4.11/4.13 revision set; PG-SAFETY with contract regeneration at head **before** seed and queue schema; add an entry to `schema-errata.md` |

Decision-log template: heading `## Dnn — <task id> <title> (<date>)`, two to four sentences, a decision table, a before/after table with report name and first 12 hash characters, checks run and not run, open items.

---

## 12. Experiment history (what was tried and why it stopped)

| Experiment | Decision | Result | Outcome |
|---|---|---|---|
| Gemini vision v1/v2/v3, flash-lite, free tier | D37, D38, D40, D41 | v3: headers 271/313, lines 80/123, handwritten 6/18, agree-wrong 1 | kept as comparison; production use not decided (D-1/D-8) |
| Qwen E5 smoke, 5 documents | D68 | headers 72/80 vs 62/80 on 9 documents; 1 call at the output cap | superseded |
| Qwen E5 on 15 more invoices | D69, D70 | p95 244 s; 3 invoices unusable (strict parse) | led to D71, D73 |
| Page answers kept; re-read of 3 invoices | D71 | still unusable: empty `text`, over-long description | led to the tolerant parse and length limits |
| Answer-format root fix (tolerant per-item parse + `minLength`/`maxLength` in the vLLM schema) | D73 | all 3 invoices usable; D74 fresh read: 0 schema drops, headers 255/313 | **kept** |
| D72 focus decision | D72 | OCR + rules frozen except as second reader and fallback; order (1) format, (2) prompt, (3) repeats, (4) truth, (5) merge | in force (O-4 lifts WI-V2 only) |
| Prompt `v5.qwen` | D75 | headers −6, every line cell lower, 4 accepted-wrong values; marketplace 5/5, handwritten 4/18 | **rejected** |
| Prompt `v6.qwen` + repeats ×3 | D76 | accepted-wrong 1 (`doc_date`), lower amount/rate/qty; handwritten 6/16; marketplace 5/5; the server answered identical requests identically within a day | **rejected**; v4 stays |
| Prompt `v7.qwen` (marketplace sentence only) | D77 | one sentence changed the whole page: 4 accepted-wrong buyer GSTINs on S03-INV | **rejected**; led to WI-V2 |
| Cross-page conflict resolution | D77 | headers 255 → 275/313; accepted-wrong 0; 23 values marked `page_conflict` | **kept** (needs the version bump of §4.12) |
| `dual_agree`, LLM × LLM agreement, `secondary_reader`, policies P3/P4, merge rule 3, DP-L2 | owner decision §0.6 of the retired LLM plan (16 Oct); D62–D67 | two LLMs are one reader kind; no request calls both providers | **rejected / out of scope** |
| Frozen scan rules WI-4 … 8 (fix-plan) | D72 | not started | **frozen** (§7.9) |
| Gemini experiment WI-12 and vision-primary WI-13 (fix-plan) | superseded by AC-L (D62–D67); Gemini arms E1–E4 moved into PH2 by D80 | — | **WI-12 superseded**; **arms E1–E4 required** for the side-by-side matrix (they were optional in D78) |
| Gemini turned off while Qwen is on (`vision_only` mode, O-13) | D78/D79 | — | **superseded 20 Oct (D80)**: both providers supported, Gemini stays on |
| Qwen weights/quantisation arm E6, Qwen3-VL arm E9 | plan-llm | the server's weights are not ours to change; E9 unplanned | **dropped** |
| Prompt `v4.q2` on full pages (WI-Q2, one typing rule) | D92, D93 | handwritten refs 12/16 (8/16 with unread copies as misses), but against the same-day v4 control headers 217 vs 229/246, rate 64 vs 78, taxable 17 vs 23, **3 accepted-wrong** | **rejected** as the full-page prompt; kept as a bench variant; the same prompt on the page-1 header band is adopted (v4 full pages + band, 12/16) |
| WI-BE8 cropped two-pass reading (3 horizontal crops on flagged pages, merged into pass 1) | D97 | headers 99/137 vs 101/136 same-day pass 1, header amounts 24 vs 27/46, lines unchanged on unique documents, **1 accepted-wrong**, docs lost to the cap 1 → 0; 84 requests | **rejected** as the default; bench variant kept; narrower cap-only variant is the next candidate |

---

## 13. Traceability appendix

Every ID-bearing item of every merged document has a row. Status words: **done (Dnn)** · **open → task** · **frozen** · **superseded by …** · **rejected (Dnn)** · **kept (evidence)** · **carried** (stays an owner item). Source documents (all archived and listed in §14): AP = old `accuracy-plan.md` (this file's previous version); IP = `accuracy-implementation-plan.md`; FP = `accuracy-fix-plan-scan-extraction.md`; PG = `progress-accuracy-plan.md`; PL = `plan-llm-extraction-call.md`; AU = `audit-llm-extraction.md`. `diagnosis-scan-extraction.md` (Dx) is kept as evidence and is cited, not merged.

### 13.0 Notes on numbering and labels

- **D43.** IP lines 3 and 178 cite "decision-log D43" as the entry that wrote the implementation plan. No `D43` heading exists in the log (D42 → D44). It is a numbering gap: the plan it names was written on 8 Oct and sits in the file itself, so no content was lost. This plan does not cite D43 and does not reuse the number; old entries are not edited.
- **AC-L phase labels.** The decision log labels work as "AC-L0 part 1" (D62: WI-G2, G1, BE1), "AC-L0 part 2" (D63: BE5, BE4), "AC-L0 part 3" (D64: BE2), "AC-L0 part 4" (D65: BE3), "AC-L0 part 5" (D66: BE6) and "AC-L1 part 1" (D67: EV1). PL §1 puts BE5, G1, G2, BE1 and EV0 in AC-L0, BE2/BE3/BE4/BE6 and WI-OPS/FE1 in AC-L1, EV1/EV2/BE8 in AC-L2, and BE7/FE2/G3 in AC-L3; D77's remaining-work table follows PL. **This plan uses the PL labels** (AC-L0 … AC-L3) and refers to work by task ID, so the D62–D67 headings are read as dates and task IDs, not as phase membership.
- **Citation key for code comments.** About 25 files in `backend/`, `scripts/`, `Makefile` and the migrations cite "LLM plan WI-xx" or "scan fix plan WI-n" in comments and docstrings. Those IDs are unchanged: "LLM plan WI-xx" is the task of that ID in §7 and §13.5, "scan fix plan WI-n" is §13.3, and "AC-x" is §13.1. The comments are not rewritten because those files carry other sessions' uncommitted work and a comment-only commit would sweep it in; a comment sweep can follow when the code tree is committed.
- **Reading old status lines.** AP line 3 stops at D58, PG describes the tree as of 16 Oct ("107 entries, no change after 9 Oct"), and IP §1.1 stops at D58; all three are superseded by §0 and §3.

### 13.1 AP — old `accuracy-plan.md`

| Old ID | Title | New location / status |
|---|---|---|
| R1 | handwriting never read; ₹ glyph; rotated page; garbled columns | AC-5 vision done (D37); AC-1 glyph rule done (D33); handwriting remains hints → WI-Q2 |
| R2 | buyer GSTIN as vendor; label as vendor name | AC-1, AC-4 done (D33, D36) |
| R3 | O/0 and I/1 in numbers; same scan read differently | AC-6 done (D44–D47); vendor patterns → AC-10a (open) |
| R4 | totals wrong on non-standard layouts | AC-1 validators done (D33), AC-6 done; rules-side remainder frozen (WI-D1) |
| R5 | 6-page bundle as one invoice | AC-3 done (D35) |
| R6 | Tally PO/GRN labels not wired | AC-1, AC-5 done (D33, D37) |
| R7 | AI invented IGST / kept GSTIN typos / buyer as vendor | AC-1 validators, AC-6 done |
| R8 | correct AI values rejected by H1 grounding | AC-5/6 box-crop grounding done (D37, D45) |
| R9 | re-scans create new documents; duplicates missed | AC-8a done (D51); AC-8b open → O-17 |
| R10 | vendor master polluted | AC-4 done (D36); cleanup run by owner (unverified) |
| R11 | no PO number means no link | AC-9a implemented (D57–D58); WI-D4; AC-9b |
| R12 | XML shows category not item; GRN serials dropped | AC-1 done (D33) |
| AC-0 | benchmark + baseline | done (D32) |
| AC-1 | deterministic guards | done (D33) |
| AC-2 | e-invoice QR | done at L1 (D34); L0 needs the NIC key → WI-QR1 |
| AC-3 | page split + classify | done (D35); integration defect → WI-D6 |
| AC-4 | vendor hygiene | done (D36) |
| AC-5, AC-5b | vision reader | done (D37–D41); D-8 open (§9) |
| AC-6, AC-6a, AC-6b, AC-6c | consensus, crop re-read, opt-in verify loop | 6a done (D44), 6b done (D45), 6c implemented (D46, D47); exit "auto-verified rate up in the app" → WI-INT0, AC-LIVE-M, MT-13; the D59 self-confirmation gap closed by WI-G1 (D62) |
| AC-7, AC-7a, AC-7b | verify screen | 7a done (D48–D50); 7b implemented, 30 s untimed → AC-7c |
| AC-8, AC-8a, AC-8b | identity and duplicates | 8a done (D51); 8b implemented, gate open (D52–D55) → AC-8b, O-17 |
| AC-9, AC-9a, AC-9b | matching v2 | 9a implemented, gate open (D57–D58) → WI-D4; 9b open → AC-9b |
| AC-10 | learning loop | open → AC-10a/b |
| AC-11 | real-set E2E + exit gate | open → AC-11 (PH7) |
| AC-12 | GSTR-2B cross-check (optional) | open, optional → AC-12 |
| D-1, D-2, D-3, D-4, D-5, D-6, D-7, D-8, D-9, D-10 | owner decisions | §9.2 (D-2 decided D42; D-3 decided 7 Oct; D-5 decided D36; D-9 decided D35) |
| §1 hard guarantees and targets | false-safe, false auto-link, false Matched, wrong vendor; 98/95/95 | §2.1, §2.2 (HZ-1 … HZ-4 carried; HZ-5, HZ-6 added) |
| §3 architecture, §3.1 trust levels | L0–L3, Rejected, two rules | §2.3, §4.1 |
| §3.2 validators | arithmetic tree, GST structure, parties, names, dates, references | §4.4 (existing validators listed; new V-HSN, V-REFEXIST) |
| §3.3 verify loop | targeted re-read, max 2 rounds, local OCR only (D45, D47) | done (D45–D47); carried as §2.3 rule 3 |
| §4 matching without a PO number | anchors, provenance, identity score 40/35/25, variance split, period assignment, auto-link policy | AC-9a done (D57–D58); rules carried in §4.9 and the code; AC-9b open |
| §5 vendor master hygiene | verified-only vendors, marketplace rule, cleanup | done (D36); carried in §2.4 |
| §6 identity, re-scans, duplicates, bundles | natural key, fingerprints, DUP_SUSPECTED | AC-8a done, AC-8b open |
| §7 AI guardrails and privacy | vision contract, grounding, hard rejects, cache, masking, async | done (D37–D41); carried in §2.3, §4.8, §4.11 |
| §8 benchmark harness, §8.1 running the bench | truth v2, `accuracy_bench.py`, reports, metric definitions, hard-metric gate | done (D32); metric definitions carried and extended in §5.2; commands in §11 |
| §11 non-goals and risks | handwriting not verified; vision can be confidently wrong; small truth set | §1, §10 |
| §11 loose item: `recon_case_aged_ck` violation | aged GRN_PENDING | fixed (D51; IP B2) |
| §11 loose item: `ai_request.ai_cache_id` NULL | by design (IP B3) | no change; D44 explains |
| §11 loose item: payment terms not wired to due date | B4 | carried → AC-11 (§9.3) |

### 13.2 IP — `accuracy-implementation-plan.md`

| Old ID | Title | New location / status |
|---|---|---|
| §0.1 rules 1–10 | session rules | §11 |
| §0.2 machine facts | shell, Python, Node, Docker, `.env`, formatting, test collection, mypy scope | §11 runbook (condensed); the 19-file `ruff format` exception list is **not** copied: re-check with `ruff format --check` before relying on it |
| §0.3 command blocks | CHK-PY, TEST-OFFLINE, RULES-HASH-UPDATE, PG-UP, INT-TEST, PG-DOWN, PG-SAFETY, CONTRACT-REGEN, OPENAPI, WEB, E2E, BENCH-OFFLINE, BENCH-PROVIDER | §11 runbook (condensed; full text archived, §14) |
| §0.4 hashes, versions, fingerprints | `RULES_HASH`, `ENGINE_VERSION`, check versions, `PARSER_VERSION`, vision contract, consent fingerprint | §4.12 (rewritten under O-5) |
| §0.5 decision-log entries | template | §11 |
| §0.6 common exit order | verification order | §11 |
| §0.7 migration checklist 1–8 | revision file, metadata mirror, workspace tables, append-only, CI head − 1, revision set, PG-SAFETY, errata | §11 runbook |
| §1.1 phase state rows | AC-6a … AC-12 | §13.1 |
| §1.2 bench numbers | 8 Oct bench | superseded by §3 |
| §1.3 code baseline | tests, schema counts, append-only tables | superseded; schema counts re-checked in AC-INT1a |
| B1 | `canonicalize_po_number` not idempotent | fixed (D57) |
| B2 | aged GRN_PENDING violates `recon_case_aged_ck` | fixed (D51) |
| B3 | `ai_cache_id` NULL | by design (D44) |
| B4 | payment terms not applied | carried → AC-11 |
| B5 | CI migration safety built the wrong schema | fixed 8 Oct; repeat per migration (checklist, §11) |
| B6 | confirmed content link becomes suggested again | open → AC-9b |
| B7 | OCR-read ref created an auto labeled link | fixed (D57) |
| B8 | corrections did not re-run vendor/link mapping | fixed (D48, D49) |
| B9 | lower-quality re-scan replaces a clean version | AC-8b implemented (D52–D55); residual → O-17 |
| B10 | manual "change" link evidence text differs | open → AC-9b |
| B11 | same-type scans split only on "Page 1 of N" | accepted gap (§10.5) |
| B12 | Vendor Summary counts; auth tests not in hosted CI; flaky frame budget | carried → AC-11 |
| §2 order and sessions | dependency table | superseded by §8 |
| AC-6a spec: goal, exit criteria 1–5, preconditions | consensus core + shadow bench | done (D44) |
| AC-6a design c.1 | trust levels as implemented | carried in §2.3 |
| AC-6a invariants I1, I2, I3, I4, I5, I6 | values never change; L0 untouched; policy off = same object; vision-only never written; order independence; never-automatic names | carried as CI-1 … CI-6 (§2.3), tests in the tree |
| AC-6a tests 1 – 24 and bench tests 1 – 4 | consensus unit/property tests | present in the tree (`test_consensus_core.py`, `test_accuracy_consensus.py`); done (D44) |
| AC-6a f–i | verification, risks, size, docs | done (D44) |
| AC-6b … AC-12 specifics | the file's §3 holds only AC-6a although titled "AC-6 to AC-11"; AC-6b … AC-12 specifications are absent from it | done work is in the tree and D45–D58; open items are carded in §7 |

### 13.3 FP — `accuracy-fix-plan-scan-extraction.md`

| Old ID | Title | New location / status |
|---|---|---|
| §0.1 invariants (8) | AI never decides; core pure; SDK only in the ai-worker; sandbox no network; Decimal; D-6; append-only; hard zeros | §1, §2.1, MG-guards; import-linter contracts in code |
| §0.2 privacy, §0.3 environment, §0.4 baseline | rules, commands, frozen baseline `9cd8137fd7c1` / 302/373 | §11, §3.1 |
| WI-1 | bench/upload parity, attribution tooling, baseline freeze | done (D59) |
| WI-2 | test infrastructure and hard-zero guards | done (D59, D61) |
| WI-3 steps 1–3 | role guard when entities are empty; `BUYER_ENTITIES_NOT_CONFIGURED` warning (`parsers/parties.py:363`) | done (D59, D60) |
| WI-3 step 4 | Settings readiness warning when buyer entities are empty | **done, verified 20 Oct**: `web/src/pages/SettingsPage.tsx:240`, test `SettingsPage.test.tsx:94` |
| WI-3 step 5 | owner enters the company's GSTINs | **done, verified 20 Oct**: live config has 1 buyer entity (count only) |
| WI-4 | doc-no / PO-ref candidate selection | **frozen** (D72) → WI-D1 |
| WI-5 | vendor name | **frozen** → WI-D1 (Qwen vendor name 39/39, so the need has shifted) |
| WI-6 | header totals and tax | **frozen** → WI-D1 |
| WI-7 | line-table extraction | **frozen** → WI-D1 |
| WI-8 steps 3, 4, 5 | force OCR on poor/tiled layers; > 20 pages; UTF-8 decode | **frozen** → WI-D2 |
| WI-8 step 1 | low-confidence header/table re-OCR | **frozen** → WI-D2b |
| WI-8 step 2 | GSTIN checksum-guided repair | **frozen** → WI-D2b (first to unfreeze if GSTIN misses persist) |
| WI-8 step 6 | skew > 5°, dewarp, upscale | **frozen, conditional** → WI-D2c |
| WI-9 | parser-version guard and re-parse | open → WI-D3 (extended, §4.12) |
| WI-10 | link-recall regression | open → WI-D4 |
| WI-11 | truth-set expansion | open → WI-D5 (replaced by the two-tier design, O-1) |
| WI-12 | gated Gemini experiment | **superseded by AC-L** (D62–D67); Gemini arms E1–E4 moved into PH2 and are required for the side-by-side matrix (D80) |
| WI-13 | vision as primary reader on scans | **superseded by WI-BE7** (Qwen) |
| §2 regression guards | hard-zero guards in CI | WI-G3, §5.3 |
| DP-1 | approve the Gemini experiment | superseded: arms E1–E4 are in PH2 (D80); each run still needs the owner's go, key and budget |
| DP-2 | enter `buyer_entities` | done |
| DP-3 | delete `scripts/measure_extraction.py` | open, §9.3 |
| DP-4 | is "flagged" acceptable where no reader is sure | §9.3 (DP-L10 / FP DP-4) |
| DP-5 | supply 15–20+ complete sets | superseded by O-1 |
| DP-6 | files over 20 pages | carried, frozen with WI-D2 |
| DP-7 | GST slab list | carried (owner tax policy) |
| DP-8 | bulk re-parse | carried → WI-D3 |
| DP-9 | monthly cost caps | = D-8 (§9) |
| §3a owner answers (9 Oct) | Docker running; entities set; free-tier key for tests; ~5 sets; LLM path expected to close the gap | recorded; superseded where §9 says so |
| §4 out of scope | AC-8b, reconciliation rules, XML/CSV, GSTR-2B, production rollout, heavy dependencies | §1 |
| §5 definition of done (8 items) and the ceiling note (OCR-only ceiling 96.8% / 95.5%) | | §2.2, §6 PH4/PH7; ceiling note carried in §4.6 reasoning |

### 13.4 PG — `progress-accuracy-plan.md`

| Old ID | Title | New location / status |
|---|---|---|
| §1 headline and targets table | status and numbers at 16 Oct | superseded by §3 (numbers re-verified 20 Oct) |
| §2 phase table (18 rows) | plan claim vs evidence | §13.1 statuses |
| §2.1 scan-fix rows (WI-1 … WI-13) | | §13.3 |
| §2.2 owner decisions | | §9.2 |
| C1 | plan said "today ≈ 89% / 84%" | stale; replaced by §3.1 (headers 81.0% all documents; 79.2% scan pages; lines 68.1%) |
| C2 | "only the paid tier is used" | stale; D39 allows the free tier for testing; the code cannot tell the tier |
| C3 | "D-4: set it in Settings before AC-1" | superseded; D59 warning; live value present |
| C4 | "D-7: 15–20 sets" | superseded by O-1 |
| C5 | README does not mention vision, AI-first, consensus, bench | open; Stage 3 adds one README paragraph |
| C6 | newest reports have an empty vision block (bench default model ≠ stored answers) | understood: `--vision-model`/`--vision-prompt-variant` must match the stored answers; the WI-EV3 gate pins them |
| §3 remaining work (14 rows) | | all in §7 |
| §5 dependency map | | §8 |
| §6 "LLM feature: what already exists" | | §4.13, §13.6 |
| §7 per-stage attribution of the D57/D58 misses | | §4.2 ST-10, diagnosis §5.7 (kept) |

### 13.5 PL — `plan-llm-extraction-call.md`

| Old ID | Title | New location / status |
|---|---|---|
| §0.1 invariants 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 | AI never decides; untrusted evidence; core pure; SDK only in the ai-worker; sandbox no network; Decimal; D-6; versioned corrections; provenance; settings/keys/SSRF/audit; hard zeros | §1, §2, MG-1 … MG-10, code contracts |
| §0.2 privacy and approvals | | §11 |
| §0.3 commands | | §11 |
| §0.4 baseline | | §3 |
| §0.5 server facts and consequences | | §3.5, §4.8 |
| §0.6 owner decision: providers are separate | no `dual_agree`, no combination of answers | kept (§1, §12); reading refined by D80: one live provider and at most one shadow provider may run for a document version; both are supported and user-selectable; their answers are never merged, voted or compared in the app (§4.14) |
| §1 amendment 1 | AC-L0 … AC-L3 phases | §13.0, §6 |
| §1 amendment 2 | order (L0/L1 parallel with deterministic work; L3 after) | §8 |
| §1 amendment 3 | plan text corrections (C1 … C4, AC-L in §9) | done by this plan |
| §1 amendment 4 | D42: two LLMs are one reader kind | §2.3 (DP-L2: No) |
| AC-L0 | provider layer | done except WI-EV0 (open, optional) |
| AC-L1 | local provider, settings, shadow | WI-BE2 … BE6 done; open: WI-FE1, FE3, OPS1, OPS2 |
| AC-L2 | evaluation and decision | WI-EV1 done; open: WI-EV2, WI-BE8 |
| AC-L3 | LLM values on scans | open: WI-BE7, WI-FE2, WI-FE3, WI-G3 |
| WI-D1 | scan rules (= FP WI-4 … 7) | frozen (D72) |
| WI-D2 | force OCR, > 20 pages, UTF-8 | frozen (D72) |
| WI-D3 | parser-version guard and re-parse | open (extended) |
| WI-D4 | link-recall regression | open |
| WI-D5 | truth set v3 | open (two-tier) |
| WI-D6 | AC-3 page-split defect | open |
| WI-G1 | consensus self-confirmation gap | done (D62) |
| WI-G2 | import guard for HTTP clients | done (D62) |
| WI-G3 | regression guards and rollback switches | open |
| WI-BE1 | provider interface; Gemini behind it | done (D62) |
| WI-BE2 | `vision_openai_compat` provider for vLLM | done (D64) |
| WI-BE3 | settings model, API, migration, consent, audit | done (D65) |
| WI-BE4 | prompt/schema v4, standalone validators | done (D63) |
| WI-BE5 | provenance and cache key v2 | done (D63) |
| WI-BE6 | shadow mode and comparison store (one reader) | done (D66) |
| WI-BE7 | merge with OCR | open → WI-BE7a/b/c |
| WI-BE8 | cropped two-pass strategy | open (Stage 4 first) |
| WI-EV0 | offline re-score of stored Gemini answers | open, optional |
| WI-EV1 | evaluation harness, one provider per run | done (D67) |
| WI-EV2 | experiments and decision matrix | open → close, **side by side for Qwen and Gemini** (arms E0, E5 run; E1 – E4 now PH2; E7 optional; E8 = crops; E6 dropped; E9 unplanned) |
| arms E0, E1, E2, E3, E4, E5, E6, E7, E8, E9 | experiment arms | §7.3 WI-EV2, §12 |
| agreement policies P1, P2, P3, P4 | agreement policies | rejected / out of scope (§0.6; D67 keeps only `single` and `llm_x_ocr`) |
| WI-FE1 | AI settings UI, two provider cards | open → FE1a/b |
| WI-FE2 | review UI provenance and disagreement | open |
| WI-FE3 | frontend tests and type-drift guard | open |
| WI-OPS1 | server checklist and smoke test | open |
| WI-OPS2 | health, concurrency, fallback in operation | open |
| §4 sequence and dependencies | | §8 |
| DP-L1 | auto-accept thresholds | decided 20 Oct: option (A) for first enablement, (B) as target; arithmetic corrected (§9.1) |
| DP-L2 | do two LLMs count as independent | answered: No |
| DP-L3 | GPU host | resolved (company vLLM, 16 Oct) |
| DP-L4 | approvals | §9.3 |
| DP-L5 | Gemini tier for production | §9.3 (rewritten D80) |
| DP-L6 | pin vLLM and Qwen | §9.3 |
| DP-L7 | default strategy | §9.3 |
| DP-L8 | local model for PO/GRN | §9.3 |
| DP-L9 | D-8 caps and GPU budget | §9.3 |
| DP-L10 | "flagged" acceptable | §9.3 |
| §6 out of scope | training, reconciliation changes, GSTR-2B, AC-10, AC-9b UI, production rollout | §1; **text hooks on the local model are now in scope** (WI-BE9b/c, D80); AC-9b and AC-10 are planned in §7.7 |
| §7 definition of done (8 items) | | §2.2, §6 PH4/PH7, §5.3 |

### 13.6 AU — `audit-llm-extraction.md` (retired, O-7)

| Old ID | Title | New location / status |
|---|---|---|
| P1, P2, P3, P4, P5, P6, P7, P8, P9, P10 | drop points for a correct LLM value | §4.13 (each with its answer) |
| §2 coupling map | SDK import, import rule, provider port, instance, request shape, error mapping, transport, settings, consent, gate, reader switch, enqueue, eligibility, prompt, schema, validators, cache key, ledger, limits, worker, key, tables | §4.13 summary; the gaps it found are closed (D62–D66) except text-hook binding → WI-BE9 |
| §3 output path | how a value is used today | §4.2 ST-13, §4.10 |
| §4 baseline (4.1 header table, 4.2 lines, 4.3 by scan type) | stored Gemini numbers | kept in §12 (Gemini row) and `after-d41-lite.json`; Qwen per-field table §3.2 |
| M1, M2, M3, M4, M5, M6 | miss classes (misread, prompt ambiguity, image quality, pipeline dropped, label error, party-role) | classes carried in WI-EV3 attribution; counts for Qwen **unverified** (§3.7) |
| §5 wiring: upload path vs scripts, mismatches (a) – (d) | bench runs vision on every PDF while the app uses eligibility; bench default model ≠ stored answers; bench never exercises the consensus job; live model id unknown | (a) WI-EV3 and WI-BE7b eligibility; (b) pinned in the gate; (c) WI-INT0 / AC-LIVE-M; (d) answered: `gemini-3.5-flash-lite` (live check) |
| §6 local serving readiness | gate, concurrency/timeouts, GPU contention, health, deployment, network rules, dependencies, this machine as server | done in D64, D65; deployment and network → AC-INT1b; the dev machine cannot serve the model (RTX 2050, 4 GB: documented), the company server is used |
| §7 cross-check inventory | arithmetic, GSTIN, role, tax type, reference shape, value-in-OCR, null-on-unreadable, date window, crop re-read, two readers | §4.4; gaps closed in D63 (marks) and D62 (self-confirmation); gate → MG-4 |
| §8 frontend / settings audit | location, state, types, fields, "AI first" meaning, who may edit, audit, key handling, test endpoint, usage, provenance, phone width | WI-FE1, FE2, FE3; "AI first" explained in §7.2 (AC-LIVE-S); phone width **unverified** → FE1b |
| §9 tests | existing coverage and gaps | WI-G3, WI-FE3, WI-OPS2 |
| §10 external facts | model card, thinking, sampling, structured outputs, `--api-key`, quantised variants, Gemini facts | §4.13 (condensed; unverified items listed) |
| §11 top findings 1 – 3 | worker hard-wired; "AI first" widens eligibility only and consensus had a false-safe gap; cache key/UI/arithmetic/unreadable gaps | all closed or tasked (D62 – D66; WI-FE2; WI-BE9) |

### 13.7 Dx — `diagnosis-scan-extraction.md` (kept as evidence)

Not merged. Its numbers are cited here as Dx §5 (per-stage attribution), §5.7 (link misses), §6 (config audit) and §8 (test gaps). Its mismatches M1, M2, M3, M4, M5, M6 between the bench and the upload path were closed by FP WI-1 (D59). Items from §6 still open and not tasked: line UOM dropped when it contains a digit; charge keywords fixed list; day-first dates only on the layout path; OCR languages (`hin` installed, unused); `verification_score` has a hard-coded 0.70 floor. All sit in the frozen rules path (WI-D1/D2) and are recorded there.

### 13.8 Parts superseded after D79 (change of requirement, D80)

| Superseded part | By | Where |
|---|---|---|
| O-13 (`vision_only` mode; Gemini turned off) | Gemini is not turned off | §9.1, WI-BE9a |
| WI-BE9 "Gemini off while Qwen vision is on" | provider-agnostic hooks WI-BE9a/b/c | §7.2, WI-BE9a/b/c |
| O-6 wording ("Qwen down → rules + OCR, no automatic Gemini failover") | provider-independent wording; optional explicit fallback setting (default off) | §9.1, §4.8 |
| O-14 (back-fill; AC-LIVE-S may turn off Gemini page-image reading) | back-fill per provider; AC-LIVE-S leaves Gemini on | §9.1, AC-LIVE-S |
| O-3 ("D-8 gates Gemini production only") | D-8 caps required per provider; Gemini cap required at connect | §9.1, WI-PV7 |
| AC-LIVE-S as written in D78 (Qwen shadow replaces the live reader) | Qwen shadows while Gemini stays the live default (needs `shadow_provider` beside the live provider) | AC-LIVE-S, WI-PV1, WI-PV5 |
| AC-LIVE-M as written in D78 (Qwen becomes the merge provider, Gemini off) | merge enabled per provider; default provider decided at AC-LIVE-M (O-22) | AC-LIVE-M |
| §4.14 "Role of Gemini" ("not an automatic fallback; off in the live workspace") | §4.14 Roles of the providers and §4.15 | §4.14, §4.15 |
| "Gemini optional / bench arm only" statements in §0, §1, §9.2 D-1 | both providers supported | §0, §1, §9.2 |
| Gemini arms E1–E4 optional in PH6 | required in PH2 | §6, WI-EV2 |
| Workspace-wide `auto_verify_field_types` | per provider, starting empty | §2.6, WI-PV1 |
| Retired LLM plan §6 "text hooks on the local model out of scope" | in scope | WI-BE9b/c |
| `RB_VISION_READER` default `vision_gemini` | default `all` | WI-PV5 |
| Retired LLM plan §0.6 as "a workspace picks one vision provider at a time or runs one in shadow" | one live plus at most one shadow provider; no merge, vote or in-app comparison | §4.14 |
| Phase list PH1 … PH7 | PH2b added | §6 |

---

## 14. Removed documents

Removed on 20 Oct 2026 (D79) with the owner's approval. Each is archived byte for byte in `work/plan-archive-20261020/` (Option B; SHA-256 list in `SHA256SUMS.txt`), in a tarball at `~/reconbench-archive/plan-archive-20261020.tar.gz` (outside the working tree), and in the snapshot commit `d916ec6` tagged `pre-plan-merge-20261020` (Option A′). The removal is the next commit on the same branch.

| File | Git state before removal | Preserved in |
|---|---|---|
| `docs/accuracy-implementation-plan.md` | tracked, modified | §4.12, §7, §11, §13.2 |
| `docs/plan-llm-extraction-call.md` | untracked | §2, §3.5, §4, §7, §9, §13.5 |
| `docs/accuracy-fix-plan-scan-extraction.md` | untracked | §7.7 – 7.9, §9, §12, §13.3 |
| `docs/progress-accuracy-plan.md` | untracked | §3, §8, §13.4 |
| `docs/audit-llm-extraction.md` | untracked | §4.13, §12, §13.6 |

The previous version of `docs/accuracy-plan.md` (this file before the rewrite) is archived in the same folder. Recovery: `git show pre-plan-merge-20261020:docs/<file>`, or copy back from `work/plan-archive-20261020/` or the tarball. The **previous version of this file** (before the Qwen-first rewrite) exists only in the archive folder and the tarball: the snapshot commit holds the rewritten version. Kept: `ReconBench_*.md`, `decision-log.md`, `gates/*`, `diagnosis-scan-extraction.md`, `schema-errata.md`, `ai-gemini-protocol.md`, `database-roles-and-seeding.md`, `dependency-review.md`, `engine-cli.md`, `index-review.md`, `schema-drift-check.md`, `schemas/*`.
