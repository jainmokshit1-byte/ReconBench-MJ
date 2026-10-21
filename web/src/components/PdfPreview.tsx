import { useEffect, useRef, useState } from "react";
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { ChevronLeft, ChevronRight, Minus, Plus } from "lucide-react";

import { fileContentUrl } from "../api/ingestion";
import { pdfHighlightStyle, type PdfPreviewFocus } from "../utils/pdfEvidence";
import { PDF_RENDER_OPTIONS } from "../utils/pdfRenderOptions";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfPreview({
  fileId,
  focus = null,
  pageNumber,
  onPageChange,
}: {
  fileId: string;
  focus?: PdfPreviewFocus | null;
  pageNumber: number;
  onPageChange: (pageNumber: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [scale, setScale] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [pageView, setPageView] = useState<number[] | null>(null);

  useEffect(() => {
    let active = true;
    let task: PDFDocumentLoadingTask | null = null;
    task = pdfjs.getDocument({ url: fileContentUrl(fileId), ...PDF_RENDER_OPTIONS });
    void task.promise.then((loaded) => {
      if (active) {
        setDocument(loaded);
        setLoading(false);
      }
    }).catch(() => {
      if (active) {
        setError("This PDF could not be opened. It may be encrypted, damaged, or unavailable.");
        setLoading(false);
      }
    });
    return () => {
      active = false;
      if (task) void task.destroy();
    };
  }, [fileId]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!document || !canvas) return;
    let renderTask: RenderTask | null = null;
    let active = true;
    void document.getPage(pageNumber).then((page) => {
      if (!active) return;
      const viewport = page.getViewport({ scale });
      setViewportSize({ width: viewport.width, height: viewport.height });
      setPageView(page.view);
      const outputScale = Math.min(window.devicePixelRatio || 1, 2);
      const context = canvas.getContext("2d");
      if (!context) {
        setError("The browser could not create a PDF drawing surface.");
        return;
      }
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      renderTask = page.render({
        canvas,
        canvasContext: context,
        viewport,
        transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
      });
      return renderTask.promise;
    }).catch(() => {
      if (active) setError("This PDF page could not be rendered.");
    });
    return () => {
      active = false;
      renderTask?.cancel();
    };
  }, [document, pageNumber, scale]);

  const focusedBox = focus?.page === pageNumber ? pdfHighlightStyle(focus, pageView) : null;

  return (
    <section className="pdf-preview" aria-label="PDF preview">
      <div className="pdf-toolbar">
        <div className="pdf-page-controls">
          <button
            className="icon-button"
            type="button"
            aria-label="Previous PDF page"
            disabled={!document || pageNumber <= 1}
            onClick={() => onPageChange(Math.max(1, pageNumber - 1))}
          >
            <ChevronLeft size={16} aria-hidden="true" />
          </button>
          <span aria-live="polite">{document ? `${pageNumber} of ${document.numPages}` : "—"}</span>
          <button
            className="icon-button"
            type="button"
            aria-label="Next PDF page"
            disabled={!document || pageNumber >= document.numPages}
            onClick={() => onPageChange(Math.min(document?.numPages ?? pageNumber, pageNumber + 1))}
          >
            <ChevronRight size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="pdf-zoom-controls">
          <button className="icon-button" type="button" aria-label="Zoom out" disabled={scale <= 0.6} onClick={() => setScale((value) => Math.max(0.6, value - 0.2))}>
            <Minus size={15} aria-hidden="true" />
          </button>
          <span>{Math.round(scale * 100)}%</span>
          <button className="icon-button" type="button" aria-label="Zoom in" disabled={scale >= 2} onClick={() => setScale((value) => Math.min(2, value + 0.2))}>
            <Plus size={15} aria-hidden="true" />
          </button>
        </div>
      </div>
      {loading ? <p className="preview-message" role="status">Opening PDF…</p> : null}
      {error ? <p className="preview-error" role="alert">{error}</p> : null}
      <div className="pdf-canvas-scroll">
        <div className="pdf-canvas-page" style={{ width: viewportSize.width || undefined, height: viewportSize.height || undefined }}>
          <canvas ref={canvasRef} aria-label={`PDF page ${pageNumber}`} />
          {focusedBox ? <div className="pdf-evidence-highlight" style={focusedBox} aria-label="Selected source crop" /> : null}
        </div>
      </div>
    </section>
  );
}
