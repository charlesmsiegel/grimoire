"""File parsing helpers.

Markdown + YAML frontmatter parsing, YAML-only loading, content hashing,
and slug / scene filename generation. All file I/O is UTF-8.
"""

from grimoire.files.frontmatter import (
    FrontmatterError,
    ParsedDocument,
    parse_frontmatter,
    read_markdown,
    render_markdown,
    write_markdown,
)
from grimoire.files.hashing import content_hash
from grimoire.files.slug import (
    parse_scene_filename,
    scene_filename,
    slugify,
)
from grimoire.files.yaml_io import (
    YamlError,
    dump_yaml,
    load_yaml,
    parse_yaml,
    write_yaml,
)

__all__ = [
    "FrontmatterError",
    "ParsedDocument",
    "YamlError",
    "content_hash",
    "dump_yaml",
    "load_yaml",
    "parse_frontmatter",
    "parse_scene_filename",
    "parse_yaml",
    "read_markdown",
    "render_markdown",
    "scene_filename",
    "slugify",
    "write_markdown",
    "write_yaml",
]
