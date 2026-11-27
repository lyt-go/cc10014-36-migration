"""档案归档领域服务：整理工序完成后联动归档，入库后锁定关联记录。

入库只通过入库凭据指向的「来源验收记录」联动，不再按档案名称包含的工序
串档；档案名称、档案类别、形成日期保持原值。存量档案的来源由档案员在
入库清单上确认（见 archive_intake）。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.archive_intake import PENDING, intake_service, _now
from app.store import store


def copy_credential(credential: dict[str, Any] | None) -> dict[str, Any] | None:
    return dict(credential) if credential is not None else None


class ArchiveMgmtService(BaseModuleService):
    MODULE = "archive_mgmt"
    ENTITY = "修缮档案"
    OPERATION_TERM = "整理归档"
    LIST_FIELDS = ["档案名称", "档案类别", "形成日期", "保管期限", "密级等级", "归档人", "归档状态", "备注说明"]
    REQUIRED_FIELDS = ["档案名称", "档案类别", "形成日期"]
    STATUS_ORDER = ["待整理", "已整理", "已入库", "已借阅"]
    ACTION_RULES = {"整理归档": "已整理", "归档入库": "已入库", "借阅档案": "已借阅"}
    NEGATIVE_ACTIONS = []
    # 入库清单维护动作：确认存量来源 / 补一份入库凭据。
    EXTRA_ACTIONS = ["确认入库来源", "补录入库凭据"]

    @staticmethod
    def _linked_acceptance(entry: dict[str, Any]) -> dict[str, Any] | None:
        """按凭据的来源验收记录精确取验收，找不到再退回档案行上的镜像字段。"""
        acceptance = intake_service.acceptance_for_archive(int(entry.get("id", 0)))
        if acceptance is not None:
            return acceptance
        source_id = entry.get("来源验收记录")
        if isinstance(source_id, int):
            return store.find("acceptance_record", source_id)
        return None

    @staticmethod
    def _has_credential(entry: dict[str, Any]) -> bool:
        return intake_service.by_archive(
            int(entry.get("id", 0)), include_revoked=True
        ) is not None

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "归档入库":
            acceptance = self._linked_acceptance(entry)
            if acceptance is not None:
                # 有明确来源时，只校验这一条验收对应工序。
                process = acceptance.get("验收工序", "")
                for ws_row in store.rows("work_sequence"):
                    if ws_row.get("工序名称", "") == process and ws_row.get("status") != "已完成":
                        return False, f"档案关联工序「{process}」尚未完成"
                return True, ""
            if self._has_credential(entry):
                # 凭据存在但来源待确认：不按同名档案猜来源，先让档案员确认。
                return False, "该档案的入库来源尚未确认，请先确认入库来源再入库"
            # 没有关联验收时保留旧口径，按档案名称包含的工序判断。
            arch_name = entry.get("档案名称", "")
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") in arch_name:
                    if ws_row.get("status") != "已完成":
                        return False, f"档案关联工序「{ws_row.get('工序名称')}」尚未完成"
        if action == "借阅档案":
            if entry.get("密级等级", "") in ("机密", "绝密"):
                return False, "高密级档案不支持借阅"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "归档入库":
            credential = intake_service.by_archive(entry_id)
            acceptance = self._linked_acceptance(entry)
            if acceptance is not None:
                # 只把凭据指向的这条验收标记为已归档，同名档案不跟着变。
                if acceptance.get("status") == "已签署":
                    acceptance["已归档"] = True
                    effects.append(dict(acceptance))
                if credential is not None:
                    credential["入库状态"] = "已入库"
                    credential["入库时间"] = _now()
                    intake_service._record_event(
                        credential=credential,
                        action="归档入库",
                        source_id=int(acceptance.get("id", 0)),
                        linked_status=credential.get("关联状态", PENDING),
                        remark=f"档案#{entry_id}入库，仅联动来源验收#{acceptance.get('id')}",
                    )
                    effects.append(dict(credential))
                process = acceptance.get("验收工序", "")
                for log in store.rows("restoration_log"):
                    if log.get("对应工序", "") == process and log.get("status") == "已审核":
                        log["status"] = "已归档"
                        log["pending"] = False
                        effects.append(dict(log))
                return effects
            if self._has_credential(entry):
                # 来源待确认的档案不做名称猜配，避免同名档案互相污染。
                return effects
            # 无来源凭据：保持旧的名称匹配行为，兼容历史数据。
            arch_name = entry.get("档案名称", "")
            for acc in store.rows("acceptance_record"):
                if acc.get("验收工序", "") in arch_name and acc.get("status") == "已签署":
                    acc["已归档"] = True
                    effects.append(dict(acc))
            for log in store.rows("restoration_log"):
                if log.get("对应工序", "") in arch_name and log.get("status") == "已审核":
                    log["status"] = "已归档"
                    log["pending"] = False
                    effects.append(dict(log))
        return effects

    def handle_custom_action(
        self,
        entry_id: int,
        action: str,
        entry: dict[str, Any],
        values: dict[str, Any],
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str] | None:
        if action == "确认入库来源":
            status = entry.get("status")
            if status == "已入库":
                return None, "档案已入库，存量来源确认请在入库清单留痕后再调整"
            if not store.acquire_resource(f"archive_mgmt:{entry_id}"):
                return None, "档案正在被其他操作处理，请稍后重试"
            try:
                credential, message = intake_service.confirm(entry_id, values, remark)
            finally:
                store.release_resource(f"archive_mgmt:{entry_id}")
            if credential is None:
                return None, message
            # 来源同步到档案行（只补来源/版本，不改名称、类别、形成日期）
            intake_service._mirror_to_archive(entry_id, credential)
            entry["入库凭据"] = copy_credential(credential)
            return entry, message

        if action == "补录入库凭据":
            source_id = intake_service._extract_source(values)
            acceptance = (
                store.find("acceptance_record", source_id)
                if source_id is not None else None
            )
            if acceptance is None:
                return None, "补录凭据需要有效的来源验收记录编号"
            existing = intake_service.by_archive(entry_id)
            if existing is not None:
                return {**entry, "入库凭据": copy_credential(existing)}, (
                    f"档案#{entry_id}已有入库凭据，无需补录"
                )
            if not store.acquire_resource(f"archive_mgmt:{entry_id}"):
                return None, "档案正在被其他操作处理，请稍后重试"
            try:
                if existing is not None:
                    intake_service.rows().remove(existing)
                credential = intake_service._new_credential(entry)
                credential["追溯线索"] = "档案员补录"
                intake_service._link_credential(
                    credential,
                    acceptance,
                    mode="补录",
                    action="补录入库凭据",
                    remark=remark or f"档案#{entry_id}补录来源验收#{acceptance.get('id')}",
                )
            finally:
                store.release_resource(f"archive_mgmt:{entry_id}")
            return {**entry, "入库凭据": copy_credential(credential)}, (
                f"已为档案#{entry_id}补录入库凭据，来源验收#{acceptance.get('id')}"
            )
        return None
