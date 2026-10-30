-- scripts/pd5c_dump.sql
-- PD-5c read-only dump of test documents from beta
-- Outputs one JSON array listing each test document with file SHA-256, page count,
-- reading state, failure code, version 1 (rules) and version 2 (AI, extraction_method='ai_first')
-- header + lines, and version 2 cross_check / ocr_reading from extras->'header'.

BEGIN READ ONLY;

WITH test_ids AS (
    SELECT unnest(
        CASE 
            WHEN NULLIF(current_setting('reconbench.test_doc_ids', true), '') IS NOT NULL 
                THEN current_setting('reconbench.test_doc_ids', true)::uuid[]
            ELSE ARRAY[
                '01a10e08-a92a-7f7f-9a16-3d6ffa967502'::uuid, -- doc-1
                '01a10e07-32b0-78b4-bfda-cda27dd31052'::uuid, -- doc-2
                '01a18c16-f97a-74ff-8241-3a928c6f12ca'::uuid, -- doc-3
                '01a10e07-ee44-703c-bbae-3c8383ad8cc3'::uuid, -- doc-4
                '01a18c29-e392-7df9-bf70-1bf010ff2994'::uuid, -- doc-5
                '01a10e07-7be9-770c-9257-7587a0e683c5'::uuid, -- doc-6
                '01a10e07-5dea-7eb2-a2fd-d18f1fb615a1'::uuid, -- doc-7
                '01a10e08-0920-796c-a239-2675588dae34'::uuid, -- doc-8
                '01a10e0a-4c59-706c-a2af-6812708c47ee'::uuid, -- doc-9
                '01a10e0a-2d4e-7eb9-a089-fc36ca9ba56d'::uuid  -- doc-10
            ]::uuid[]
        END
    ) AS id
),
target_docs AS (
    SELECT d.id, d.reading_state, d.reading_failure_code, d.current_version_id
    FROM document d
    JOIN test_ids t ON d.id = t.id
),
v2_rows AS (
    SELECT DISTINCT ON (dv.document_id)
        dv.id,
        dv.document_id,
        dv.seq,
        dv.derived_from_version_id,
        dv.source_file_id,
        dv.page_count,
        dv.doc_no,
        dv.doc_date,
        dv.vendor_name,
        dv.vendor_gstin,
        dv.vendor_pan,
        dv.buyer_gstin,
        dv.taxable_total,
        dv.cgst_total,
        dv.sgst_total,
        dv.igst_total,
        dv.grand_total,
        dv.po_refs,
        dv.grn_refs,
        dv.extras
    FROM document_version dv
    JOIN target_docs td ON dv.document_id = td.id
    WHERE dv.extraction_method = 'ai_first'
    ORDER BY dv.document_id, dv.seq DESC
),
v1_rows AS (
    SELECT DISTINCT ON (dv.document_id)
        dv.id,
        dv.document_id,
        dv.seq,
        dv.source_file_id,
        dv.page_count,
        dv.doc_no,
        dv.doc_date,
        dv.vendor_name,
        dv.vendor_gstin,
        dv.vendor_pan,
        dv.buyer_gstin,
        dv.taxable_total,
        dv.cgst_total,
        dv.sgst_total,
        dv.igst_total,
        dv.grand_total,
        dv.po_refs,
        dv.grn_refs,
        dv.extras
    FROM document_version dv
    JOIN target_docs td ON dv.document_id = td.id
    LEFT JOIN v2_rows v2 ON dv.document_id = v2.document_id
    WHERE (v2.derived_from_version_id IS NOT NULL AND dv.id = v2.derived_from_version_id)
       OR (v2.derived_from_version_id IS NULL AND dv.extraction_method != 'ai_first')
    ORDER BY dv.document_id, dv.seq DESC
),
v1_lines AS (
    SELECT 
        dl.document_version_id,
        json_agg(json_build_object(
            'line_no', dl.line_no,
            'description', dl.description,
            'amount', dl.amount,
            'quantity', dl.quantity,
            'rate', dl.rate,
            'hsn_sac', dl.hsn_sac,
            'uom', dl.uom,
            'po_ref', dl.po_ref
        ) ORDER BY dl.line_no) AS lines
    FROM doc_line dl
    JOIN v1_rows v1 ON dl.document_version_id = v1.id
    GROUP BY dl.document_version_id
),
v2_lines AS (
    SELECT 
        dl.document_version_id,
        json_agg(json_build_object(
            'line_no', dl.line_no,
            'description', dl.description,
            'amount', dl.amount,
            'quantity', dl.quantity,
            'rate', dl.rate,
            'hsn_sac', dl.hsn_sac,
            'uom', dl.uom,
            'po_ref', dl.po_ref
        ) ORDER BY dl.line_no) AS lines
    FROM doc_line dl
    JOIN v2_rows v2 ON dl.document_version_id = v2.id
    GROUP BY dl.document_version_id
),
doc_payloads AS (
    SELECT json_build_object(
        'document_id', td.id,
        'source_file_sha256', sf.sha256,
        'page_count', COALESCE(
            NULLIF(sf.page_count, 0),
            NULLIF(v1.page_count, 0),
            NULLIF(v2.page_count, 0),
            NULLIF((SELECT count(*)::int FROM document_page_image dpi WHERE dpi.document_version_id = v1.id), 0),
            NULLIF((SELECT count(*)::int FROM document_page_image dpi WHERE dpi.document_version_id = v2.id), 0)
        ),
        'reading_state', td.reading_state,
        'reading_failure_code', td.reading_failure_code,
        'v1', CASE WHEN v1.id IS NOT NULL THEN json_build_object(
            'version_id', v1.id,
            'seq', v1.seq,
            'header', json_build_object(
                'doc_no', v1.doc_no,
                'doc_date', v1.doc_date,
                'vendor_name', v1.vendor_name,
                'vendor_gstin', v1.vendor_gstin,
                'vendor_pan', v1.vendor_pan,
                'buyer_gstin', v1.buyer_gstin,
                'taxable_total', v1.taxable_total,
                'cgst_total', v1.cgst_total,
                'sgst_total', v1.sgst_total,
                'igst_total', v1.igst_total,
                'grand_total', v1.grand_total,
                'po_refs', v1.po_refs,
                'grn_refs', v1.grn_refs
            ),
            'lines', COALESCE(l1.lines, '[]'::json)
        ) ELSE NULL END,
        'v2', CASE WHEN v2.id IS NOT NULL THEN json_build_object(
            'version_id', v2.id,
            'seq', v2.seq,
            'header', json_build_object(
                'doc_no', v2.doc_no,
                'doc_date', v2.doc_date,
                'vendor_name', v2.vendor_name,
                'vendor_gstin', v2.vendor_gstin,
                'vendor_pan', v2.vendor_pan,
                'buyer_gstin', v2.buyer_gstin,
                'taxable_total', v2.taxable_total,
                'cgst_total', v2.cgst_total,
                'sgst_total', v2.sgst_total,
                'igst_total', v2.igst_total,
                'grand_total', v2.grand_total,
                'po_refs', v2.po_refs,
                'grn_refs', v2.grn_refs
            ),
            'lines', COALESCE(l2.lines, '[]'::json),
            'cross_check', v2.extras->'header'->'cross_check',
            'ocr_reading', v2.extras->'header'->'ocr_reading'
        ) ELSE NULL END
    ) AS doc_json
    FROM target_docs td
    LEFT JOIN v1_rows v1 ON td.id = v1.document_id
    LEFT JOIN v2_rows v2 ON td.id = v2.document_id
    LEFT JOIN source_file sf ON COALESCE(v1.source_file_id, v2.source_file_id) = sf.id
    LEFT JOIN v1_lines l1 ON v1.id = l1.document_version_id
    LEFT JOIN v2_lines l2 ON v2.id = l2.document_version_id
    ORDER BY td.id
)
SELECT COALESCE(json_agg(doc_json), '[]'::json)
FROM doc_payloads;

ROLLBACK;
