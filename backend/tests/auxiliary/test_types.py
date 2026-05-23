from grimoire.auxiliary.types import (
    AuxiliaryResult,
    AuxiliaryTask,
    CommitAction,
    TaskKind,
    commit_action_for,
)


def test_task_kinds_complete():
    assert {k.value for k in TaskKind} == {
        "impersonate_pc",
        "rewrite_post",
        "continue_as",
        "what_would_x_say",
        "brainstorm",
        "edit_prose",
        "translate",
    }


def test_task_kind_to_commit_action_map():
    assert commit_action_for(TaskKind.IMPERSONATE_PC) == CommitAction.SUBMIT_POST
    assert commit_action_for(TaskKind.REWRITE_POST) == CommitAction.REPLACE_POST
    assert commit_action_for(TaskKind.CONTINUE_AS) == CommitAction.EXTEND_POST
    assert commit_action_for(TaskKind.WHAT_WOULD_X_SAY) == CommitAction.COPY
    assert commit_action_for(TaskKind.BRAINSTORM) == CommitAction.COPY
    assert commit_action_for(TaskKind.EDIT_PROSE) == CommitAction.REPLACE_DRAFT
    assert commit_action_for(TaskKind.TRANSLATE) == CommitAction.REPLACE_DRAFT


def test_every_kind_has_action():
    for kind in TaskKind:
        assert isinstance(commit_action_for(kind), CommitAction)


def test_aux_task_dataclass_defaults():
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM)
    assert task.target_character_ref is None
    assert task.target_post_id is None
    assert task.extra_params == {}


def test_aux_result_dataclass():
    from datetime import UTC, datetime

    result = AuxiliaryResult(
        id="ar_1",
        task=AuxiliaryTask(kind=TaskKind.BRAINSTORM),
        text="hello",
        completed_at=datetime.now(UTC),
        model_used="claude-haiku-4-5",
        tokens=12,
        pending_commit_action=CommitAction.COPY,
    )
    assert result.warnings == []
