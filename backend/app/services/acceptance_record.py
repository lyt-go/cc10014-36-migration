"""验收记录领域服务：签署验收后联动工序完成状态、修缮日志、归档和质量评定。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class AcceptanceRecordService(BaseModuleService):
    MODULE = "acceptance_record"
    ENTITY = "验收报告"
    OPERATION_TERM = "开始验收"
    LIST_FIELDS = ["验收工序", "验收标准", "验收结论", "遗留问题", "整改期限", "验收人", "验收日期", "备注说明"]
    REQUIRED_FIELDS = ["验收工序", "验收标准", "验收结论"]
    STATUS_ORDER = ["待验收", "验收中", "已签署", "待整改"]
    ACTION_RULES = {"开始验收": "验收中", "签署验收": "已签署", "退回整改": "待整改"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        process = entry.get("验收工序", "")
        if action == "签署验收":
            # 1. 必须有对应的质量检查已通过
            quality_records = store.query_by_field("quality_inspect", "检查工序", process)
            if not quality_records:
                return False, f"工序「{process}」缺少质量检查记录"
            if not any(r.get("status") == "已通过" for r in quality_records):
                return False, "关联质量检查未通过，无法签署验收"
            # 2. 关联排期中工期是否合理
            schedules = store.query_by_field("construction_schedule", "计划工序", process)
            for s in schedules:
                if s.get("status") == "已延期":
                    return False, "关联排期已延期，需先获取变更签证再验收"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "签署验收":
            process = entry.get("验收工序", "")
            # 1. 联动工序完成状态
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process:
                    ws_row["status"] = "已完成"
                    ws_row["pending"] = False
                    effects.append(dict(ws_row))

            # 2. 联动修缮日志归档
            for log in store.rows("restoration_log"):
                if log.get("对应工序", "") == process:
                    log["status"] = "已归档"
                    log["pending"] = False
                    effects.append(dict(log))

            # 3. 联动施工排期里程碑更新
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process:
                    sched["里程碑节点"] = f"{process}-验收已签署"
                    effects.append(dict(sched))

            # 4. 同步档案入库清单：按「来源验收记录」精确关联，不按档案标题匹配，
            #    因此同名同日的两份验收报告不会互相串档。
            from app.services.archive_intake import intake_service

            credential = intake_service.record_sign(
                entry,
                actor=entry.get("验收人", ""),
                remark=f"验收签署#{entry_id}同步档案入库清单",
            )
            archive = store.find("archive_mgmt", int(credential.get("档案编号", 0)))
            if archive is not None:
                effects.append(dict(archive))
            effects.append(dict(credential))

            # 5. 更新竣工总结质量评定
            for summary in store.rows("completion_summary"):
                if summary.get("status") in ("编制中", "已提交"):
                    if summary.get("质量评定", "") == "":
                        summary["质量评定"] = f"工序{process}已验收"
                    effects.append(dict(summary))

        elif action == "退回整改":
            process = entry.get("验收工序", "")
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process:
                    ws_row["abnormal"] = True
                    effects.append(dict(ws_row))
        return effects