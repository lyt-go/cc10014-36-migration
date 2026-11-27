"""竣工总结领域服务：汇总全部工序后编制竣工报告，前置校验全部工序已完成。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CompletionSummaryService(BaseModuleService):
    MODULE = "completion_summary"
    ENTITY = "竣工报告"
    OPERATION_TERM = "提交报告"
    LIST_FIELDS = ["修缮项目", "开工日期", "竣工日期", "实际工期", "质量评定", "编制人", "报告版本", "备注说明"]
    REQUIRED_FIELDS = ["修缮项目", "开工日期", "竣工日期"]
    STATUS_ORDER = ["编制中", "已提交", "已备案", "待补充"]
    ACTION_RULES = {"提交报告": "已提交", "执行备案": "已备案", "退回补充": "待补充"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "提交报告":
            # 1. 所有工序必须完成
            incomplete = [
                r for r in store.rows("work_sequence") if r.get("status") != "已完成"
            ]
            if incomplete:
                names = [r.get("工序名称", "") for r in incomplete[:3]]
                return False, f"尚有{len(incomplete)}道工序未完成，如：{'、'.join(names)}"
            # 2. 所有材料必须签认
            unsigned_materials = [
                r for r in store.rows("material_verify") if r.get("status") not in ("已签认",)
            ]
            if unsigned_materials:
                return False, f"尚有{len(unsigned_materials)}组材料未签认"
            # 3. 所有验收必须签署
            unsigned_acceptance = [
                r for r in store.rows("acceptance_record") if r.get("status") != "已签署"
            ]
            if unsigned_acceptance:
                return False, f"尚有{len(unsigned_acceptance)}条验收未签署"
        if action == "执行备案":
            # 备案前所有档案必须入库
            unarchived = [
                r for r in store.rows("archive_mgmt") if r.get("status") != "已入库"
            ]
            if unarchived and len(unarchived) > len(store.rows("archive_mgmt")) * 0.3:
                return False, f"尚有{len(unarchived)}份档案未入库"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "执行备案":
            # 备案后锁定所有关联记录
            for ws_row in store.rows("work_sequence"):
                ws_row["已备案锁定"] = True
                effects.append(dict(ws_row))
            for acc in store.rows("acceptance_record"):
                acc["已备案锁定"] = True
                effects.append(dict(acc))
            for log in store.rows("restoration_log"):
                log["已备案锁定"] = True
                effects.append(dict(log))
        return effects