"""Request models: mechanics domain."""


from __future__ import annotations

from pydantic import BaseModel

# ---- models ----

class ModuleCreate(BaseModel):
    name: str


class ModuleManifestBody(BaseModel):
    name: str = ""
    description: str = ""
    version: str = ""
    dice: str = ""
    notes: str = ""
    dry_run: bool = False


class ModuleGroupBody(BaseModel):
    group: dict = {}
    dry_run: bool = False


class ModuleSheetTypeBody(BaseModel):
    sheet_type: dict = {}
    dry_run: bool = False


class ModuleCheckBody(BaseModel):
    check: dict = {}
    dry_run: bool = False


class ModuleDefaultsBody(BaseModel):
    defaults: dict = {}
    dry_run: bool = False


class ModuleRuleBody(BaseModel):
    flags: dict = {}
    body: str = ""
    dry_run: bool = False


class ModuleContentBody(BaseModel):
    name: str = ""
    body: str = ""
    keys: str = ""
    fields: dict = {}
    sheet: dict | None = None
    dry_run: bool = False


class ModuleLayoutBody(BaseModel):
    layout: dict = {}
    dry_run: bool = False


class ModuleThemeBody(BaseModel):
    theme: dict = {}
    dry_run: bool = False


class ModuleRenameBody(BaseModel):
    kind: str = ""
    address: dict = {}
    to: str = ""
    dry_run: bool = False


class ModuleSetting(BaseModel):
    module: str = ""


class SheetBody(BaseModel):
    sheet_type: str
    fields: dict | None = None
    expected: dict | None = None  # omitted == null == "assert no sheet exists"


class SheetCreationBody(BaseModel):
    sheet_type: str
    spends: dict[str, dict[str, int]] = {}
    expected: dict | None = None  # omitted == null == "assert no sheet exists"


class SheetAdvanceBody(BaseModel):
    field: str


class SheetBulkBody(BaseModel):
    # Per file kind, the sheet type a bulk create should use. A kind the module
    # has exactly one sheet type for needs no entry; one with several has no
    # default, and is skipped-with-a-reason when it has none here.
    types: dict[str, str] = {}


