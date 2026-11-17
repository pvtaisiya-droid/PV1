from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from app.database import SessionLocal
from app.models import (
    Attachment,
    AuditTrail,
    PSMFBlock,
    PSMFBlockVersion,
    PSMFComment,
    PSMFComponent,
    PSMFComponentVersion,
    PSMFPartnerBuild,
    PSMFPartnerBuildItem,
)
from app.psmf import PSMF_SOURCE_MODULE, ensure_psmf_seed_data, get_psmf_settings


GENERATED_ROOT = (WORKSPACE / "uploads" / "psmf").resolve()


def descendant_component_depths(db, root_id: str) -> dict[str, int]:
    depths: dict[str, int] = {}
    frontier = {root_id}
    depth = 1
    while frontier:
        rows = (
            db.query(PSMFComponent.id)
            .filter(PSMFComponent.parent_component_id.in_(frontier))
            .all()
        )
        next_frontier = {row_id for (row_id,) in rows if row_id not in depths}
        for component_id in next_frontier:
            depths[component_id] = depth
        frontier = next_frontier
        depth += 1
    return depths


def safe_clear_generated_files() -> int:
    workspace = WORKSPACE.resolve()
    expected = (workspace / "uploads" / "psmf").resolve()
    if GENERATED_ROOT != expected:
        raise RuntimeError(f"Неверный каталог выгрузок: {GENERATED_ROOT}")
    GENERATED_ROOT.relative_to(workspace)
    if not GENERATED_ROOT.exists():
        GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
        return 0

    files = [path for path in GENERATED_ROOT.rglob("*") if path.is_file()]
    for path in files:
        path.resolve().relative_to(GENERATED_ROOT)
        path.unlink()
    directories = sorted(
        [path for path in GENERATED_ROOT.rglob("*") if path.is_dir()],
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        directory.rmdir()
    return len(files)


def reset() -> dict[str, int | str]:
    with SessionLocal() as db:
        annex_root = db.query(PSMFComponent).filter(PSMFComponent.code == "9").first()
        if not annex_root:
            raise RuntimeError("Раздел 9 «Приложения к МФСФ» не найден.")

        depths = descendant_component_depths(db, annex_root.id)
        component_ids = set(depths)
        block_ids = {
            block_id
            for (block_id,) in db.query(PSMFBlock.id)
            .filter(PSMFBlock.component_id.in_(component_ids))
            .all()
        }
        component_version_ids = {
            version_id
            for (version_id,) in db.query(PSMFComponentVersion.id)
            .filter(PSMFComponentVersion.component_id.in_(component_ids))
            .all()
        }
        block_version_ids = {
            version_id
            for (version_id,) in db.query(PSMFBlockVersion.id)
            .filter(PSMFBlockVersion.block_id.in_(block_ids))
            .all()
        }
        build_ids = {build_id for (build_id,) in db.query(PSMFPartnerBuild.id).all()}
        build_item_ids = {
            item_id
            for (item_id,) in db.query(PSMFPartnerBuildItem.id)
            .filter(PSMFPartnerBuildItem.build_id.in_(build_ids))
            .all()
        }
        related_ids = component_ids | block_ids | component_version_ids | block_version_ids
        comment_ids = {
            comment_id
            for (comment_id,) in db.query(PSMFComment.id)
            .filter(PSMFComment.object_id.in_(related_ids))
            .all()
        }
        attachment_ids = {
            attachment_id
            for (attachment_id,) in db.query(Attachment.id)
            .filter(
                (Attachment.related_object_id.in_(related_ids))
                | (Attachment.related_object_type.ilike("PSMF%"))
            )
            .all()
        }
        audit_entity_ids = (
            related_ids
            | build_ids
            | build_item_ids
            | comment_ids
            | attachment_ids
        )

        counts: dict[str, int | str] = {
            "old_components": len(component_ids),
            "old_component_versions": len(component_version_ids),
            "old_blocks": len(block_ids),
            "old_block_versions": len(block_version_ids),
            "old_builds": len(build_ids),
            "old_build_items": len(build_item_ids),
            "old_comments": len(comment_ids),
            "old_attachments": len(attachment_ids),
        }

        if build_ids:
            db.query(PSMFPartnerBuildItem).filter(
                PSMFPartnerBuildItem.build_id.in_(build_ids)
            ).delete(synchronize_session=False)
            db.query(PSMFPartnerBuild).filter(
                PSMFPartnerBuild.id.in_(build_ids)
            ).delete(synchronize_session=False)
        if comment_ids:
            db.query(PSMFComment).filter(PSMFComment.id.in_(comment_ids)).delete(
                synchronize_session=False
            )
        if attachment_ids:
            db.query(Attachment).filter(Attachment.id.in_(attachment_ids)).delete(
                synchronize_session=False
            )
        if audit_entity_ids:
            db.query(AuditTrail).filter(
                AuditTrail.source_module == PSMF_SOURCE_MODULE,
                AuditTrail.entity_id.in_(audit_entity_ids),
            ).delete(synchronize_session=False)
        if block_version_ids:
            db.query(PSMFBlockVersion).filter(
                PSMFBlockVersion.id.in_(block_version_ids)
            ).delete(synchronize_session=False)
        if block_ids:
            db.query(PSMFBlock).filter(PSMFBlock.id.in_(block_ids)).delete(
                synchronize_session=False
            )
        if component_version_ids:
            db.query(PSMFComponentVersion).filter(
                PSMFComponentVersion.id.in_(component_version_ids)
            ).delete(synchronize_session=False)
        for component_id, _depth in sorted(
            depths.items(), key=lambda item: item[1], reverse=True
        ):
            db.query(PSMFComponent).filter(PSMFComponent.id == component_id).delete(
                synchronize_session=False
            )

        db.flush()
        ensure_psmf_seed_data(db)
        settings = get_psmf_settings(db)
        settings.include_optional_annex_k_if_has_content = True
        settings.skip_empty_partner_specific_blocks = False
        db.commit()

        new_component_ids = descendant_component_depths(db, annex_root.id)
        counts["new_components"] = len(new_component_ids)
        counts["new_blocks"] = (
            db.query(PSMFBlock)
            .filter(PSMFBlock.component_id.in_(set(new_component_ids)))
            .count()
        )

    counts["deleted_generated_files"] = safe_clear_generated_files()
    counts["status"] = "ok"
    return counts


if __name__ == "__main__":
    print(json.dumps(reset(), ensure_ascii=False, indent=2))
