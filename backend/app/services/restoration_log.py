"""修缮日志领域服务：每日记录施工内容和材料用量，与工序编排、质量检查联动。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RestorationLogService(BaseModuleService):
    MODULE = "restoration_log"
    ENTITY = "施工日志"
    OPERATION_TERM = "提交日志"
    LIST_FIELDS = ["施工日期", "对应工序", "施工内容", "用工数量", "材料用量", "存在问题", "记录人", "备注说明"]
    REQUIRED_FIELDS = ["施工日期", "对应工序", "施工内容"]
    STATUS_ORDER = ["待填写", "已填写", "已审核", "已归档"]
    ACTION_RULES = {"提交日志": "已填写", "审核通过": "已审核", "归档日志": "已归档"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "提交日志":
            process = entry.get("对应工序", "")
            # 关联工序必须已发布或施工中
            ws_rows = store.query_by_field("work_sequence", "工序名称", process)
            if ws_rows and all(r.get("status") not in ("已发布", "施工中", "已完成") for r in ws_rows):
                return False, f"关联工序「{process}」状态不允许提交日志"
        if action == "审核通过":
            process = entry.get("对应工序", "")
            # 如果日志提到材料用量，对应材料核验单需已签认
            material_used = entry.get("材料用量", "")
            if material_used and material_used not in ("", "无", "0"):
                verified = [
                    r for r in store.rows("material_verify")
                    if r.get("材料名称", "") in material_used and r.get("status") == "已签认"
                ]
                if material_used and not verified:
                    return False, f"日志中材料「{material_used}」未经过核验签认"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "审核通过":
            process = entry.get("对应工序", "")
            # 日志审核通过后联动排期执行中
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process and sched.get("status") == "已发布":
                    sched["status"] = "执行中"
                    effects.append(dict(sched))
        if action == "归档日志":
            # 检查是否可以联动工序完成
            process = entry.get("对应工序", "")
            logs_for_process = [
                r for r in store.rows(self.MODULE)
                if r.get("对应工序", "") == process
            ]
            if all(l.get("status") == "已归档" for l in logs_for_process):
                for ws_row in store.rows("work_sequence"):
                    if ws_row.get("工序名称", "") == process and ws_row.get("status") == "施工中":
                        ws_row["status"] = "已完成"
                        ws_row["pending"] = False
                        effects.append(dict(ws_row))
        return effects

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # 返回按工序聚合的日志状态
        all_rows = store.rows(self.MODULE)
        by_process: dict[str, list] = {}
        for row in all_rows:
            process = row.get("对应工序", "")
            by_process.setdefault(process, []).append({
                "id": row.get("id"),
                "status": row.get("status"),
                "date": row.get("施工日期"),
            })
        base["by_process"] = by_process
        return base