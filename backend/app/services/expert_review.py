"""专家评审领域服务：汇总意见后联动方案状态。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ExpertReviewService(BaseModuleService):
    MODULE = "expert_review"
    ENTITY = "评审意见"
    OPERATION_TERM = "开始评审"
    LIST_FIELDS = ["评审方案", "专家姓名", "专业领域", "评审结论", "修改建议", "评审日期", "评审轮次", "备注说明"]
    REQUIRED_FIELDS = ["评审方案", "专家姓名", "专业领域"]
    STATUS_ORDER = ["待评审", "评审中", "已汇总", "需复议"]
    ACTION_RULES = {"开始评审": "评审中", "汇总意见": "已汇总", "发起复议": "需复议"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        plan_id = entry.get("评审方案", "")
        if action == "开始评审":
            # 被评审的方案必须存在
            plan = store.find("restoration_plan", int(plan_id)) if plan_id.isdigit() else None
            if not plan:
                return False, f"评审方案#{plan_id}在系统中不存在"
            if plan.get("status") not in ("待审核", "已通过"):
                return False, f"方案#{plan_id}状态「{plan.get('status')}」不支持评审"
        if action == "汇总意见":
            # 汇总时方案必须处于待审核
            plan = store.find("restoration_plan", int(plan_id)) if plan_id.isdigit() else None
            if plan and plan.get("status") not in ("待审核", "已通过"):
                return False, f"待评审方案#{plan_id}状态为{plan.get('status')}，不能汇总"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "汇总意见":
            plan_id = entry.get("评审方案", "")
            plan = store.find("restoration_plan", int(plan_id)) if plan_id.isdigit() else None
            if plan and plan.get("status") == "待审核":
                plan["status"] = "已通过"
                plan["pending"] = False
                effects.append(dict(plan))
                # 方案通过后更新关联勘查的所有方案
                survey_id = plan.get("对应勘查", "")
                if survey_id:
                    survey = store.find("building_survey", int(survey_id)) if survey_id.isdigit() else None
                    if survey:
                        survey["status"] = "已复核"
                        survey["pending"] = False
                        effects.append(dict(survey))
        elif action == "发起复议":
            plan_id = entry.get("评审方案", "")
            plan = store.find("restoration_plan", int(plan_id)) if plan_id.isdigit() else None
            if plan:
                plan["status"] = "已驳回"
                plan["pending"] = True
                plan["abnormal"] = True
                effects.append(dict(plan))
        return effects

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["pending_reviews"] = [
            {"方案": r.get("评审方案"), "专家": r.get("专家姓名"), "领域": r.get("专业领域")}
            for r in store.rows(self.MODULE) if r.get("status") in ("待评审", "评审中")
        ]
        return base