"""Layout-aware document extraction from positioned words (pure; no I/O).

An extraction engine in the application layer turns each PDF page into
positioned words, from the text layer or from OCR. Everything here works only
on those words: page classification, label-anchored header fields, column-based
tables, OCR-tolerant values and cross-check verification.
"""
