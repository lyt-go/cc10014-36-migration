"""工匠分配领域服务：工匠到岗后影响工序可执行状态，与安全交底联动。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CraftsmanAssignService(BaseModuleService):
    MODULE = "craftsman_assign"
    ENTITY = "工匠分配单"
    OPERATION_TERM = "执行分配"
    LIST_FIELDS = ["工匠姓名", "技艺类别", "资质等级", "分配工序", "到岗日期", "分配人", "班组编号", "备注说明"]
    REQUIRED_FIELDS = ["工匠姓名", "技艺类别", "资质等级"]
    STATUS_ORDER = ["待分配", "已分配", "已到岗", "已调离"]
    ACTION_RULES = {"执行分配": "已分配", "确认到岗": "已到岗", "调离工匠": "已调离"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "确认到岗":
            # 确认到岗前检查：分配工序对应的安全交底是否已签认
            process = entry.get("分配工序", "")
            safety_records = [
                row for row in store.rows("safety_briefing")
                if row.get("交底工序", "") == process
                and row.get("status") == "已签认"
            ]
            if not safety_records:
                return False, f"工序「{process}」的安全交底尚未签认，无法确认工匠到岗"
            # 同时确认该工序在排期中的状态
            schedules = [
                row for row in store.rows("construction_schedule")
                if row.get("计划工序", "") == process
            ]
            if schedules and all(s.get("status") in ("已发布", "已延期") for s in schedules):
                return False, f"与工序「{process}」关联的排期均已发布或延期，请先调整排期"
        if action == "调离工匠":
            # 调离时检查是否有施工中的关联工序
            process = entry.get("分配工序", "")
            active_work = [
                row for row in store.rows("work_sequence")
                if row.get("工序名称", "") == process and row.get("status") == "施工中"
            ]
            if active_work:
                return False, f"工序「{process}」正在施工中，无法调离工匠"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "确认到岗":
            process = entry.get("分配工序", "")
            # 到岗后更新关联工序的工匠状态
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("所需工匠", "") == process or ws_row.get("工序名称", "") == process:
                    if ws_row.get("status") == "待排期":
                        ws_row["工匠已到位"] = True
                    effects.append(dict(ws_row))
        elif action == "调离工匠":
            process = entry.get("分配工序", "")
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("所需工匠", "") == process:
                    ws_row["工匠已到位"] = False
                    ws_row["abnormal"] = True
                    effects.append(dict(ws_row))
        return effects

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["onboarded"] = [
            {"工匠姓名": r.get("工匠姓名"), "分配工序": r.get("分配工序"), "班组": r.get("班组编号")}
            for r in store.rows(self.MODULE) if r.get("status") == "已到岗"
        ]
        return base