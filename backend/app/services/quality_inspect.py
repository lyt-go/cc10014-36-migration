"""质量检查领域服务：检查后自动关联验收记录创建、联动修缮日志工序完成状态。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class QualityInspectService(BaseModuleService):
    MODULE = "quality_inspect"
    ENTITY = "检查记录"
    OPERATION_TERM = "开始检查"
    LIST_FIELDS = ["检查工序", "检查项目", "技术标准", "检查结果", "不合格项", "检查人", "检查日期", "备注说明"]
    REQUIRED_FIELDS = ["检查工序", "检查项目", "技术标准"]
    STATUS_ORDER = ["待检查", "检查中", "已通过", "需整改"]
    ACTION_RULES = {"开始检查": "检查中", "出具报告": "已通过", "退回整改": "需整改"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "出具报告":
            # 出具报告前检查关联工序是否完成
            process = entry.get("检查工序", "")
            ws_rows = store.query_by_field("work_sequence", "工序名称", process)
            if ws_rows and any(r.get("status") != "已完成" for r in ws_rows):
                return False, f"关联工序「{process}」尚未完成"
        if action == "开始检查":
            process = entry.get("检查工序", "")
            # 必须有对应的已排期工序
            schedules = store.query_by_field("construction_schedule", "计划工序", process)
            if schedules and not any(s.get("status") in ("执行中", "已发布") for s in schedules):
                return False, f"关联排期「{process}」尚未进入可检查状态"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "出具报告":
            process = entry.get("检查工序", "")
            # 1. 自动创建验收记录
            acc_rows = store.rows("acceptance_record")
            new_acc = {
                "id": max((int(r.get("id", 0)) for r in acc_rows), default=0) + 1,
                "验收工序": process,
                "验收标准": entry.get("技术标准", ""),
                "验收结论": "质量检查已通过",
                "遗留问题": entry.get("不合格项", "无"),
                "整改期限": "",
                "验收人": "系统自动",
                "验收日期": entry.get("检查日期", ""),
                "备注说明": f"由质量检查#{entry_id}触发创建",
                "status": "待验收",
                "pending": True,
                "abnormal": False,
            }
            acc_rows.append(new_acc)
            effects.append(new_acc)

            # 2. 更新修缮日志状态
            for log in store.rows("restoration_log"):
                if log.get("对应工序", "") == process and log.get("status") in ("已填写", "已审核"):
                    log["status"] = "已审核"
                    log["pending"] = False
                    effects.append(dict(log))
        return effects