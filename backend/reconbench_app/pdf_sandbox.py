"""Run untrusted PDF parsing in a resource-bounded child process."""

from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from reconbench_core.extraction.pages import PageType, text_page_types
from reconbench_core.extraction.qr import apply_einvoice, parse_einvoice_qr
from reconbench_core.parsers.pdf import parse_pdf
from reconbench_core.parsers.types import PageSplit, ParseResult, ParserHints, ParseWarning
from reconbench_core.schema import DocType, FileFormat

PDF_CPU_SECONDS = 8
# pdfplumber/pdfminer and the spawned OCR runtime need more than 1 GiB of
# virtual address space in the worker image (the dependency set reserves
# address space before parsing starts). Keep a hard per-child cap while
# leaving room for ordinary scanned and multi-page documents.
PDF_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
PDF_WALL_SECONDS = 12
PDF_OCR_CPU_SECONDS = 90
PDF_OCR_WALL_SECONDS = 120
# Layout extraction OCRs every scanned page itself (about 3–8 s per page).
PDF_LAYOUT_CPU_SECONDS = 150
PDF_LAYOUT_WALL_SECONDS = 180
PDF_LAYOUT_MAX_PAGES = 20
# Rendering and decoding the first pages for an e-invoice QR (about 0.5 s a page).
PDF_QR_CPU_SECONDS = 30
PDF_QR_WALL_SECONDS = 45
_MAX_OCR_OUTPUT_BYTES = 100 * 1024 * 1024


def _failed(code: str, message: str) -> ParseResult:
    return ParseResult(
        success=False,
        file_format=FileFormat.PDF,
        warnings=(ParseWarning(code=code, message=message),),
        layout_signature="0" * 64,
        reason_code=code,
    )


