"""AuxiliaryCoordinator — non-canonical auxiliary tasks for the orchestrator."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from grimoire.orchestrator.errors import OrchestratorError, UnknownCampaignError
from grimoire.orchestrator.helpers import _pydantic_scene
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import AuthorKind as SceneAuthorKind
from grimoire.types.common import CampaignId, CharacterRef
from grimoire.types.extraction_modes import ExtractionMode
from grimoire.types.state import StateSnapshot
from grimoire.util import new_id

logger = logging.getLogger(__name__)


class AuxiliaryCoordinator:
    """Manages auxiliary tasks (drafts, rewrites, brainstorms)."""

    def __init__(
        self,
        *,
        host: Any,
        scenes: SceneManager,
        state_store: Any,
        context_builder: Any,
        extractor: Any,
        inflight_aux: dict[str, Any],
    ) -> None:
        self._host = host
        self._scenes = scenes
        self._store = state_store
        self._context = context_builder
        self._extractor = extractor
        self._inflight_aux = inflight_aux

    async def _require_campaign(self, campaign_id: CampaignId) -> None:
        if not await self._store.campaign_exists(campaign_id):
            raise UnknownCampaignError(campaign_id)

    async def run_auxiliary_task(
        self,
        *,
        campaign_id: CampaignId,
        task: Any,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> Any:
        from grimoire.orchestrator.auxiliary_runner import run_auxiliary_task as _run

        await self._require_campaign(campaign_id)
        return await _run(
            self._host,
            campaign_id=campaign_id,
            task=task,
            on_token=on_token,
        )

    def discard_auxiliary(self, result_id: str) -> bool:
        return self._inflight_aux.pop(result_id, None) is not None

    def list_inflight_auxiliary(self, campaign_id: CampaignId | None = None) -> list[Any]:
        results = list(self._inflight_aux.values())
        if campaign_id is None:
            return results
        tagged = getattr(self._host, "_inflight_aux_campaign", {})
        return [r for r in results if tagged.get(r.id) == campaign_id]

    async def accept_auxiliary(
        self,
        campaign_id: CampaignId,
        result_id: str,
        *,
        edited_text: str | None = None,
    ) -> dict[str, Any]:
        from grimoire.auxiliary.types import CommitAction
        from grimoire.orchestrator.errors import AuxiliaryNotFoundError

        await self._require_campaign(campaign_id)
        aux = self._inflight_aux.pop(result_id, None)
        if aux is None:
            raise AuxiliaryNotFoundError(result_id)

        text = edited_text if edited_text is not None else aux.text
        action = aux.pending_commit_action

        if action == CommitAction.SUBMIT_POST:
            pc_ref = (aux.task.extra_params or {}).get("active_pc_ref")
            if not pc_ref:
                pc_ref = await self._characters_active_pc(campaign_id)
            submit = await self._host.submit_post(campaign_id, pc_ref, text)
            logger.info(
                "[aux-accept] task=%s campaign=%s result=%s submitted",
                aux.task.kind.value,
                campaign_id,
                result_id,
            )
            return {
                "committed": True,
                "action": "submit_post",
                "result_id": result_id,
                "turn_id": getattr(submit, "turn_id", None),
            }

        if action == CommitAction.REPLACE_POST:
            try:
                return await self._accept_rewrite_post(campaign_id, aux, text)
            except Exception:
                self._inflight_aux[result_id] = aux
                raise

        if action == CommitAction.EXTEND_POST:
            target_post_id = aux.task.target_post_id
            if not target_post_id:
                self._inflight_aux[result_id] = aux
                raise OrchestratorError(f"continue-as result {result_id!r} has no target_post_id")
            try:
                _scene, existing = await self._scenes._find_post(target_post_id)
            except Exception as err:
                self._inflight_aux[result_id] = aux
                raise OrchestratorError(
                    f"continue-as target post {target_post_id!r} not found"
                ) from err
            joiner = "\n\n" if existing.body and not existing.body.endswith("\n") else ""
            new_body = f"{existing.body}{joiner}{text}"
            await self._scenes.edit_post(target_post_id, new_body, source="aux:continue_as")
            logger.info(
                "[aux-accept] task=%s campaign=%s result=%s extended=%s",
                aux.task.kind.value,
                campaign_id,
                result_id,
                target_post_id,
            )
            return {
                "committed": True,
                "action": "extend_post",
                "result_id": result_id,
                "post_id": target_post_id,
            }

        if action == CommitAction.APPEND_POST:
            scene = await self._scenes.active_scene_for_campaign(campaign_id)
            if scene is None:
                raise OrchestratorError(
                    f"no active scene for aux append in campaign {campaign_id!r}"
                )
            post = self._host._new_post(
                author_kind=SceneAuthorKind.NPC,
                body=text,
                is_player=False,
                author_npc_ref=aux.task.target_character_ref,
            )
            await self._scenes.append_post(scene.id, post)
            logger.info(
                "[aux-accept] task=%s campaign=%s result=%s appended=%s",
                aux.task.kind.value,
                campaign_id,
                result_id,
                post.id,
            )
            return {
                "committed": True,
                "action": "append_post",
                "result_id": result_id,
                "post_id": post.id,
            }

        logger.info(
            "[aux-accept] task=%s campaign=%s result=%s action=%s no-op",
            aux.task.kind.value,
            campaign_id,
            result_id,
            action.value,
        )
        return {
            "committed": True,
            "action": action.value,
            "result_id": result_id,
            "text": text,
        }

    async def _characters_active_pc(self, campaign_id: CampaignId) -> CharacterRef:
        getter = getattr(self._context, "_characters", None)
        if getter is not None and hasattr(getter, "active_pc"):
            ref = await getter.active_pc(campaign_id)
            if ref:
                return ref
        row = await self._store.db.fetchone(
            "SELECT character_ref FROM campaign_pcs WHERE campaign_id = ? LIMIT 1",
            (campaign_id,),
        )
        if row is None:
            raise OrchestratorError(f"no active PC for campaign {campaign_id!r}")
        return row["character_ref"] if hasattr(row, "__getitem__") else row[0]

    async def _accept_rewrite_post(
        self,
        campaign_id: CampaignId,
        aux: Any,
        text: str,
    ) -> dict[str, Any]:
        from grimoire.auxiliary.types import TaskKind
        from grimoire.scenes.types import Alternate

        post_id = aux.task.target_post_id
        if not post_id:
            raise OrchestratorError("rewrite_post auxiliary missing target_post_id")
        scene, post = await self._host._alternates.find_scene_and_post(post_id)
        new_alt_id = new_id("a", length=16)
        new_ds_id = new_id("ds", length=16)

        deltas: list[Any] = []
        try:
            pyd_scene = _pydantic_scene(scene)
            snapshot = StateSnapshot(campaign_id=campaign_id, scene_id=scene.id)
            extraction = await self._extractor.extract(
                text,
                pyd_scene,
                campaign_id,
                snapshot,
                turn_id=post.turn_id,
                mode=ExtractionMode.SEPARATE,
            )
            deltas = list(getattr(extraction, "deltas", []) or [])
        except Exception as exc:
            logger.warning("aux rewrite_post extraction failed: %s", exc)
            deltas = []

        alt = Alternate(
            id=new_alt_id,
            post_id=post_id,
            text=text,
            delta_set_id=new_ds_id,
            author_kind=post.author_kind,
            model=getattr(aux, "model_used", None) or "",
            prompt_hash=None,
            steering_hint=aux.task.edit_instruction,
            created_at=datetime.now(UTC),
            tokens=getattr(aux, "tokens", None),
            pinned=False,
            is_primary=False,
        )
        await self._scenes.append_alternate(post_id, alt)

        if deltas and hasattr(self._store, "apply_delta_set"):
            try:
                await self._store.apply_delta_set(
                    deltas=deltas,
                    delta_set_id=new_ds_id,
                    campaign_id=campaign_id,
                    turn_id=post.turn_id,
                    source="orchestrator:aux-rewrite",
                )
            except Exception as exc:
                logger.warning("aux rewrite_post apply_delta_set failed: %s", exc)

        switch = await self._host.switch_primary_alternate(
            campaign_id=campaign_id, post_id=post_id, alternate_id=new_alt_id
        )
        logger.info(
            "[aux-accept] task=%s campaign=%s result=%s cascaded_replace=%s alt=%s",
            TaskKind.REWRITE_POST.value,
            campaign_id,
            aux.id,
            bool(switch.get("delta_swap")),
            new_alt_id,
        )
        return {
            "committed": True,
            "action": "replace_post",
            "result_id": aux.id,
            "post_id": post_id,
            "alternate_id": new_alt_id,
            "cascaded_replace": bool(switch.get("delta_swap")),
        }
