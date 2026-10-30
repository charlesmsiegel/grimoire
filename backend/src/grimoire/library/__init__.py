"""V2 library location and V1 isolation (filesystem adapter; not part of the pure core)."""

from .paths import (
    DEFAULT_DIRNAME,
    V1_DEFAULT_DIRNAME,
    V1_HOME_ENV,
    V1_POINTER_NAME,
    V1_ROOT_MARKERS,
    V2_HOME_ENV,
    LibraryLocation,
    LibraryPathError,
    V1IsolationError,
    check_migration_paths,
    check_write_destination,
    resolve_library,
)

__all__ = [
    "DEFAULT_DIRNAME", "V1_DEFAULT_DIRNAME", "V1_HOME_ENV", "V1_POINTER_NAME",
    "V1_ROOT_MARKERS", "V2_HOME_ENV", "LibraryLocation", "LibraryPathError",
    "V1IsolationError", "check_migration_paths", "check_write_destination",
    "resolve_library",
]
