"""施工排期领域服务：排期制定与变更签证、工序编排联动，变更签认后自动调整。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ConstructionScheduleService(BaseModuleService):
    MODULE = "construction_schedule"
    ENTITY = "排期计划"
    OPERATION_TERM = "发布排期"
    LIST_FIELDS = ["计划工序", "开始日期", "结束日期", "资源配置", "里程碑节点", "编制人", "计划版本", "备注说明"]
    REQUIRED_FIELDS = ["计划工序", "开始日期", "结束日期"]
    STATUS_ORDER = ["编制中", "已发布", "执行中", "已延期"]
    ACTION_RULES = {"发布排期": "已发布", "启动执行": "执行中", "申请延期": "已延期"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        process = entry.get("计划工序", "")
        if action == "发布排期":
            # 对应工序必须已发布
            ws_rows = store.query_by_field("work_sequence", "工序名称", process)
            if ws_rows and all(r.get("status") == "待排期" for r in ws_rows):
                return False, f"对应工序「{process}」尚未发布，无法发布排期"
            # 对应安全交底必须已签认
            safety = store.query_by_field("safety_briefing", "交底工序", process)
            if not safety:
                return False, f"工序「{process}」缺少安全交底记录"
            if not any(s.get("status") == "已签认" for s in safety):
                return False, "安全交底尚未签认，不能发布排期"
        if action == "启动执行":
            # 检查是否有关联变更签证未处理
            cs = store.query_by_field("change_order", "影响工序", process)
            pending_changes = [c for c in cs if c.get("status") == "已提出"]
            if pending_changes:
                return False, f"工序「{process}」有{len(pending_changes)}条待签认变更"
        if action == "申请延期":
            # 检查是否有变更签证支撑延期
            cs = store.query_by_field("change_order", "影响工序", process)
            if not any(c.get("status") == "已签认" for c in cs):
                return False, "延期需要已签认的变更签证支撑"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "发布排期":
            process = entry.get("计划工序", "")
            # 排期发布后更新关联工序
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process and ws_row.get("status") == "已发布":
                    ws_row["排期已就绪"] = True
                    effects.append(dict(ws_row))
        elif action == "已延期":
            process = entry.get("计划工序", "")
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process:
                    ws_row["abnormal"] = True
                    effects.append(dict(ws_row))
        return effects

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # 返回关键路径
        all_rows = store.rows(self.MODULE)
        base["timeline"] = [
            {"工序": r.get("计划工序"), "开始": r.get("开始日期"), "结束": r.get("结束日期"),
             "里程碑": r.get("里程碑节点"), "状态": r.get("status")}
            for r in all_rows
        ]
        return base