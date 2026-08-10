"""
AIMFP Helper Functions - Catalog

Adoption of an existing FP codebase into AIMFP tracking.

Two stages, deliberately separated so the AI reviews before anything is written:

1. ``scan_source_tree`` — read-only inventory of the source tree with extracted
   signatures, honoring the watchdog's exclusion stack.
2. ``catalog_files`` / ``catalog_functions`` / ``catalog_types`` — single-phase
   registration of code that already exists on disk.

Everything downstream is the ordinary batch surface: compose ``add_files_to_module``,
``add_file_flows``, ``add_interactions``, and ``add_types_functions`` on top.

Scope note: this path is niche by design. AIMFP rejects OOP codebases, so most
projects never reach a catalog — but a functional codebase adopting AIMFP has no
other way in, and doing it by hand does not scale past a few dozen functions.
"""

from .extract import (
    ExtractedEntities,
    ExtractedFunction,
    ExtractedType,
    extract_entities,
    extract_python,
)
from .register import (
    CatalogResult,
    catalog_files,
    catalog_functions,
    catalog_types,
)
from .scan import ScanResult, ScannedFile, scan_source_tree

__all__ = [
    'ExtractedEntities',
    'ExtractedFunction',
    'ExtractedType',
    'extract_entities',
    'extract_python',
    'CatalogResult',
    'catalog_files',
    'catalog_functions',
    'catalog_types',
    'ScanResult',
    'ScannedFile',
    'scan_source_tree',
]
