"""修缮方案领域服务：基于勘查记录编制方案，已通过方案作为材料核验的工艺基准。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RestorationPlanService(BaseModuleService):
    MODULE = "restoration_plan"
    ENTITY = "修缮方案"
    OPERATION_TERM = "提交审核"
    LIST_FIELDS = ["方案编号", "对应勘查", "修缮工艺", "材料清单", "工期预估", "编制人", "方案版本", "备注说明"]
    REQUIRED_FIELDS = ["方案编号", "对应勘查", "修缮工艺"]
    STATUS_ORDER = ["编制中", "待审核", "已通过", "已驳回"]
    ACTION_RULES = {"提交审核": "待审核", "审核通过": "已通过", "驳回方案": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回方案"]

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "提交审核":
            # 关联勘查必须是"已提交"或"已复核"
            survey_id = entry.get("对应勘查", "")
            if survey_id:
                survey = store.find("building_survey", int(survey_id)) if survey_id.isdigit() else None
                if survey and survey.get("status") not in ("已提交", "已复核"):
                    return False, f"关联勘查{survey_id}状态为「{survey.get('status', '')}」，需先提交勘查记录"
        if action == "审核通过":
            # 如果有专家评审且未汇总，不允许通过
            reviews = store.query_by_field("expert_review", "评审方案", str(entry_id))
            if reviews and any(r.get("status") not in ("已汇总", "已通过") for r in reviews):
                return False, "存在未完成的专家评审，无法审核通过"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "审核通过":
            # 方案通过后，自动将关联专家评审汇总（如未汇总）
            reviews = store.query_by_field("expert_review", "评审方案", str(entry_id))
            for review in reviews:
                if review.get("status") not in ("已汇总",):
                    review["status"] = "已汇总"
                    review["pending"] = False
                    effects.append(dict(review))
        elif action == "驳回方案":
            # 驳回后标记关联专家评审可复议
            reviews = store.query_by_field("expert_review", "评审方案", str(entry_id))
            for review in reviews:
                if review.get("status") in ("评审中", "已汇总"):
                    review["status"] = "需复议"
                    review["pending"] = True
                    effects.append(dict(review))
        return effects

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["approved_plans"] = [
            {"id": r.get("id"), "工艺要求": r.get("方案编号"), "材料清单": r.get("材料清单")}
            for r in store.rows(self.MODULE) if r.get("status") == "已通过"
        ]
        return base