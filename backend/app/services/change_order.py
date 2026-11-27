"""变更签证领域服务：签认签证后直接更新施工排期里程碑和费用联动。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ChangeOrderService(BaseModuleService):
    MODULE = "change_order"
    ENTITY = "变更签证单"
    OPERATION_TERM = "提出变更"
    LIST_FIELDS = ["变更事项", "变更原因", "影响工序", "费用增减", "提出人", "审批人", "签认日期", "备注说明"]
    REQUIRED_FIELDS = ["变更事项", "变更原因", "影响工序"]
    STATUS_ORDER = ["待提出", "已提出", "已签认", "已驳回"]
    ACTION_RULES = {"提出变更": "已提出", "签认变更": "已签认", "驳回变更": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回变更"]

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "签认变更":
            process = entry.get("影响工序", "")
            # 1. 校验被影响的工序/排期确实存在
            schedules = store.query_by_field("construction_schedule", "计划工序", process)
            if not schedules:
                return False, f"受影响工序「{process}」在施工排期中不存在"
            # 2. 不能对已完成验收的工序签认变更
            accs = store.query_by_field("acceptance_record", "验收工序", process)
            if any(a.get("status") == "已签署" for a in accs):
                return False, f"工序「{process}」已有签署验收，不能签认变更"
            # 3. 费用增减不能为负且超过已核准总支出
            fee_change_val = entry.get("费用增减", 0)
            if fee_change_val is None:
                fee_change_val = 0
            fee_change = float(fee_change_val)
            if fee_change < 0:
                approved_total = sum(
                    float(r.get("实际金额", 0)) for r in store.rows("funding_use")
                    if r.get("status") == "已核准"
                )
                if abs(fee_change) > approved_total:
                    return False, "费用扣减超过已核准总支出"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "签认变更":
            process = entry.get("影响工序", "")
            # 1. 更新施工排期里程碑节点
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process:
                    sched["里程碑节点"] = f"变更签证#{entry_id}：{entry.get('变更事项', '')}"
                    sched["abnormal"] = True
                    effects.append(dict(sched))

            # 2. 如果费用有变化，创建修缮支出预警记录
            fee_change_val = entry.get("费用增减", 0)
            if fee_change_val is None:
                fee_change_val = 0
            fee_change = float(fee_change_val)
            if fee_change != 0:
                funding_rows = store.rows("funding_use")
                new_funding = {
                    "id": max((int(r.get("id", 0)) for r in funding_rows), default=0) + 1,
                    "支出项目": f"变更签证#{entry_id}费用调整",
                    "支出类别": "变更追加",
                    "预算金额": fee_change,
                    "实际金额": fee_change,
                    "支出日期": entry.get("签认日期", ""),
                    "经办人": entry.get("审批人", ""),
                    "审批状态": "变更费用",
                    "备注说明": f"由变更签证#{entry_id}自动触发",
                    "status": "待提交",
                    "pending": True,
                    "abnormal": False,
                }
                funding_rows.append(new_funding)
                effects.append(new_funding)

            # 3. 更新关联工序异常标记
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process:
                    ws_row["abnormal"] = True
                    effects.append(dict(ws_row))
        return effects