def _set_posix_limits(cpu_seconds: int, memory_bytes: int) -> None:
    if os.name != "posix":
        return
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def _windows_job(process: multiprocessing.Process, cpu_seconds: int, memory_bytes: int) -> Any:
    """Assign a Windows child to a Job Object with CPU and process-memory caps."""

    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle_type = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = handle_type
    kernel.SetInformationJobObject.argtypes = [
        handle_type,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [handle_type, handle_type]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [handle_type]
    kernel.CloseHandle.restype = wintypes.BOOL

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = 0x00000002 | 0x00000100
    limits.BasicLimitInformation.PerProcessUserTimeLimit = cpu_seconds * 10_000_000
    limits.ProcessMemoryLimit = memory_bytes
    if not kernel.SetInformationJobObject(
        job,
        9,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise OSError(error, "SetInformationJobObject failed")
    popen = process._popen  # type: ignore[attr-defined]
    child_handle = handle_type(int(popen._handle))  # type: ignore[attr-defined]
    if not kernel.AssignProcessToJobObject(job, child_handle):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise OSError(error, "AssignProcessToJobObject failed")
    return job


def _close_windows_job(job: Any) -> None:
    if os.name != "nt" or job is None:
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CloseHandle(job)


def _child_main(
    connection: Any,
    start_event: Any,
    content: bytes,
    hints: ParserHints,
    cpu_seconds: int,
    memory_bytes: int,
) -> None:
    try:
        if not start_event.wait(timeout=10):
            connection.send({"child_error": True})
            return
        _set_posix_limits(cpu_seconds, memory_bytes)
        result = parse_pdf(content, hints)
        connection.send(_result_payload(result))
    except BaseException:
        connection.send({"child_error": True})
    finally:
        connection.close()


def _result_payload(result: ParseResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "file_format": result.file_format.value if result.file_format else None,
        "documents": [
            document.model_dump(mode="json", exclude={"confidence"})
            for document in result.documents
        ],
        "warnings": [warning.model_dump(mode="json") for warning in result.warnings],
        "layout_signature": result.layout_signature,
        "reason_code": result.reason_code,
        "page_texts": [page.model_dump(mode="json") for page in result.page_texts],
    }


def _page_ranges(pages: tuple[int, ...]) -> str:
    ranges: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def _ocr_child_main(
    connection: Any,
    start_event: Any,
    content: bytes,
    hints: ParserHints,
    scanned_pages: tuple[int, ...],
    languages: tuple[str, ...],
    cpu_seconds: int,
    memory_bytes: int,
) -> None:
    try:
        if not start_event.wait(timeout=10):
            connection.send({"child_error": True})
            return
        _set_posix_limits(cpu_seconds, memory_bytes)
        from ocrmypdf import ocr
        from ocrmypdf.exceptions import ExitCode

        with tempfile.TemporaryDirectory(prefix="reconbench-pdf-ocr-") as directory:
            source_path = Path(directory) / "source.pdf"
            output_path = Path(directory) / "ocr.pdf"
            source_path.write_bytes(content)
            exit_code = ocr(
                source_path,
                output_path,
                language=languages,
                mode="force",
                pages=_page_ranges(scanned_pages),
                jobs=1,
                optimize=0,
                output_type="pdf",
                max_image_mpixels=50,
                max_ocr_image_mpixels=50,
                tesseract_timeout=15,
                progress_bar=False,
            )
            if exit_code != ExitCode.ok or not output_path.is_file():
                connection.send({"child_error": True})
                return
            output_size = output_path.stat().st_size
            output_limit = max(hints.max_bytes, min(_MAX_OCR_OUTPUT_BYTES, hints.max_bytes * 4))
            if output_size > output_limit:
                connection.send({"child_error": True})
                return
            ocr_hints = hints.model_copy(update={"max_bytes": output_limit})
            result = parse_pdf(
                output_path.read_bytes(),
                ocr_hints,
                ocr_pages=frozenset(scanned_pages),
            )
            if not result.success:
                connection.send({"child_error": True})
                return
            connection.send(_result_payload(result))
    except BaseException:
        connection.send({"child_error": True})
    finally:
        connection.close()


def _append_ocr_warning(result: ParseResult, message: str) -> ParseResult:
    warnings = tuple(warning for warning in result.warnings if warning.code != "NO_TEXT_LAYER")
    return result.model_copy(
        update={
            "warnings": (
                *warnings,
                ParseWarning(code="NO_TEXT_LAYER", message=message, locator="pdf"),
            )
        }
    )


def _append_warning(result: ParseResult, code: str, message: str) -> ParseResult:
    return result.model_copy(
        update={
            "warnings": (
                *result.warnings,
                ParseWarning(code=code, message=message, locator="pdf"),
            )
        }
    )


def _complete_line_count(result: ParseResult, hints: ParserHints) -> int:
    count = 0
    for document in result.documents:
        for line in document.lines:
            if line.description is None:
                continue
            if hints.doc_type is DocType.GRN:
                count += int(
                    line.quantity is not None
                    or line.received_qty is not None
                    or line.accepted_qty is not None
                )
            else:
                count += int(
                    line.quantity is not None and (line.rate is not None or line.amount is not None)
                )
    return count


def _extraction_quality(result: ParseResult, hints: ParserHints) -> int:
    """Compare completeness without inspecting or preferring particular values."""

    if any(warning.code == "EXTRACTION_QA_FAILED" for warning in result.warnings):
        return -1_000_000
    score = _complete_line_count(result, hints) * 10
    for document in result.documents:
        header = document.header
        score += sum(
            getattr(header, name, None) not in (None, [])
            for name in ("doc_no", "doc_date", "vendor_name", "vendor_gstin", "grand_total")
        )
    return score


def _has_critical_header_gaps(result: ParseResult, hints: ParserHints) -> bool:
    if not result.documents:
        return True
    for document in result.documents:
        header = document.header
        if header.doc_no is None or header.doc_date is None:
            return True
        if header.vendor_name is None and header.vendor_gstin is None:
            return True
        if hints.doc_type is DocType.INVOICE and header.grand_total is None:
            return True
    return False


def _layout_child_main(
    connection: Any,
    start_event: Any,
    content: bytes,
    hints: ParserHints,
    languages: tuple[str, ...],
    engine_name: str,
    cpu_seconds: int,
    memory_bytes: int,
) -> None:
    try:
        if not start_event.wait(timeout=10):
            connection.send({"child_error": True})
            return
        _set_posix_limits(cpu_seconds, memory_bytes)
        from reconbench_app.extraction.pipeline import layout_parse

        result = layout_parse(
            content,
            hints,
            languages=languages,
            engine_name=engine_name,
            max_pages=PDF_LAYOUT_MAX_PAGES,
        )
        if not result.success:
            connection.send({"child_error": True})
            return
        connection.send(_result_payload(result))
    except BaseException:
        connection.send({"child_error": True})
    finally:
        connection.close()


def _run_layout_isolated(
    content: bytes,
    hints: ParserHints,
    languages: tuple[str, ...],
    engine_name: str,
    *,
    memory_bytes: int,
) -> ParseResult | None:
    """Run the layout engine in its own limited process; ``None`` on any failure."""

    return _run_child(
        _layout_child_main,
        (content, hints, languages, engine_name, PDF_LAYOUT_CPU_SECONDS, memory_bytes),
        name="reconbench-pdf-layout",
        cpu_seconds=PDF_LAYOUT_CPU_SECONDS,
        memory_bytes=memory_bytes,
        wall_seconds=PDF_LAYOUT_WALL_SECONDS,
    )


def _qr_child_main(
    connection: Any,
    start_event: Any,
    content: bytes,
    cpu_seconds: int,
    memory_bytes: int,
) -> None:
    try:
        if not start_event.wait(timeout=10):
            connection.send({"child_error": True})
            return
        _set_posix_limits(cpu_seconds, memory_bytes)
        from reconbench_app.extraction.qr_decode import decode_qr_texts

        connection.send({"codes": decode_qr_texts(content)})
    except BaseException:
        connection.send({"child_error": True})
    finally:
        connection.close()


def _qr_codes(payload: dict[str, Any]) -> list[tuple[int, str]]:
    return [(int(page), str(text)) for page, text in payload["codes"]]


def _run_qr_isolated(content: bytes, *, memory_bytes: int) -> list[tuple[int, str]] | None:
    """QR texts by page from a limited child process; ``None`` on any failure."""

    codes: list[tuple[int, str]] | None = _run_child(
        _qr_child_main,
        (content, PDF_QR_CPU_SECONDS, memory_bytes),
        name="reconbench-pdf-qr",
        cpu_seconds=PDF_QR_CPU_SECONDS,
        memory_bytes=memory_bytes,
        wall_seconds=PDF_QR_WALL_SECONDS,
        convert=_qr_codes,
    )
    return codes


def _run_child(
    target: Any,
    arguments: tuple[Any, ...],
    *,
    name: str,
    cpu_seconds: int,
    memory_bytes: int,
    wall_seconds: float,
    convert: Callable[[dict[str, Any]], Any] = ParseResult.model_validate,
) -> Any:
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    start_event = context.Event()
    process = context.Process(target=target, args=(send, start_event, *arguments), name=name)
    job = None
    started = time.monotonic()
    try:
        process.start()
        send.close()
        if os.name == "nt":
            job = _windows_job(process, cpu_seconds, memory_bytes)
        start_event.set()
        if not receive.poll(wall_seconds):
            process.kill()
            process.join(timeout=2)
            return None
        payload = receive.recv()
        process.join(timeout=max(0.1, wall_seconds - (time.monotonic() - started)))
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
            return None
        if not isinstance(payload, dict) or payload.get("child_error"):
            return None
        return convert(payload)
    except (OSError, RuntimeError, EOFError, ValueError, KeyError, TypeError):
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        return None
    finally:
        receive.close()
        if not send.closed:
            send.close()
        if process.pid is not None and process.is_alive():
            process.kill()
            process.join(timeout=2)
        _close_windows_job(job)


def _may_hold_several(result: ParseResult, hints: ParserHints) -> bool:
    """The text layer names another document type, or a title on more than one page.

    Only the layout path splits a file into documents (accuracy plan AC-3).
    """

    if hints.page_split is PageSplit.OFF or hints.doc_type is None:
        return False
    titled = [
        found
        for found in text_page_types([page.content for page in result.page_texts])
        if found is not None and found is not PageType.EWAY_BILL
    ]
    return len(titled) > 1 or any(found.value != hints.doc_type.value for found in titled)


def _is_split(result: ParseResult) -> bool:
    return any("page_split" in document.header.extras for document in result.documents)


def _prefer_layout(
    result: ParseResult,
    content: bytes,
    hints: ParserHints,
    languages: tuple[str, ...],
    engine_name: str,
    memory_bytes: int,
    *,
    split_only: bool = False,
) -> ParseResult | None:
    """The layout extraction when it splits the file or scores at least as well as ``result``.

    With ``split_only`` the layout result is used only when it split the file.
    """

    if engine_name == "legacy" or hints.doc_type is None:
        return None
    if len(result.page_texts) > PDF_LAYOUT_MAX_PAGES:
        return None
    layout = _run_layout_isolated(content, hints, languages, engine_name, memory_bytes=memory_bytes)
    if layout is None:
        return None
    from reconbench_app.extraction.pipeline import has_content, verification_score

    if not has_content(layout):
        return None
    # The text parser reads a file as one document; a split always wins over it.
    if _is_split(layout):
        return layout
    if split_only:
        return None
    scanned = any(page.scanned for page in result.page_texts) or any(
        warning.code == "NO_TEXT_LAYER" for warning in result.warnings
    )
    layout_score, legacy_score = verification_score(layout), verification_score(result)
    if layout_score > legacy_score or (scanned and layout_score == legacy_score):
        return layout
    return None


def _run_ocr_isolated(
    content: bytes,
    hints: ParserHints,
    scanned_pages: tuple[int, ...],
    languages: tuple[str, ...],
    *,
    cpu_seconds: int,
    memory_bytes: int,
    wall_seconds: float,
) -> ParseResult | None:
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    start_event = context.Event()
    process = context.Process(
        target=_ocr_child_main,
        args=(
            send,
            start_event,
            content,
            hints,
            scanned_pages,
            languages,
            cpu_seconds,
            memory_bytes,
        ),
        name="reconbench-pdf-ocr",
    )
    job = None
    started = time.monotonic()
    try:
        process.start()
        send.close()
        if os.name == "nt":
            job = _windows_job(process, cpu_seconds, memory_bytes)
        start_event.set()
        if not receive.poll(wall_seconds):
            process.kill()
            process.join(timeout=2)
            return None
        payload = receive.recv()
        process.join(timeout=max(0.1, wall_seconds - (time.monotonic() - started)))
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
            return None
        if not isinstance(payload, dict) or payload.get("child_error"):
            return None
        return ParseResult.model_validate(payload)
    except (OSError, RuntimeError, EOFError, ValueError):
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        return None
    finally:
        receive.close()
        if not send.closed:
            send.close()
        if process.pid is not None and process.is_alive():
            process.kill()
            process.join(timeout=2)
        _close_windows_job(job)


def parse_pdf_isolated(
    content: bytes,
    hints: ParserHints,
    *,
    languages: tuple[str, ...] = ("eng",),
    engine_name: str = "local_hybrid",
    cpu_seconds: int = PDF_CPU_SECONDS,
    memory_bytes: int = PDF_MEMORY_BYTES,
    wall_seconds: float = PDF_WALL_SECONDS,
    einvoice_keys: Sequence[Any] = (),
) -> ParseResult:
    """Apply OS process limits and a parent wall-clock timeout to the extractor.

    Invoices are then checked for an e-invoice QR; ``einvoice_keys`` are the
    portal's RSA public keys that verify its signature.
    """

    result = _extract_isolated(
        content,
        hints,
        languages=languages,
        engine_name=engine_name,
        cpu_seconds=cpu_seconds,
        memory_bytes=memory_bytes,
        wall_seconds=wall_seconds,
    )
    if not result.success or hints.doc_type is not DocType.INVOICE:
        return result
    texts = _run_qr_isolated(content, memory_bytes=memory_bytes)
    if not texts:
        return result
    codes = [
        code
        for page, text in texts
        if (code := parse_einvoice_qr(text, page=page, public_keys=einvoice_keys)) is not None
    ]
    return apply_einvoice(result, codes, hints)


def _extract_isolated(
    content: bytes,
    hints: ParserHints,
    *,
    languages: tuple[str, ...],
    engine_name: str,
    cpu_seconds: int,
    memory_bytes: int,
    wall_seconds: float,
) -> ParseResult:

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    start_event = context.Event()
    process = context.Process(
        target=_child_main,
        args=(send, start_event, content, hints, cpu_seconds, memory_bytes),
        name="reconbench-pdf-extractor",
    )
    job = None
    started = time.monotonic()
    try:
        process.start()
        send.close()
        if os.name == "nt":
            job = _windows_job(process, cpu_seconds, memory_bytes)
        start_event.set()
        if not receive.poll(wall_seconds):
            process.kill()
            process.join(timeout=2)
            return _failed(
                "PDF_PROCESS_TIMEOUT", "PDF extraction exceeded its time limit and was stopped."
            )
        payload = receive.recv()
        process.join(timeout=max(0.1, wall_seconds - (time.monotonic() - started)))
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
            return _failed(
                "PDF_PROCESS_TIMEOUT", "PDF extraction exceeded its time limit and was stopped."
            )
        if not isinstance(payload, dict) or payload.get("child_error"):
            return _failed(
                "PDF_RESOURCE_LIMIT", "PDF extraction stopped at a process resource limit."
            )
        result = ParseResult.model_validate(payload)
        if not result.success:
            return result
        fully_verified = bool(result.documents) and not any(
            document.needs_verification for document in result.documents
        )
        scanned_pages = tuple(page.page for page in result.page_texts if page.scanned)
        if scanned_pages or not fully_verified:
            layout = _prefer_layout(result, content, hints, languages, engine_name, memory_bytes)
            if layout is not None:
                return layout
        elif _may_hold_several(result, hints):
            layout = _prefer_layout(
                result, content, hints, languages, engine_name, memory_bytes, split_only=True
            )
            if layout is not None:
                return layout
        if scanned_pages:
            ocr_result = _run_ocr_isolated(
                content,
                hints,
                scanned_pages,
                languages,
                cpu_seconds=PDF_OCR_CPU_SECONDS,
                memory_bytes=memory_bytes,
                wall_seconds=PDF_OCR_WALL_SECONDS,
            )
            if ocr_result is None:
                return _append_ocr_warning(
                    result,
                    "OCR could not be completed for one or more scanned pages. The original PDF "
                    "is preserved for human review.",
                )
            if not any(page.is_ocr and page.content.strip() for page in ocr_result.page_texts):
                return _append_ocr_warning(
                    result,
                    "OCR ran but did not recover readable text from the scanned pages. The "
                    "original PDF is preserved for human review.",
                )
            if _extraction_quality(ocr_result, hints) > _extraction_quality(result, hints):
                return ocr_result
            return _append_warning(
                result,
                "OCR_ASSIST_NO_IMPROVEMENT",
                (
                    "OCR did not improve field and line-item coverage; "
                    "the original extraction is shown."
                ),
            )

        # Some PDFs contain a text layer but no useful item rows (for example,
        # image-backed pages with a sparse hidden layer). Try generic OCR in that
        # case, then keep it only when field and line coverage improves.
        native_warnings = {warning.code for warning in result.warnings}
        needs_assist = _has_critical_header_gaps(result, hints) and (
            _complete_line_count(result, hints) == 0
            or bool(native_warnings & {"INVALID_NUMBER", "EXTRACTION_QA_FAILED"})
        )
        if result.page_texts and needs_assist:
            page_numbers = tuple(page.page for page in result.page_texts)
            assisted = _run_ocr_isolated(
                content,
                hints,
                page_numbers,
                languages,
                cpu_seconds=PDF_OCR_CPU_SECONDS,
                memory_bytes=memory_bytes,
                wall_seconds=PDF_OCR_WALL_SECONDS,
            )
            if (
                assisted is not None
                and any(page.is_ocr and page.content.strip() for page in assisted.page_texts)
                and _extraction_quality(assisted, hints) > _extraction_quality(result, hints)
            ):
                return assisted
            if assisted is not None and any(page.is_ocr for page in assisted.page_texts):
                return _append_warning(
                    result,
                    "OCR_ASSIST_NO_IMPROVEMENT",
                    (
                        "OCR did not improve field and line-item coverage; "
                        "the original extraction is shown."
                    ),
                )
        return result
    except (OSError, RuntimeError, EOFError, ValueError):
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        return _failed(
            "PDF_SANDBOX_UNAVAILABLE", "The PDF extraction sandbox could not be started safely."
        )
    finally:
        receive.close()
        if not send.closed:
            send.close()
        if process.pid is not None and process.is_alive():
            process.kill()
            process.join(timeout=2)
        _close_windows_job(job)


__all__ = [
    "PDF_CPU_SECONDS",
    "PDF_MEMORY_BYTES",
    "PDF_WALL_SECONDS",
    "PDF_OCR_CPU_SECONDS",
    "PDF_OCR_WALL_SECONDS",
    "parse_pdf_isolated",
]
