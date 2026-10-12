"""Write the source-only APK stamp with explicit, checkout-independent paths.

Run with backend/src and backend on PYTHONPATH; Gradle supplies both. The
stamp intentionally has no build-machine Python/dependency version fields.
"""

import argparse
import json
from pathlib import Path

from grimoire import compiled_build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--templates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = {"format": 1, "source_digest": compiled_build.source_digest(
        args.package, args.templates)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
