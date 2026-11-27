"""统一资源接口：查询、导出、登记、动作及跨模块联动查询。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import SERVICE_INSTANCES, service_for
from app.services.archive_intake import intake_service
from app.store import store

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


# ── 档案入库清单：必须在 /{module} 动态路由之前注册 ──
# 旧编号仍可查：档案本身照旧走 /api/archive_mgmt/{id}，凭据以档案编号为原编号。

@router.get("/archive-intake")
def list_intake(
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    source_id: int | None = Query(default=None),
    archive_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """档案入库清单：一行对应一份档案和它的入库凭据。"""
    intake_service.ensure_migrated()
    items, total = intake_service.list_entries(
        keyword=keyword, status=status, page=page, size=size,
        source_id=source_id, archive_id=archive_id,
    )
    return {
        "items": [dict(item) for item in items],
        "total": total,
        "page": page,
        "size": size,
        "migration": intake_service.migration_state(),
    }


@router.get("/archive-intake/migration")
def intake_migration_state() -> dict[str, Any]:
    """查看迁移/回滚状态，确认升级是否已经执行。"""
    return intake_service.migration_state()


@router.post("/archive-intake/migrate")
def intake_migrate() -> dict[str, Any]:
    """迁移旧式档案关联为入库凭据；可重复执行，不会重复插入同源档案。"""
    return intake_service.migrate(triggered_by="api")


@router.post("/archive-intake/rollback")
def intake_rollback() -> dict[str, Any]:
    """回滚迁移：只撤销迁移凭据，不动验收签署和人工确认结果。"""
    return intake_service.rollback(triggered_by="api")


@router.post("/archive-intake/restore")
def intake_restore() -> dict[str, Any]:
    """恢复迁移：重新建立迁移凭据，不回退已完成的签署关联。"""
    return intake_service.restore(triggered_by="api")


@router.get("/archive-intake/{credential_id}")
def get_intake(credential_id: int) -> dict[str, Any]:
    """按凭据编号查看入库凭据及其全部关联动作轨迹。"""
    credential = intake_service.get(credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail=f"入库凭据 {credential_id} 不存在")
    result = dict(credential)
    result["events"] = intake_service.events_for(credential_id)
    archive = store.find("archive_mgmt", int(credential.get("档案编号", 0)))
    result["archive"] = dict(archive) if archive else None
    return result


@router.get("/archive-intake/{credential_id}/events")
def get_intake_events(credential_id: int) -> dict[str, Any]:
    """逐条列出该凭据上发生过的关联动作（迁移、确认、签署、入库、回滚）。"""
    credential = intake_service.get(credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail=f"入库凭据 {credential_id} 不存在")
    events = intake_service.events_for(credential_id)
    return {"credential_id": credential_id, "events": events, "total": len(events)}


@router.get("/archive-intake/by-archive/{archive_id}")
def get_intake_by_archive(archive_id: int) -> dict[str, Any]:
    """按原档案编号查凭据，保证旧编号仍然可查（回滚留档的也返回历史）。"""
    credential = intake_service.by_archive(archive_id)
    if credential is None:
        credential = intake_service.by_archive(archive_id, include_revoked=True)
    if credential is None:
        raise HTTPException(status_code=404, detail=f"档案 {archive_id} 暂无入库凭据")
    result = dict(credential)
    result["events"] = intake_service.events_for_archive(archive_id)
    return result


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(
        entry_id, payload.action.strip(), values=payload.values, remark=payload.remark)
    return ActionResult(ok=entry is not None, message=message, entry=entry)


# ── 跨模块联动查询 API ──

@router.get("/cross-module/snapshot")
def cross_module_snapshot(
    modules: str = Query(default=""),
) -> dict[str, Any]:
    """返回多个模块的状态快照，供前端跨模块面板使用。"""
    module_list = [m.strip() for m in modules.split(",") if m.strip()] if modules else []
    if not module_list:
        module_list = store.module_names()
    return store.cross_module_snapshot(module_list)


@router.get("/cross-module/dependent-status/{module}/{entry_id}")
def get_dependent_status(module: str, entry_id: int) -> dict[str, Any]:
    """获取指定条目的上下游跨模块依赖状态。"""
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"条目 {entry_id} 在 {module} 中不存在")

    result: dict[str, Any] = {
        "module": module,
        "entry_id": entry_id,
        "entry": entry,
        "upstream": [],
        "downstream": [],
    }

    # 按模块键建立跨模块查询规则
    if module == "work_sequence":
        process = entry.get("工序名称", "")
        # 上游：前置工序、安全交底
        prereq = entry.get("前置工序", "")
        for ws in store.rows("work_sequence"):
            if ws.get("工序名称", "") == prereq:
                result["upstream"].append({"module": "work_sequence", "id": ws.get("id"),
                    "name": ws.get("工序名称"), "status": ws.get("status")})
        for safety in store.rows("safety_briefing"):
            if safety.get("交底工序", "") == process:
                result["upstream"].append({"module": "safety_briefing", "id": safety.get("id"),
                    "name": safety.get("交底工序"), "status": safety.get("status")})
        # 下游：质量检查、验收记录、排期
        for qi in store.rows("quality_inspect"):
            if qi.get("检查工序", "") == process:
                result["downstream"].append({"module": "quality_inspect", "id": qi.get("id"),
                    "name": qi.get("检查工序"), "status": qi.get("status")})
        for ar in store.rows("acceptance_record"):
            if ar.get("验收工序", "") == process:
                result["downstream"].append({"module": "acceptance_record", "id": ar.get("id"),
                    "name": ar.get("验收工序"), "status": ar.get("status")})
    elif module == "quality_inspect":
        process = entry.get("检查工序", "")
        for ar in store.rows("acceptance_record"):
            if ar.get("验收工序", "") == process:
                result["downstream"].append({"module": "acceptance_record", "id": ar.get("id"),
                    "name": ar.get("验收工序"), "status": ar.get("status")})
    elif module == "change_order":
        process = entry.get("影响工序", "")
        for sched in store.rows("construction_schedule"):
            if sched.get("计划工序", "") == process:
                result["downstream"].append({"module": "construction_schedule", "id": sched.get("id"),
                    "name": sched.get("计划工序"), "status": sched.get("status")})
    elif module == "acceptance_record":
        process = entry.get("验收工序", "")
        for qi in store.rows("quality_inspect"):
            if qi.get("检查工序", "") == process:
                result["upstream"].append({"module": "quality_inspect", "id": qi.get("id"),
                    "name": qi.get("检查工序"), "status": qi.get("status")})
    elif module == "construction_schedule":
        process = entry.get("计划工序", "")
        for co in store.rows("change_order"):
            if co.get("影响工序", "") == process:
                result["upstream"].append({"module": "change_order", "id": co.get("id"),
                    "name": co.get("变更事项"), "status": co.get("status")})

    return result


@router.get("/cross-module/dependency-graph")
def dependency_graph() -> dict[str, Any]:
    """返回跨模块依赖关系图。"""
    graph: dict[str, Any] = {
        "nodes": [],
        "edges": [],
    }
    # 各模块依存关系
    for mod_name in store.module_names():
        service = SERVICE_INSTANCES.get(mod_name)
        if service:
            snapshot = service.domain_snapshot()
            graph["nodes"].append({
                "module": mod_name,
                "entity": snapshot.get("entity", ""),
                "count": snapshot.get("count", 0),
                "abnormal": snapshot.get("abnormal", 0),
                "by_status": snapshot.get("by_status", {}),
            })

    # 边：业务单据流转
    graph["edges"] = [
        {"from": "building_survey", "to": "restoration_plan", "label": "勘查输入方案"},
        {"from": "restoration_plan", "to": "material_verify", "label": "方案工艺约束核验"},
        {"from": "restoration_plan", "to": "expert_review", "label": "专家评审方案"},
        {"from": "expert_review", "to": "restoration_plan", "label": "汇总意见通过方案"},
        {"from": "restoration_plan", "to": "craftsman_assign", "label": "方案确定工匠"},
        {"from": "safety_briefing", "to": "work_sequence", "label": "安全交底==发布前提"},
        {"from": "safety_briefing", "to": "construction_schedule", "label": "安全交底==排期前提"},
        {"from": "craftsman_assign", "to": "work_sequence", "label": "工匠到岗==施工前提"},
        {"from": "work_sequence", "to": "construction_schedule", "label": "工序编排驱动排期"},
        {"from": "work_sequence", "to": "quality_inspect", "label": "工序完成==质量检查"},
        {"from": "quality_inspect", "to": "acceptance_record", "label": "检查通过触发验收"},
        {"from": "acceptance_record", "to": "restoration_log", "label": "验收签署归档日志"},
        {"from": "acceptance_record", "to": "work_sequence", "label": "验收签署完成工序"},
        {"from": "acceptance_record", "to": "archive_mgmt", "label": "验收签署触发归档"},
        {"from": "change_order", "to": "construction_schedule", "label": "变更签认影响排期"},
        {"from": "change_order", "to": "funding_use", "label": "变更费用追加支出"},
        {"from": "construction_schedule", "to": "completion_summary", "label": "全部排期完成==竣工"},
        {"from": "material_verify", "to": "work_sequence", "label": "材料签认解锁工序"},
        {"from": "restoration_log", "to": "work_sequence", "label": "全部日志归档==完成"},

    ]
    return graph
