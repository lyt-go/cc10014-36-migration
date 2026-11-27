"""历史图纸领域服务：与构件登记、档案归档联动，数字化后自动关联构件信息。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class HistoricalDrawingService(BaseModuleService):
    MODULE = "historical_drawing"
    ENTITY = "历史图纸"
    OPERATION_TERM = "数字化处理"
    LIST_FIELDS = ["图纸编号", "绘制年代", "绘制内容", "比例尺", "保存状况", "数字化状态", "归档位置", "备注说明"]
    REQUIRED_FIELDS = ["图纸编号", "绘制年代", "绘制内容"]
    STATUS_ORDER = ["已登记", "待数字化", "已归档", "已借出"]
    ACTION_RULES = {"数字化处理": "待数字化", "执行归档": "已归档", "借出图纸": "已借出"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "数字化处理":
            if entry.get("保存状况", "") in ("严重破损", "无法展开"):
                return False, "图纸保存状况不允许数字化，需先修复"
        if action == "执行归档":
            # 如果图纸关联了构件，确认关联构件也已登记
            content = entry.get("绘制内容", "")
            for comp in store.rows("component_registry"):
                if comp.get("构件名称", "") in content and comp.get("status") == "待登记":
                    comp_name = comp.get("构件名称")
                    return False, f"图纸关联构件「{comp_name}」尚未登记确认"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "执行归档":
            # 归档后检查是否有对应档案记录，若有则标记
            drawing_id = entry.get("图纸编号", "")
            for arch in store.rows("archive_mgmt"):
                if arch.get("档案名称", "").find(drawing_id) >= 0:
                    arch["status"] = "已整理"
                    arch["pending"] = False
                    effects.append(dict(arch))
        return effects