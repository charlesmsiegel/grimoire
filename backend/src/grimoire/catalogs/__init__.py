"""Optional, pinned catalog composition and bounded discovery (Sprint 056)."""
from .compose import compose_catalogs
from .library import publish_catalog
from .model import Catalog, CatalogEntry, CatalogError, CatalogOrigin, CatalogView, catalog_envelope, catalog_from_record

__all__ = ['Catalog', 'CatalogEntry', 'CatalogError', 'CatalogOrigin', 'CatalogView', 'catalog_envelope',
           'catalog_from_record', 'compose_catalogs', 'publish_catalog']
