"""Deterministic vendor identity mapping and candidate resolution."""

from reconbench_core.mapping.links import (
    LinkCandidate,
    LinkResolution,
    MappingConfig,
    MappingDocument,
    MappingPO,
    ProposedLink,
    resolve_links,
)
from reconbench_core.mapping.vendor import (
    VendorAliasRecord,
    VendorDocument,
    VendorMergeSuggestion,
    VendorRecord,
    VendorResolution,
    VendorResolutionBatch,
    VendorResolutionConfig,
    resolve_vendors,
)

__all__ = [
    "VendorAliasRecord",
    "VendorDocument",
    "VendorMergeSuggestion",
    "VendorRecord",
    "VendorResolution",
    "VendorResolutionBatch",
    "VendorResolutionConfig",
    "LinkCandidate",
    "LinkResolution",
    "MappingConfig",
    "MappingDocument",
    "MappingPO",
    "ProposedLink",
    "resolve_links",
    "resolve_vendors",
]
