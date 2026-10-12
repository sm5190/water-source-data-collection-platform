"""Backward-compatible import path; each scholarly provider now has its own module."""

from .crossref import CrossrefDiscovery
from .openalex import OpenAlexDiscovery
from .semantic_scholar import SemanticScholarDiscovery

__all__ = ["CrossrefDiscovery", "OpenAlexDiscovery", "SemanticScholarDiscovery"]
