"""Extraction engine port and registry (decision D17)."""

from __future__ import annotations

from typing import Protocol

from reconbench_core.extraction.layout import DocumentLayout

ENGINE_NAMES = ("local_hybrid",)
DEFAULT_ENGINE = "local_hybrid"


class LayoutEngine(Protocol):
    name: str

    def layout(self, content: bytes, *, max_pages: int) -> DocumentLayout: ...


class UnknownEngine(ValueError):
    pass


def engine_for(name: str, *, languages: tuple[str, ...] = ("eng",)) -> LayoutEngine:
    if name == "local_hybrid":
        from reconbench_app.extraction.local_hybrid import LocalHybridEngine

        return LocalHybridEngine(languages=languages)
    raise UnknownEngine(
        f"RB_EXTRACTION_ENGINE must be one of {', '.join(ENGINE_NAMES)}; got {name!r}"
    )


__all__ = ["DEFAULT_ENGINE", "ENGINE_NAMES", "LayoutEngine", "UnknownEngine", "engine_for"]
