"""档案入库清单 API 契约：迁移/回滚/恢复、原编号查询与确认来源动作。

环境里没有 httpx，直接调用路由处理函数验证返回结构，保持依赖不变。
"""
from __future__ import annotations

import pytest

from app.schemas import ActionPayload
from app.seed import SEED_ROWS
from app.services.archive_mgmt import ArchiveMgmtService
from app.store import store
from app.routers import resource

ARCHIVE_A = 4
ARCHIVE_B = 5
ACC_B = 5


@pytest.fixture(autouse=True)
def reset_store():
    store._idempotent_log.clear()
    store._resource_locks.clear()
    for name in list(store._tables.keys()):
        store._tables[name] = [dict(row) for row in SEED_ROWS.get(name, [])]
    yield


def test_list_endpoint_reports_migration_state_and_lazily_migrates():
    payload = resource.list_intake(
        keyword=None, status=None, source_id=None, archive_id=None, page=1, size=20)
    assert payload["total"] == 2
    assert payload["migration"]["状态"] == "已迁移"
    names = {item["档案编号"]: item["档案名称"] for item in payload["items"]}
    assert names[ARCHIVE_A] == names[ARCHIVE_B] == "北廊彩画修补-验收报告"
    statuses = {item["档案编号"]: item["关联状态"] for item in payload["items"]}
    assert statuses[ARCHIVE_A] == "已关联"
    assert statuses[ARCHIVE_B] == "待确认"


def test_migrate_rollback_restore_endpoints_are_composable():
    assert resource.intake_migrate()["ok"] is True
    rollback = resource.intake_rollback()
    assert rollback["ok"] is True and rollback["state"] == "已回滚"
    restore = resource.intake_restore()
    assert restore["ok"] is True
    assert resource.intake_migration_state()["状态"] == "已迁移"


def test_old_archive_number_still_addressable_with_credential():
    resource.intake_migrate()
    credential = resource.get_intake_by_archive(ARCHIVE_A)
    assert credential["档案编号"] == ARCHIVE_A
    # 凭据带全部关联动作轨迹。
    assert credential["events"][0]["动作"] == "迁移关联"
    # 原档案接口仍按原编号返回。
    archive = resource.get_entry("archive_mgmt", ARCHIVE_A)
    assert archive["id"] == ARCHIVE_A
    assert archive["档案名称"] == "北廊彩画修补-验收报告"


def test_confirm_source_action_through_archive_route():
    resource.intake_migrate()
    service = ArchiveMgmtService()
    entry, message = service.run_action(
        ARCHIVE_B, "确认入库来源", values={"来源验收记录": ACC_B}
    )
    assert entry is not None, message
    assert entry["来源验收记录"] == ACC_B
    assert entry["凭据版本"] >= 2
    # 已入库档案不允许再确认来源
    archive = store.find("archive_mgmt", ARCHIVE_B)
    archive["status"] = "已入库"
    blocked, blocked_msg = service.run_action(
        ARCHIVE_B, "确认入库来源", values={"来源验收记录": ACC_B}
    )
    assert blocked is None
    assert "已入库" in blocked_msg


def test_legacy_action_payload_without_values_still_works():
    service = ArchiveMgmtService()
    entry, message = service.run_action(ARCHIVE_A, "整理归档")
    assert entry is not None, message
    assert entry["status"] == "已整理"


def test_intake_action_via_router_schema():
    resource.intake_migrate()
    service = ArchiveMgmtService()
    payload = ActionPayload(action="确认入库来源", values={"来源验收记录": ACC_B})
    entry, message = service.run_action(
        ARCHIVE_B, payload.action, values=payload.values, remark=payload.remark
    )
    assert entry is not None, message
