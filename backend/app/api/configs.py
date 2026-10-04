"""Config generation endpoints — the multi-vendor / ForgeOS output surface."""

from __future__ import annotations

import logging
from typing import Annotated

from engine.emulation.podman_adaptor import PodmanSocketUnreachable
from fastapi import APIRouter, Depends, Query

from app.api.deps import repo, translate_not_found
from app.exceptions.base import AppException
from app.models import ConfigArtifact, GenerateConfigRequest
from app.services import configgen
from app.services.emul_lab import EmulationLeaseUnavailable, get_emul_lab_manager
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound

router = APIRouter(tags=["configs"])
logger = logging.getLogger(__name__)


class ConfigGenFailed(AppException):
    status_code = 422
    code = "CONFIG_GEN_FAILED"


class ConfigApplyFailed(AppException):
    status_code = 422
    code = "CONFIG_APPLY_FAILED"


@router.post("/projects/{project_id}/nodes/{node_id}/configs/{artifact_id}/apply")
async def apply_config(
    project_id: str,
    node_id: str,
    artifact_id: str,
    r: Annotated[MemoryRepository, Depends(repo)],
) -> dict:
    try:
        topo = await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    node = next((n for n in topo.nodes if n.id == node_id), None)
    if node is None:
        raise ConfigApplyFailed("node does not belong to project")
    artifact = next((a for a in await r.configs_for_node(node_id) if a.id == artifact_id), None)
    if artifact is None or artifact.node_id != node_id:
        raise ConfigApplyFailed("artifact does not belong to node")
    if (topo.project.mode != "pure-emul" or str(node.mode) != "emul"
            or str(node.nos) != "frr" or artifact.vendor.lower() != "frr"
            or str(artifact.format) != "cli"):
        raise ConfigApplyFailed("only CLI FRR artifacts for pure-emul FRR nodes can be applied")
    try:
        result = await get_emul_lab_manager().push_config(topo, node_id, artifact.content, "cli")
    except (EmulationLeaseUnavailable, PodmanSocketUnreachable) as exc:
        raise ConfigApplyFailed(str(exc)) from exc
    except Exception as exc:
        # Podman/vtysh exceptions can contain full configuration or passwords.
        logger.warning("FRR apply failed for project=%s node=%s: %s", project_id, node_id, type(exc).__name__)
        raise ConfigApplyFailed("FRR apply or verification failed; inspect local backend/runtime logs") from exc
    return {"project_id": project_id, "node_id": node_id, "artifact_id": artifact_id, **result}


@router.post("/configs/generate", response_model=ConfigArtifact, status_code=201)
async def generate_config(
    body: GenerateConfigRequest, r: Annotated[MemoryRepository, Depends(repo)]
):
    try:
        node = await r.get_node(body.node_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    try:
        artifact = configgen.build_artifact(node, body.vendor)
    except configgen.ConfigGenError as exc:
        raise ConfigGenFailed(str(exc)) from exc
    await r.add_config(artifact)
    # link the latest artifact back onto the node
    await r.update_node(node.id, {"config_ref": artifact.id})
    return artifact


@router.get("/configs", response_model=list[ConfigArtifact])
async def list_configs(
    r: Annotated[MemoryRepository, Depends(repo)], node_id: str = Query(...)
):
    return await r.configs_for_node(node_id)


@router.get("/nodes/{node_id}/config/diff")
async def node_config_diff(
    node_id: str,
    r: Annotated[MemoryRepository, Depends(repo)],
    vendor: str | None = Query(None),
) -> dict:
    """Unified diff of a node's stored config vs a freshly regenerated one
    (NG-CFG-03) — preview before overwrite."""
    try:
        node = await r.get_node(node_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    if vendor is not None and configgen._TEMPLATE_MAP.get(vendor.lower()) is None:
        raise ConfigGenFailed(f"no template for vendor '{vendor}'")
    try:
        new = configgen.render(node, configgen.vendor_for(node, vendor))
    except configgen.ConfigGenError as exc:
        raise ConfigGenFailed(str(exc)) from exc
    stored = await r.configs_for_node(node_id)
    old = stored[-1].content if stored else ""
    diff = configgen.config_diff(old, new, node.name)
    return {
        "node_id": node_id,
        "vendor": configgen.vendor_for(node, vendor),
        "had_stored": bool(stored),
        "changed": bool(diff),
        "diff": diff,
    }


@router.get("/projects/{project_id}/configs/export")
async def export_project_configs(
    project_id: str,
    r: Annotated[MemoryRepository, Depends(repo)],
    vendor: str | None = Query(None),
) -> dict:
    """Whole-project vendor export (NG-CFG-01): every device's config, rendered
    to ``vendor`` (or each node's native NOS when omitted)."""
    try:
        topo = await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    if vendor is not None and configgen._TEMPLATE_MAP.get(vendor.lower()) is None:
        raise ConfigGenFailed(f"no template for vendor '{vendor}'")
    return {
        "project_id": project_id,
        "vendor": vendor.lower() if vendor else "native",
        "configs": configgen.export_project(topo.nodes, vendor),
    }
