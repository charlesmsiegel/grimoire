"""Version-scoped expression authoring; no generation or tracker dependency."""

import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ...store import image_refs
from ...store.expression_library import (
    actor_pack_data,
    character_pack,
    contracts,
    images,
    lifecycle,
    packs,
    paths,
    read,
    write,
)
from ...store.record_art import targets
from .. import common


class Mutations(BaseModel):
    expected_effective_revision: str
    mutations: list[dict]


class Selection(BaseModel):
    expected_effective_revision: str
    selection: list[dict]


class LineageSelection(BaseModel):
    disposition: str


@contextmanager
def errors():
    try:
        yield
    except contracts.ExpressionBudgetError as exc:
        raise HTTPException(413, detail=str(exc)) from exc
    except (contracts.ExpressionConflictError, contracts.ExpressionUnavailableError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except (targets.ArtTargetNotFoundError, FileNotFoundError) as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except contracts.ExpressionInvalidError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


def scoped_router(scope: str) -> APIRouter:
    result = APIRouter(prefix=f"/{scope}s/{{owner_id}}/{{kind}}/{{actor_id}}/versions/{{version_id}}/expressions")

    def target(owner_id: str, kind: str, actor_id: str, version_id: str):
        with errors():
            value = contracts.ExpressionTarget(scope, owner_id, kind, actor_id, version_id)
            paths.resolved(value)
            return value

    @result.get("")
    def collection(address=Depends(target)):
        with errors():
            return read.effective(address)

    @result.post("/mutations")
    def mutate(body: Mutations, address=Depends(target)):
        with errors():
            validate_mutations(body.mutations)
            return write.apply_effective(address, body.mutations,
                                         expected_effective_revision=body.expected_effective_revision)

    @result.post("/cleanup")
    def cleanup(address=Depends(target)):
        with errors():
            return {"removed_revisions": lifecycle.cleanup_revisions(address)}

    @result.post("/imports/preview")
    def preview(file: UploadFile = File(...), mode: str = Form(...), outfit_id: str | None = Form(None),
                address=Depends(target)):
        with errors():
            return packs.preview(address, file.file, mode=mode, outfit_id=outfit_id)

    @result.post("/imports/{token}/commit")
    def publish(token: str, body: Selection, address=Depends(target)):
        with errors():
            return packs.publish(address, token, body.selection,
                                 expected_effective_revision=body.expected_effective_revision)

    @result.post("/imports/folder")
    def folder(files: list[UploadFile] = File(...), mode: str = Form("grimoire"),
               outfit_id: str | None = Form(None), address=Depends(target)):
        with errors():
            return packs.preview_folder(address, [(file.filename or "", file.file) for file in files],
                                        mode=mode, outfit_id=outfit_id)

    @result.get("/export")
    def export(address=Depends(target)):
        with errors(), tempfile.TemporaryDirectory(prefix="grimoire-expressions-") as temporary:
            destination = Path(temporary) / "expressions.zip"
            packs.export(address, destination)
            return Response(destination.read_bytes(), media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="{address.actor_id}-expressions.zip"'})

    _image_routes(result, target)
    return result


def _image_routes(result: APIRouter, target):
    @result.get("/{outfit}/{label}/image")
    def image(outfit: str, label: str, request: Request, address=Depends(target)):
        with errors():
            return serve_image(address, outfit, label, request)

    @result.put("/{outfit}/{label}/image")
    def upload(outfit: str, label: str, file: UploadFile = File(...),
               expected_effective_revision: str = Form(...), address=Depends(target)):
        with errors():
            data = packs._bounded(file.file, images.MAX_BYTES)
            return write.apply_effective(address, [{"op": "put-expression", "outfit": outfit,
                "expression": label, "data": data}], expected_effective_revision=expected_effective_revision)

def validate_mutations(mutations: list[dict]) -> None:
    if any(m.get("op") == "put-expression" for m in mutations):
        raise contracts.ExpressionInvalidError("Upload sprite images through the image endpoint")


def serve_image(address, outfit: str, label: str, request: Request):
    contracts.slug(outfit)
    contracts.slug(label)
    collection = read.effective(address)
    row = collection["outfits"].get(outfit, {}).get("expressions", {}).get(label)
    resolved = image_refs.resolve_ref(image_refs.Ref(label, row["image_id"], None)) if row else None
    if resolved is None:
        raise HTTPException(404, detail="Expression image unavailable")
    return common._serve_image_file(resolved.blob_path, request)


def actor_router(scope: str) -> APIRouter:
    result = APIRouter(prefix=f"/{scope}s/{{owner_id}}/{{kind}}/{{actor_id}}/bundle")

    def target(owner_id: str, kind: str, actor_id: str):
        with errors():
            value = contracts.ExpressionActorTarget(scope, owner_id, kind, actor_id)
            actor_pack_data.owner_root(value)
            return value

    @result.get("")
    def export_actor(address=Depends(target)):
        with errors(), tempfile.TemporaryDirectory(prefix="grimoire-actor-") as temporary:
            destination = Path(temporary) / "actor.zip"
            character_pack.export(address, destination)
            return Response(destination.read_bytes(), media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="{address.actor_id}.grimoire-character.zip"'})

    @result.post("/imports/preview")
    def preview_actor(file: UploadFile = File(...), address=Depends(target)):
        with errors():
            return character_pack.import_preview(file.file, address)

    @result.post("/imports/{token}/commit")
    def publish_actor(token: str, body: LineageSelection, address=Depends(target)):
        with errors():
            return character_pack.import_commit(token, address, body.disposition).__dict__

    return result


router = APIRouter()
router.routes.extend(actor_router("world").routes)
router.routes.extend(actor_router("campaign").routes)
router.routes.extend(scoped_router("world").routes)
router.routes.extend(scoped_router("campaign").routes)
