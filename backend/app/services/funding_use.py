"""修缮支出领域服务：支出审批与变更签证费用联动，超出预算后联动排期异常。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FundingUseService(BaseModuleService):
    MODULE = "funding_use"
    ENTITY = "支出记录"
    OPERATION_TERM = "提交报销"
    LIST_FIELDS = ["支出项目", "支出类别", "预算金额", "实际金额", "支出日期", "经办人", "审批状态", "备注说明"]
    REQUIRED_FIELDS = ["支出项目", "支出类别", "预算金额"]
    STATUS_ORDER = ["待提交", "已提交", "已核准", "已驳回"]
    ACTION_RULES = {"提交报销": "已提交", "核准支出": "已核准", "驳回支出": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回支出"]

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "核准支出":
            actual = float(entry.get("实际金额", 0))
            budget = float(entry.get("预算金额", 0))
            # 超额检查
            if actual > budget * 1.1:
                # 检查是否有变更签证支撑超额
                cs = [
                    r for r in store.rows("change_order")
                    if r.get("status") == "已签认" and "费用增减" in str(r.keys())
                ]
                has_support = any(
                    float(c.get("费用增减", 0)) >= (actual - budget)
                    for c in cs
                )
                if not has_support:
                    return False, f"超出预算{actual - budget:.2f}元，需变更签证支撑"
        if action == "驳回支出":
            # 驳回后检查是否有已核准的支出引用了此驳回项
            pass  # 无特殊前置校验
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "核准支出":
            # 核准后更新竣工总结中的费用汇总
            actual = float(entry.get("实际金额", 0))
            for summary in store.rows("completion_summary"):
                if summary.get("status") in ("编制中", "已提交"):
                    summary["费用已核准"] = True
                    effects.append(dict(summary))
        return effects