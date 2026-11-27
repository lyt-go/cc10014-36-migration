"""构件登记领域服务：登记古建筑构件后联动修缮方案中对应修复计划。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ComponentRegistryService(BaseModuleService):
    MODULE = "component_registry"
    ENTITY = "构件档案"
    OPERATION_TERM = "确认构件"
    LIST_FIELDS = ["构件名称", "所属建筑", "材质类型", "年代判定", "保存状态", "修复历史", "登记人", "备注说明"]
    REQUIRED_FIELDS = ["构件名称", "所属建筑", "材质类型"]
    STATUS_ORDER = ["待登记", "已登记", "已鉴定", "待修复"]
    ACTION_RULES = {"确认构件": "已登记", "提交鉴定": "已鉴定", "标记修复": "待修复"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "提交鉴定":
            # 检查构件的所属建筑是否已有勘查记录
            building = entry.get("所属建筑", "")
            surveys = store.query_by_field("building_survey", "建筑名称", building)
            if not surveys:
                return False, f"建筑「{building}」尚无勘查记录，不能提交鉴定"
            if any(s.get("status") not in ("已提交", "已复核") for s in surveys):
                return False, f"建筑「{building}」勘查未完成"
        if action == "标记修复":
            # 标记修复前须有已通过的修缮方案
            plans = store.rows("restoration_plan")
            comp_name = entry.get("构件名称", "")
            matched = [
                p for p in plans
                if p.get("status") == "已通过" and comp_name in str(p.get("工艺要求", ""))
            ]
            if not matched:
                return False, f"构件「{comp_name}」缺少关联的已通过修缮方案"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "标记修复":
            # 标记后联动更新修缮方案中对应工序
            comp_name = entry.get("构件名称", "")
            for plan in store.rows("restoration_plan"):
                if comp_name in str(plan.get("工艺要求", "")):
                    plan["构件修复已登记"] = True
                    effects.append(dict(plan))
        return effects