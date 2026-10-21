import type { CSSProperties } from "react";

export interface PdfPreviewFocus {
  page: number;
  bbox: number[] | null;
  source: string | null;
  ai_call_id: string | null;
}

function percentage(value: number): string {
  return `${Number((value * 100).toFixed(4))}%`;
}

/** Convert stored PDF points or normalized vision boxes into a CSS overlay. */
export function pdfHighlightStyle(
  focus: PdfPreviewFocus | null,
  pageView: number[] | null,
): CSSProperties | null {
  if (!focus?.bbox || focus.bbox.length !== 4) return null;
  const [a, b, c, d] = focus.bbox;
  if ([a, b, c, d].some((value) => !Number.isFinite(value))) return null;

  let left: number;
  let top: number;
  let right: number;
  let bottom: number;
  const normalized = focus.ai_call_id !== null || focus.source === "vision";
  if (normalized && Math.max(a!, b!, c!, d!) <= 1000) {
    top = a! / 1000;
    left = b! / 1000;
    bottom = c! / 1000;
    right = d! / 1000;
  } else {
    if (!pageView || pageView.length < 4) return null;
    const pageWidth = pageView[2]! - pageView[0]!;
    const pageHeight = pageView[3]! - pageView[1]!;
    if (pageWidth <= 0 || pageHeight <= 0) return null;
    left = (a! - pageView[0]!) / pageWidth;
    top = b! / pageHeight;
    right = (c! - pageView[0]!) / pageWidth;
    bottom = d! / pageHeight;
  }
  const clampedLeft = Math.max(0, Math.min(1, left));
  const clampedTop = Math.max(0, Math.min(1, top));
  const clampedRight = Math.max(0, Math.min(1, right));
  const clampedBottom = Math.max(0, Math.min(1, bottom));
  if (clampedRight <= clampedLeft || clampedBottom <= clampedTop) return null;
  return {
    left: percentage(clampedLeft),
    top: percentage(clampedTop),
    width: percentage(clampedRight - clampedLeft),
    height: percentage(clampedBottom - clampedTop),
  };
}
