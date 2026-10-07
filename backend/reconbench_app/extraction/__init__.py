"""Extraction engines that turn PDF pages into positioned words (decision D17).

The engine is selected by ``RB_EXTRACTION_ENGINE``. Only ``local_hybrid`` exists:
text-layer words for digital pages and local Tesseract OCR for scanned pages.
An external Document AI engine can be added later behind the same port; the
pure extraction and cross-checks in ``reconbench_core.extraction`` stay the same.
"""
