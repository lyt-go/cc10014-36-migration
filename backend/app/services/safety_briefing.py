"""安全交底领域服务：签认交底是开工和发布工序的前置条件。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SafetyBriefingService(BaseModuleService):
    MODULE = "safety_briefing"
    ENTITY = "交底记录"
    OPERATION_TERM = "执行交底"
    LIST_FIELDS = ["交底工序", "危险源", "防护措施", "应急方案", "交底人", "接受人", "交底日期", "备注说明"]
    REQUIRED_FIELDS = ["交底工序", "危险源", "防护措施"]
    STATUS_ORDER = ["待交底", "已交底", "已签认", "已过期"]
    ACTION_RULES = {"执行交底": "已交底", "签认确认": "已签认", "重新交底": "已交底"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        process = entry.get("交底工序", "")
        if action == "签认确认":
            # 签认前检查工序是否为非完成状态
            ws_rows = store.query_by_field("work_sequence", "工序名称", process)
            if ws_rows and any(r.get("status") == "已完成" for r in ws_rows):
                return False, f"工序「{process}」已完成，无需签认交底"
        if action == "重新交底":
            # 重新交底前复位关联排期为编制中
            schedules = store.query_by_field("construction_schedule", "计划工序", process)
            for s in schedules:
                if s.get("status") in ("执行中", "已发布"):
                    return False, "排期已启动或发布中，需先退回排期再重新交底"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "签认确认":
            process = entry.get("交底工序", "")
            # 签认后解锁关联排期可发布
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process and sched.get("status") == "编制中":
                    sched["安全交底已完成"] = True
                    effects.append(dict(sched))
            # 解锁关联工序
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process:
                    ws_row["安全交底已完成"] = True
                    effects.append(dict(ws_row))
        elif action == "重新交底":
            process = entry.get("交底工序", "")
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process:
                    sched["安全交底已完成"] = False
                    sched["abnormal"] = True
                    effects.append(dict(sched))
        return effects