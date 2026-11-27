"""内存数据仓库：含线程安全、幂等追踪和跨模块数据协调。

真实项目里这里会换成数据库访问层；当前实现保证克隆下来就能起，且支持并发保护。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from app.seed import SEED_ROWS

# 内部台账表：随业务模块一起保存在仓库里，但不作为独立业务模块出现在概览/依赖图中。
INTERNAL_MODULES = {
    "archive_intake",
    "archive_link_events",
    "archive_migration_state",
}


class Store:
    _lock = threading.RLock()

    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 幂等追踪：{(module, entry_id, action, idempotency_key): timestamp}
        self._idempotent_log: dict[tuple, float] = {}
        # 跨模块锁：{resource_key: thread_id}  用于并发场景 tolerance
        self._resource_locks: dict[str, int] = {}

    # ── 基础访问 ──
    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in INTERNAL_MODULES)

    def all_table_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}

    # ── 幂等性 ──
    def idempotent_key(self, module: str, entry_id: int, action: str) -> str:
        """生成幂等键：模块+记录+动作+小时窗口，避免同小时重复请求。"""
        hour_window = int(time.time() / 3600)
        return f"{module}:{entry_id}:{action}:{hour_window}"

    def check_idempotent(self, module: str, entry_id: int, action: str) -> bool:
        """检查是否已在幂等窗口内执行过。返回 True 表示已执行过。"""
        key = self.idempotent_key(module, entry_id, action)
        with self._lock:
            return key in self._idempotent_log

    def mark_idempotent(self, module: str, entry_id: int, action: str) -> None:
        """标记动作已执行。"""
        key = self.idempotent_key(module, entry_id, action)
        with self._lock:
            self._idempotent_log[key] = time.time()
            # 定期清理超过2小时的日志
            cutoff = time.time() - 7200
            expired = [k for k, v in self._idempotent_log.items() if v < cutoff]
            for k in expired:
                del self._idempotent_log[k]

    # ── 并发保护 ──
    def acquire_resource(self, resource_key: str, wait_timeout: float = 5.0) -> bool:
        """获取资源锁，用于并发操作保护。"""
        deadline = time.time() + wait_timeout
        with self._lock:
            while self._resource_locks.get(resource_key) is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._lock.release()
                time.sleep(0.05)
                self._lock.acquire()
            self._resource_locks[resource_key] = threading.get_ident()
            return True

    def release_resource(self, resource_key: str) -> None:
        """释放资源锁。"""
        with self._lock:
            self._resource_locks.pop(resource_key, None)

    # ── 跨模块原子更新 ──
    def atomic_update(self, operations: list[dict]) -> list[dict] | None:
        """原子地执行一组操作，全部成功或全部回滚。

        operations: [{"module": ..., "entry_id": ..., "changes": {...}}, ...]
        返回更新后的条目列表，失败返回 None。
        """
        if not operations:
            return []
        snapshots = []
        with self._lock:
            # 快照所有即将修改的条目
            for op in operations:
                row = self.find(op["module"], op["entry_id"])
                if row is None:
                    return None
                snapshots.append(dict(row))
            # 应用变更
            updated = []
            for op in operations:
                row = self.find(op["module"], op["entry_id"])
                if row is None:
                    # 理论上不会到这里，因为上面已检查
                    # 回滚
                    for i, s in enumerate(snapshots):
                        target = self.find(operations[i]["module"], operations[i]["entry_id"])
                        if target is not None:
                            target.clear()
                            target.update(s)
                    return None
                for key, value in op.get("changes", {}).items():
                    row[key] = value
                updated.append(dict(row))
            return updated

    # ── 跨模块查询辅助 ──
    def query_by_field(self, module: str, field: str, value: Any) -> list[dict[str, Any]]:
        """跨模块查询：按字段值查找所有匹配行。"""
        result = []
        for row in self.rows(module):
            if str(row.get(field, "")) == str(value):
                result.append(dict(row))
        return result

    def cross_module_snapshot(self, modules: list[str]) -> dict[str, Any]:
        """返回多个模块的状态快照，供前端跨模块面板使用。"""
        result: dict[str, Any] = {}
        for mod in modules:
            rows = self.rows(mod)
            status_counts: dict[str, int] = {}
            for row in rows:
                s = row.get("status", "")
                status_counts[s] = status_counts.get(s, 0) + 1
            result[mod] = {
                "total": len(rows),
                "by_status": status_counts,
            }
        return result


store = Store()