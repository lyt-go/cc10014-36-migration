"""档案入库凭据与北廊同名同日验收演练。

覆盖：
- 旧式档案关联迁移为带版本的入库凭据，名称/类别/成文日期不变；
- 同名同日的两份验收档案不会按标题串档，来源不足的留待确认且原编号可查；
- 迁移可重复执行，不会重复插入同源档案；
- 档案员确认存量来源后，另一份验收的签署回执迟到也能分别查到两次关联动作；
- 回滚再恢复不把现场签署退回未办；
- 档案入库只变更凭据指向的验收来源。
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.services.acceptance_record import AcceptanceRecordService
from app.services.archive_intake import ASSOCIATED, PENDING, REVOKED, intake_service
from app.services.archive_mgmt import ArchiveMgmtService
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store._idempotent_log.clear()
    store._resource_locks.clear()
    for name in list(store._tables.keys()):
        store._tables[name] = [dict(row) for row in SEED_ROWS.get(name, [])]
    yield


acceptance_service = AcceptanceRecordService()
archive_service = ArchiveMgmtService()

ARCHIVE_A = 4  # 北廊彩画修补-验收报告（备注带 验收签署#4）
ARCHIVE_B = 5  # 北廊彩画修补-验收报告（旧式纸质移交，来源未登记）
ACC_A = 4     # 北廊彩画修补（已签署）
ACC_B = 5     # 北廊木构加固（验收中，待现场签署）


def archive(archive_id: int) -> dict:
    return store.find("archive_mgmt", archive_id)


def acceptance(acceptance_id: int) -> dict:
    return store.find("acceptance_record", acceptance_id)


def credential_of(archive_id: int) -> dict:
    return intake_service.by_archive(archive_id, include_revoked=True)


# ── 迁移 ──

def test_migration_creates_versioned_credentials_without_touching_fields():
    original = {aid: dict(archive(aid)) for aid in (ARCHIVE_A, ARCHIVE_B)}
    result = intake_service.migrate()
    assert result["ok"] is True
    assert result["scanned"] == 2
    assert result["created"] == 2

    for aid in (ARCHIVE_A, ARCHIVE_B):
        current = archive(aid)
        assert current["档案名称"] == original[aid]["档案名称"]
        assert current["档案类别"] == original[aid]["档案类别"]
        assert current["形成日期"] == original[aid]["形成日期"]
        assert current["id"] == aid  # 仍按原编号可查

    cred_a = credential_of(ARCHIVE_A)
    assert cred_a["关联状态"] == ASSOCIATED
    assert cred_a["来源验收记录"] == ACC_A
    assert cred_a["凭据版本"] >= 1
    assert cred_a["档案名称"] == original[ARCHIVE_A]["档案名称"]


def test_insufficient_source_stays_pending_and_keeps_original_number():
    intake_service.migrate()
    cred_b = credential_of(ARCHIVE_B)
    assert cred_b["关联状态"] == PENDING
    assert cred_b["来源验收记录"] is None
    # 原编号仍然可查，且没有按标题挂到另一份同名档案的验收上。
    assert archive(ARCHIVE_B)["id"] == ARCHIVE_B
    assert archive(ARCHIVE_B).get("来源验收记录") is None
    assert archive(ARCHIVE_A).get("来源验收记录") == ACC_A


def test_rerunning_migration_does_not_insert_duplicate_source_archive():
    intake_service.migrate()
    archives_before = [row["id"] for row in store.rows("archive_mgmt")]
    credentials_before = len(intake_service.rows())

    second = intake_service.migrate()
    assert second["created"] == 0
    assert second["linked"] == 0
    assert [row["id"] for row in store.rows("archive_mgmt")] == archives_before
    assert len(intake_service.rows()) == credentials_before
    assert len(intake_service.by_source(ACC_A)) == 1


# ── 确认 + 迟到签署 ──

def test_confirm_then_late_signature_traces_both_actions():
    intake_service.migrate()
    credential, message = intake_service.confirm(
        ARCHIVE_B, {"来源验收记录": ACC_B}
    )
    assert credential is not None, message
    assert credential["关联状态"] == ASSOCIATED
    assert credential["来源验收记录"] == ACC_B
    assert credential["关联方式"] == "人工确认"

    # 另一份同名验收的签署回执迟到。
    signed, msg = acceptance_service.run_action(ACC_B, "签署验收")
    assert signed is not None, msg
    assert signed["status"] == "已签署"
    assert signed["签署版本"] == 1

    events = intake_service.events_for(credential["id"])
    actions = [event["动作"] for event in events]
    assert "确认存量来源" in actions
    assert "验收签署同步" in actions
    confirm_event = next(e for e in events if e["动作"] == "确认存量来源")
    sign_event = next(e for e in events if e["动作"] == "验收签署同步")
    assert confirm_event["动作号"] != sign_event["动作号"]
    assert confirm_event["凭据版本"] < sign_event["凭据版本"]
    assert sign_event["来源验收签署版本"] == 1

    # 两次动作都建立的是同一份档案与同一条验收的唯一关联，没有额外档案。
    assert len(intake_service.by_source(ACC_B)) == 1
    assert sum(1 for row in store.rows("archive_mgmt")
               if row["档案名称"] == archive(ARCHIVE_B)["档案名称"]) == 2


def test_late_signature_for_other_named_acceptance_does_not_cross_archive():
    intake_service.migrate()
    intake_service.confirm(ARCHIVE_B, {"来源验收记录": ACC_B})

    # 签署 B 之后，A 的凭据与档案行都不应被改动。
    cred_a_before = dict(credential_of(ARCHIVE_A))
    acceptance_service.run_action(ACC_B, "签署验收")
    cred_a_after = credential_of(ARCHIVE_A)
    assert cred_a_after["来源验收记录"] == cred_a_before["来源验收记录"]
    assert cred_a_after["凭据版本"] == cred_a_before["凭据版本"]
    assert archive(ARCHIVE_A).get("来源验收记录") == ACC_A


# ── 回滚 / 恢复 ──

def test_rollback_and_restore_keep_signed_state():
    intake_service.migrate()
    intake_service.confirm(ARCHIVE_B, {"来源验收记录": ACC_B})
    acceptance_service.run_action(ACC_B, "签署验收")
    assert acceptance(ACC_B)["status"] == "已签署"

    rollback = intake_service.rollback()
    assert rollback["ok"] is True
    # 人工确认的关联保留，迁移关联被撤销。
    assert credential_of(ARCHIVE_A)["关联状态"] == REVOKED
    assert credential_of(ARCHIVE_B)["关联状态"] == ASSOCIATED
    # 现场签署状态不受回滚影响。
    assert acceptance(ACC_B)["status"] == "已签署"
    assert acceptance(ACC_B)["签署版本"] == 1

    restore = intake_service.restore()
    assert restore["ok"] is True
    assert acceptance(ACC_B)["status"] == "已签署"
    assert acceptance(ACC_B)["签署版本"] == 1  # 未被退回未办、也未重置
    # 恢复后迁移凭据重新建立；人工确认的 B 仍是确认来源。
    assert credential_of(ARCHIVE_A)["关联状态"] == ASSOCIATED
    assert credential_of(ARCHIVE_A)["来源验收记录"] == ACC_A
    assert credential_of(ARCHIVE_B)["关联方式"] == "人工确认"
    assert credential_of(ARCHIVE_B)["来源验收记录"] == ACC_B
    # 历史动作仍可查到。
    actions = [e["动作"] for e in intake_service.events_for_archive(ARCHIVE_A)]
    assert "迁移关联" in actions
    assert "回滚迁移" in actions


def test_rollback_does_not_revert_signature_or_resign_archive():
    intake_service.migrate()
    intake_service.confirm(ARCHIVE_B, {"来源验收记录": ACC_B})
    acceptance_service.run_action(ACC_B, "签署验收")
    archives_before = [row["id"] for row in store.rows("archive_mgmt")]

    intake_service.rollback()
    intake_service.restore()

    assert [row["id"] for row in store.rows("archive_mgmt")] == archives_before
    assert acceptance(ACC_B)["status"] == "已签署"
    # 恢复后再次签署同一验收（新的回执）仍只挂在这份档案上。
    assert len(intake_service.by_source(ACC_B)) == 1


# ── 入库联动只变更对应来源 ──

def test_archive_intake_only_marks_linked_acceptance():
    intake_service.migrate()
    intake_service.confirm(ARCHIVE_B, {"来源验收记录": ACC_B})

    # A 档案直接入库 → 只标记验收 A。
    updated, msg = archive_service.run_action(ARCHIVE_A, "归档入库")
    assert updated is not None, msg
    assert updated["status"] == "已入库"
    assert acceptance(ACC_A).get("已归档") is True
    assert acceptance(ACC_B).get("已归档") is None

    # 同名同日 B 档案入库不受 A 影响，B 的验收仍未签、未归档。
    assert acceptance(ACC_B)["status"] == "验收中"
    assert acceptance(ACC_B).get("已归档") is None
    assert archive(ARCHIVE_B)["status"] != "已入库"


def test_archive_verify_uses_linked_source_process():
    intake_service.migrate()
    intake_service.confirm(ARCHIVE_B, {"来源验收记录": ACC_B})
    # 变更 B 档案的名称，让它不再包含任何工序名，仍能凭来源验收校验。
    archive(ARCHIVE_B)["档案名称"] = "北廊-卷宗（待编号）"

    passed, reason = archive_service.cross_module_verify(
        0, "归档入库", archive(ARCHIVE_B)
    )
    assert passed is True, reason


def test_pending_credential_blocks_intake_instead_of_title_matching():
    """来源待确认的档案不能被同名档案的工序猜配，必须先确认来源。"""
    intake_service.migrate()
    # 档案 A 与 B 名称完全相同且工序都已完成，仍不应允许 B 在未确认来源时入库。
    assert archive(ARCHIVE_A)["档案名称"] == archive(ARCHIVE_B)["档案名称"]
    updated, msg = archive_service.run_action(ARCHIVE_B, "归档入库")
    assert updated is None
    assert "来源尚未确认" in msg
    assert acceptance(ACC_B).get("已归档") is None
    assert acceptance(ACC_A).get("已归档") is None


def test_sign_created_archive_survives_rollback_and_migration_reuses_it():
    """先签署建档再回滚/迁移，不会为同一条验收再插一份档案。"""
    for row in store.rows("quality_inspect"):
        row["检查工序"] = "验收记录样例1"
        row["status"] = "已通过"
    signed, msg = acceptance_service.run_action(1, "签署验收")
    assert signed is not None, msg
    archive_count = len(store.rows("archive_mgmt"))
    created = store.rows("archive_mgmt")[-1]
    assert created["备注说明"] == "验收签署#1自动触发"

    rollback = intake_service.rollback()
    assert rollback["ok"] is True
    # 签署产生的凭据不是迁移凭据，回滚不动它。
    surviving = intake_service.by_archive(created["id"], include_revoked=True)
    assert surviving is not None and surviving["来源验收记录"] == 1

    intake_service.migrate()
    assert len(store.rows("archive_mgmt")) == archive_count
    sources = [row["档案编号"] for row in intake_service.by_source(1)]
    assert sources == [created["id"]]


def test_late_signature_after_restore_adds_version_not_archive():
    intake_service.confirm(ARCHIVE_B, {"来源验收记录": ACC_B})
    acceptance_service.run_action(ACC_B, "签署验收")
    intake_service.rollback()
    intake_service.restore()
    archives = len(store.rows("archive_mgmt"))
    # 恢复后再来一次签署回执：只追加版本/动作，不再新插同源档案。
    credential = intake_service.record_sign(acceptance(ACC_B), remark="补签回执")
    assert len(store.rows("archive_mgmt")) == archives
    assert credential["档案编号"] == ARCHIVE_B
    sign_events = [
        e for e in intake_service.events_for(credential["id"])
        if e["动作"] == "验收签署同步"
    ]
    assert len(sign_events) == 2
    assert len({e["动作号"] for e in sign_events}) == 2
