"""Run untrusted PDF parsing in a resource-bounded child process."""

from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from reconbench_core.parsers.pdf import parse_pdf
from reconbench_core.parsers.types import ParseResult, ParserHints, ParseWarning
from reconbench_core.schema import FileFormat

PDF_CPU_SECONDS = 8
PDF_MEMORY_BYTES = 1024 * 1024 * 1024
PDF_WALL_SECONDS = 12
PDF_OCR_CPU_SECONDS = 90
PDF_OCR_WALL_SECONDS = 120
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
    cpu_seconds: int = PDF_CPU_SECONDS,
    memory_bytes: int = PDF_MEMORY_BYTES,
    wall_seconds: float = PDF_WALL_SECONDS,
) -> ParseResult:
    """Apply OS process limits and a parent wall-clock timeout to the extractor."""

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
        scanned_pages = tuple(page.page for page in result.page_texts if page.scanned)
        if not result.success or not scanned_pages:
            return result
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
        return ocr_result
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
