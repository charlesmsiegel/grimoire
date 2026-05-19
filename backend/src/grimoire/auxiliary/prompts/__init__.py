"""Jinja prompt templates for auxiliary tasks (one per `TaskKind`)."""

from __future__ import annotations

from functools import lru_cache

from jinja2 import Environment, PackageLoader, Template, select_autoescape

from grimoire.auxiliary.types import TaskKind

_TEMPLATE_FILES: dict[TaskKind, str] = {
    TaskKind.IMPERSONATE_PC: "impersonate_pc.j2",
    TaskKind.REWRITE_POST: "rewrite_post.j2",
    TaskKind.CONTINUE_AS: "continue_as.j2",
    TaskKind.WHAT_WOULD_X_SAY: "what_would_x_say.j2",
    TaskKind.BRAINSTORM: "brainstorm.j2",
    TaskKind.EDIT_PROSE: "edit_prose.j2",
    TaskKind.TRANSLATE: "translate.j2",
}


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=PackageLoader("grimoire.auxiliary", "prompts"),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def load_template(kind: TaskKind) -> Template:
    return _env().get_template(_TEMPLATE_FILES[kind])


__all__ = ["load_template"]
