"""Read-only source removal/update previews and optional pinned dependencies (Sprint 079)."""
from .dependencies import declared_dependencies, dependency_report, pin_dependency, source_version
from .model import (
    ATTENTION, DEPENDENCIES_KEY, STATUSES, Dependency, DependencyStatus, EntityChange, Problem, Reference,
    SourceChangePreview, SourceUpgradeError,
)
from .preview import preview_source_removal, preview_source_update

__all__ = ['ATTENTION', 'DEPENDENCIES_KEY', 'STATUSES', 'Dependency', 'DependencyStatus', 'EntityChange', 'Problem',
           'Reference', 'SourceChangePreview', 'SourceUpgradeError', 'declared_dependencies', 'dependency_report',
           'pin_dependency', 'preview_source_removal', 'preview_source_update', 'source_version']
