"""Link containment checks shared by low-level storage and image inventories."""

import os
import stat
from pathlib import Path


def linked(root: Path, d: Path) -> bool:
    """Any symlink/junction through d, or a path outside the pinned root.

    lstat's junction tag works on every supported Windows Python and does not
    mistake cloud placeholders for links. Unreadable paths fail closed.
    """
    try:
        parts = d.relative_to(root).parts
    except ValueError:
        return True
    cur = root
    for part in parts:
        cur = cur / part
        try:
            info = cur.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            return True
        if (stat.S_ISLNK(info.st_mode) or (os.name == "nt"
                and getattr(info, "st_reparse_tag", 0)
                == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None))):
            return True
    return False
