"""环境监测领域服务：异常报警联动安全交底和施工排期。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class EnvMonitorService(BaseModuleService):
    MODULE = "env_monitor"
    ENTITY = "监测数据"
    OPERATION_TERM = "采集数据"
    LIST_FIELDS = ["监测位置", "温度读数", "湿度读数", "光照强度", "振动频率", "记录时间", "监测设备", "备注说明"]
    REQUIRED_FIELDS = ["监测位置", "温度读数", "湿度读数"]
    STATUS_ORDER = ["待采集", "已采集", "已上传", "异常报警"]
    ACTION_RULES = {"采集数据": "已采集", "上传数据": "已上传", "标记异常": "异常报警"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "标记异常":
            location = entry.get("监测位置", "")
            # 异常标记前检查该位置是否有施工中的工序
            active_work = [
                r for r in store.rows("work_sequence")
                if r.get("施工区域", "") == location and r.get("status") == "施工中"
            ]
            if not active_work:
                return False, "该位置无施工中工序，无需标记异常报警"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        effects = []
        if action == "标记异常":
            location = entry.get("监测位置", "")
            # 报警后自动暂停关联工序
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("施工区域", "") == location and ws_row.get("status") == "施工中":
                    ws_row["abnormal"] = True
                    ws_row["环境报警"] = True
                    effects.append(dict(ws_row))
            # 关联排期标记异常
            for sched in store.rows("construction_schedule"):
                if sched.get("status") == "执行中":
                    sched["abnormal"] = True
                    effects.append(dict(sched))
            # 关联安全交底标记过期需重新交底
            for safety in store.rows("safety_briefing"):
                if safety.get("status") == "已签认":
                    safety["abnormal"] = True
                    effects.append(dict(safety))
        return effects

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        all_rows = store.rows(self.MODULE)
        base["alerts"] = [
            {"位置": r.get("监测位置"), "温度": r.get("温度读数"), "湿度": r.get("湿度读数")}
            for r in all_rows if r.get("status") == "异常报警"
        ]
        return base