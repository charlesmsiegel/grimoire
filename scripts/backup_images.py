"""Write an image-only archive of the active Grimoire library.

Run with the installed backend's Python, for example:
    backend/.venv/Scripts/python.exe scripts/backup_images.py  (Windows)
    backend/.venv/bin/python scripts/backup_images.py          (Unix)

The source is resolved by store.paths.home(), including GRIMOIRE_HOME and the
configured storage location. The destination is the configured backup folder.
"""

from __future__ import annotations

from grimoire.store.backups import create_image_backup


def main() -> None:
    print(create_image_backup())


if __name__ == "__main__":
    main()
