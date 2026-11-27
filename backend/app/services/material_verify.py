"""材料核验领域服务：核验材料时校验已通过方案中的工艺要求，签认后联动工序可用性。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class MaterialVerifyService(BaseModuleService):
    MODULE = "material_verify"
    ENTITY = "材料核验单"
    OPERATION_TERM = "开始核验"
    LIST_FIELDS = ["材料名称", "规格型号", "供应商", "送检批次", "检测报告", "核验人", "核验结果", "备注说明"]
    REQUIRED_FIELDS = ["材料名称", "规格型号", "供应商"]
    STATUS_ORDER = ["待核验", "核验中", "已签认", "已退回"]
    ACTION_RULES = {"开始核验": "核验中", "签认材料": "已签认", "退回材料": "已退回"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "签认材料":
            # 校验该材料是否在任一已通过方案的工艺要求中出现
            approved_plans = [
                row for row in store.rows("restoration_plan") if row.get("status") == "已通过"
            ]
            if approved_plans:
                material_name = entry.get("材料名称", "")
                spec = entry.get("规格型号", "")
                matched = False
                mismatch_reasons = []
                for plan in approved_plans:
                    craft = plan.get("修缮工艺", "")
                    if material_name in craft or (spec and spec in plan.get("材料清单", "")):
                        matched = True
                        break
                    mismatch_reasons.append(
                        f"方案{plan.get('方案编号','')}工艺要求: {craft}"
                    )
                if not matched:
                    details = "; ".join(mismatch_reasons[:3])
                    return False, f"材料「{material_name}」未在任何已通过修缮方案的工艺中出现。{details}"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "签认材料":
            # 签认后，检查是否可以解锁需要此材料的工序
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("status") == "待排期":
                    ws_row["需要材料已验证"] = True
                    effects.append(dict(ws_row))
        elif action == "退回材料":
            # 退回后标记关联工序材料未验证
            material_name = entry.get("材料名称", "")
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("所需工匠", "") == material_name:
                    ws_row["abnormal"] = True
                    effects.append(dict(ws_row))
        return effects