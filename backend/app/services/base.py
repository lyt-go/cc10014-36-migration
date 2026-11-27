"""模块服务基类：共享查询、登记、动作和统计骨架，及跨模块业务联动。

每个领域服务除了基础CRUD外，还可以通过 cross_module_verify / cross_module_effect
注入跨模块业务校验逻辑和副作用。
"""
from __future__ import annotations

from typing import Any

from app.store import store


class BaseModuleService:
    MODULE = ""
    ENTITY = ""
    LIST_FIELDS: list[str] = []
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER: list[str] = []
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []

    # ── 跨模块业务扩展点（子类可覆盖） ──
    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        """执行动作前的跨模块校验。返回 (通过, 失败原因)。"""
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any], **kwargs: Any
    ) -> list[dict]:
        """动作执行后的跨模块副作用。返回受影响的联动条目列表。"""
        return []

    def handle_custom_action(
        self,
        entry_id: int,
        action: str,
        entry: dict[str, Any],
        values: dict[str, Any],
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str] | None:
        """处理不在 ACTION_RULES 里的专属动作（可携带 values）。

        返回 None 表示本服务不认领该动作，继续走通用校验；返回二元组则作为
        动作结果直接返回。
        """
        return None

    # ── 基础 CRUD ──
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(self.MODULE))
        for field, expected in (filters or {}).items():
            if field in self.LIST_FIELDS:
                rows = [
                    row for row in rows
                    if expected.casefold() in str(row.get(field, "")).casefold()
                ]
        if keyword and self.LIST_FIELDS:
            field = self.LIST_FIELDS[0]
            rows = [row for row in rows if keyword.casefold() in str(row.get(field, "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        rows = store.rows(self.MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in self.LIST_FIELDS})
        entry["status"] = self.STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """执行动作：幂等检查 -> 前置校验 -> 状态变更 -> 跨模块联动。"""
        values = values or {}
        entry = store.find(self.MODULE, entry_id)
        # 专属动作（如档案员确认存量来源）不改变通用状态机，先交给服务自行处理。
        if action not in self.ACTION_RULES and entry is not None:
            handled = self.handle_custom_action(entry_id, action, entry, values, remark)
            if handled is not None:
                return handled

        # 幂等检查
        if store.check_idempotent(self.MODULE, entry_id, action):
            entry = store.find(self.MODULE, entry_id)
            if entry:
                return entry, f"操作「{action}」已执行过，当前状态：{entry.get('status', '')}"
            return None, "操作已执行但记录可能已变更"

        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

        # 跨模块前置校验
        passed, reason = self.cross_module_verify(entry_id, action, entry)
        if not passed:
            return None, reason

        # 获取资源锁
        resource_key = f"{self.MODULE}:{entry_id}"
        if not store.acquire_resource(resource_key):
            return None, "资源正在被其他操作处理，请稍后重试"

        try:
            entry["status"] = target
            entry["pending"] = target != self.STATUS_ORDER[-1]
            entry["abnormal"] = action in self.NEGATIVE_ACTIONS

            # 执行跨模块副作用
            self.cross_module_effect(entry_id, action, entry, values=values)

            # 标记幂等
            store.mark_idempotent(self.MODULE, entry_id, action)
            return entry, f"{self.ENTITY}已{action}"
        finally:
            store.release_resource(resource_key)

    def domain_snapshot(self) -> dict[str, Any]:
        """返回本模块的状态统计与业务对象，供其他模块读取。"""
        rows = store.rows(self.MODULE)
        by_status = {
            status: sum(1 for row in rows if row.get("status") == status)
            for status in self.STATUS_ORDER
        }
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}状态记录",
            "count": len(rows),
            "by_status": by_status,
            "abnormal": sum(1 for row in rows if row.get("abnormal")),
            "rows": [dict(row) for row in rows],
        }