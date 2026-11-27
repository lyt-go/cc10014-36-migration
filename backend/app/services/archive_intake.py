"""档案入库凭据：把验收签署/旧式档案关联升级为可追溯、带版本的入库清单。

设计要点：
- 一张档案对应一张入库凭据（archive_intake 行），凭据以「档案编号」（archive_mgmt.id）
  为原编号，档案本身仍按原编号查询，名称、类别、形成日期一律不改写。
- 凭据只通过显式的来源验收记录编号建立关联，绝不按档案标题匹配；同名同日的两份
  验收档案因此不会互相串档。
- 每次关联/迁移/确认/签署同步都会写入 archive_link_events，两个动作分别建立了
  什么关联可以逐条查到。
- 迁移是幂等的：同一份档案、同一条来源验收不会重复插入档案或凭据；回滚只撤销
  迁移产生的凭据，不触碰验收记录的签署状态，恢复时也不会把已签署退回未办。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

INTAKE_TABLE = "archive_intake"
EVENT_TABLE = "archive_link_events"
STATE_TABLE = "archive_migration_state"

LEGACY_MARKER = re.compile(r"验收签署#(\d+)")
SOURCE_KEYS = (
    "来源验收记录",
    "来源验收编号",
    "来源验收id",
    "验收记录",
    "验收编号",
    "acceptance_id",
    "source_acceptance_id",
)

ASSOCIATED = "已关联"
PENDING = "待确认"
REVOKED = "已失效"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _digits(value: Any) -> int | None:
    """从数字/字符串（含「验收报告#5」这类写法）里取出记录编号。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


class ArchiveIntakeService:
    """档案入库清单领域服务（不被通用 /api/{module} 路由直接暴露）。"""

    MODULE = INTAKE_TABLE
    ENTITY = "入库凭据"

    # ── 基础访问 ──
    def rows(self) -> list[dict[str, Any]]:
        return store.rows(INTAKE_TABLE)

    def events(self) -> list[dict[str, Any]]:
        return store.rows(EVENT_TABLE)

    def _next_id(self, rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def _state_row(self) -> dict[str, Any]:
        rows = store.rows(STATE_TABLE)
        if not rows:
            rows.append({
                "id": 1,
                "状态": "未迁移",
                "迁移次数": 0,
                "回滚次数": 0,
                "更新时间": _now(),
            })
        return rows[0]

    def migration_state(self) -> dict[str, Any]:
        return dict(self._state_row())

    def get(self, credential_id: int) -> dict[str, Any] | None:
        for row in self.rows():
            if int(row.get("id", 0)) == credential_id:
                return row
        return None

    def by_archive(self, archive_id: int, *, include_revoked: bool = False) -> dict[str, Any] | None:
        for row in self.rows():
            if int(row.get("档案编号", 0)) != archive_id:
                continue
            if row.get("关联状态") == REVOKED and not include_revoked:
                continue
            return row
        return None

    def history_for_archive(self, archive_id: int) -> list[dict[str, Any]]:
        """同一档案的全部凭据版本，含已回滚的历史。"""
        return [
            dict(row) for row in self.rows()
            if int(row.get("档案编号", 0)) == archive_id
        ]

    def by_source(self, acceptance_id: int) -> list[dict[str, Any]]:
        return [
            row for row in self.rows()
            if _digits(row.get("来源验收记录")) == acceptance_id
            and row.get("关联状态") != REVOKED
        ]

    def events_for(self, credential_id: int) -> list[dict[str, Any]]:
        return [dict(e) for e in self.events() if e.get("凭据编号") == credential_id]

    def events_for_archive(self, archive_id: int) -> list[dict[str, Any]]:
        return [dict(e) for e in self.events() if e.get("档案编号") == archive_id]

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        source_id: int | None = None,
        archive_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(self.rows())
        if keyword:
            text = keyword.casefold()
            rows = [
                row for row in rows
                if text in str(row.get("档案名称", "")).casefold()
                or text in str(row.get("来源验收工序", "")).casefold()
                or text in str(row.get("档案编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("关联状态") == status]
        if source_id is not None:
            rows = [row for row in rows if _digits(row.get("来源验收记录")) == source_id]
        if archive_id is not None:
            rows = [row for row in rows if int(row.get("档案编号", 0)) == archive_id]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    # ── 事件与凭据写入 ──
    def _record_event(
        self,
        *,
        credential: dict[str, Any],
        action: str,
        source_id: int | None,
        linked_status: str,
        remark: str,
        action_no: str | None = None,
        signature_version: int | None = None,
    ) -> dict[str, Any]:
        rows = self.events()
        event = {
            "id": self._next_id(rows),
            "凭据编号": credential.get("id"),
            "档案编号": credential.get("档案编号"),
            "动作": action,
            "动作号": action_no or f"ACT-{self._next_id(rows):06d}",
            "来源验收记录": source_id,
            "来源验收工序": credential.get("来源验收工序", ""),
            "来源验收日期": credential.get("来源验收日期", ""),
            "来源验收签署版本": signature_version,
            "关联状态": linked_status,
            "凭据版本": credential.get("凭据版本", 1),
            "时间": _now(),
            "备注说明": remark,
        }
        rows.append(event)
        return event

    def _next_action_no(self) -> str:
        return f"ACT-{self._next_id(self.events()):06d}"

    def _mirror_to_archive(
        self, archive_id: int, credential: dict[str, Any] | None
    ) -> None:
        """把凭据摘要镜像到档案行，便于旧接口兼容读取；绝不改名称/类别/日期。"""
        archive = store.find("archive_mgmt", archive_id)
        if archive is None:
            return
        if credential is None:
            archive.pop("来源验收记录", None)
            archive.pop("凭据版本", None)
            archive.pop("关联状态", None)
            archive.pop("凭据编号", None)
            return
        archive["来源验收记录"] = credential.get("来源验收记录")
        archive["凭据版本"] = credential.get("凭据版本", 1)
        archive["关联状态"] = credential.get("关联状态", PENDING)
        archive["凭据编号"] = credential.get("id")

    def _new_credential(self, archive: dict[str, Any]) -> dict[str, Any]:
        credential = {
            "id": self._next_id(self.rows()),
            "档案编号": int(archive.get("id", 0)),
            "档案名称": archive.get("档案名称", ""),
            "档案类别": archive.get("档案类别", ""),
            "形成日期": archive.get("形成日期", ""),
            "来源验收记录": None,
            "来源验收工序": "",
            "来源验收日期": "",
            "来源验收签署版本": None,
            "来源验收结论": "",
            "凭据版本": 0,
            "关联状态": PENDING,
            "关联方式": "迁移",
            "最近动作号": "",
            "最近关联时间": "",
            "追溯线索": "",
            "备注说明": "",
        }
        self.rows().append(credential)
        return credential

    # ── 迁移 ──
    @staticmethod
    def _looks_like_acceptance_archive(archive: dict[str, Any]) -> bool:
        if str(archive.get("档案类别", "")) == "验收档案":
            return True
        if LEGACY_MARKER.search(str(archive.get("备注说明", ""))):
            return True
        return str(archive.get("档案名称", "")).endswith("-验收报告")

    def _source_from_archive(self, archive: dict[str, Any]) -> tuple[int | None, str]:
        explicit = archive.get("来源验收记录")
        source_id = _digits(explicit)
        if source_id is not None:
            return source_id, "档案字段来源验收记录"
        match = LEGACY_MARKER.search(str(archive.get("备注说明", "")))
        if match:
            return int(match.group(1)), f"旧式备注「{match.group(0)}」"
        return None, str(archive.get("备注说明", "")).strip()

    def _link_credential(
        self,
        credential: dict[str, Any],
        acceptance: dict[str, Any],
        *,
        mode: str,
        action: str,
        remark: str,
        signature_version: int | None = None,
    ) -> dict[str, Any]:
        credential["来源验收记录"] = int(acceptance.get("id", 0))
        credential["来源验收工序"] = acceptance.get("验收工序", "")
        credential["来源验收日期"] = acceptance.get("验收日期", "")
        credential["来源验收结论"] = acceptance.get("验收结论", "")
        credential["来源验收签署版本"] = (
            signature_version
            if signature_version is not None
            else acceptance.get("签署版本")
        )
        credential["凭据版本"] = int(credential.get("凭据版本", 0)) + 1
        credential["关联状态"] = ASSOCIATED
        credential["关联方式"] = mode
        credential["最近关联时间"] = _now()
        action_no = self._next_action_no()
        credential["最近动作号"] = action_no
        event = self._record_event(
            credential=credential,
            action=action,
            source_id=int(acceptance.get("id", 0)),
            linked_status=ASSOCIATED,
            remark=remark,
            action_no=action_no,
            signature_version=credential.get("来源验收签署版本"),
        )
        self._mirror_to_archive(int(credential["档案编号"]), credential)
        return event

    def migrate(self, *, triggered_by: str = "migrate") -> dict[str, Any]:
        """扫描旧式验收档案关联，生成/刷新入库凭据。可重复执行。"""
        if not store.acquire_resource("archive_intake:migration"):
            return {"ok": False, "message": "迁移正在被其他操作处理，请稍后重试"}
        try:
            return self._migrate_locked(triggered_by)
        finally:
            store.release_resource("archive_intake:migration")

    def _migrate_locked(self, triggered_by: str) -> dict[str, Any]:
        state = self._state_row()
        created = linked = pending = reused = 0
        scanned = 0
        for archive in list(store.rows("archive_mgmt")):
            if not self._looks_like_acceptance_archive(archive):
                continue
            scanned += 1
            archive_id = int(archive.get("id", 0))
            credential = self.by_archive(archive_id, include_revoked=True)
            is_new = credential is None
            if is_new:
                credential = self._new_credential(archive)
                created += 1

            if credential.get("关联状态") == REVOKED:
                # 回滚过的凭据在恢复时重新走一遍关联，历史事件仍保留。
                credential["关联状态"] = PENDING
                credential["备注说明"] = "迁移已恢复，重新建立关联"
                credential["关联方式"] = "迁移"

            source_id, clue = self._source_from_archive(archive)
            credential["追溯线索"] = clue
            acceptance = (
                store.find("acceptance_record", source_id)
                if source_id is not None else None
            )

            if credential.get("关联状态") == ASSOCIATED:
                # 已经由迁移/确认/签署建立过关联的凭据，重跑迁移一律不覆盖。
                reused += 1
                self._mirror_to_archive(archive_id, credential)
                continue

            if acceptance is None:
                # 来源不足：留待档案员显式确认，凭据仍挂在原档案编号上。
                if credential.get("关联状态") != ASSOCIATED:
                    credential["关联状态"] = PENDING
                    credential["来源验收记录"] = None
                    credential["来源验收工序"] = ""
                    credential["来源验收日期"] = ""
                    credential["凭据版本"] = max(int(credential.get("凭据版本", 0)), 1)
                    credential["备注说明"] = (
                        "旧档案缺少可核对的验收来源，待档案员确认"
                        if not clue else f"旧档案来源「{clue}」无法核对，待档案员确认"
                    )
                    pending += 1
                self._mirror_to_archive(archive_id, credential)
                if is_new:
                    self._record_event(
                        credential=credential,
                        action="迁移待确认",
                        source_id=None,
                        linked_status=PENDING,
                        remark=credential["备注说明"],
                    )
                continue

            # 来源明确：同一来源已经有一份凭据挂到别的档案上，不能按标题合并，
            # 也不能重复插入，另一份保留待确认，交给档案员人工判断。
            others = [
                row for row in self.by_source(int(acceptance["id"]))
                if int(row.get("档案编号", 0)) != archive_id
            ]
            if others:
                if credential.get("关联状态") != PENDING:
                    credential["关联状态"] = PENDING
                    credential["备注说明"] = (
                        f"来源验收#{acceptance['id']}已由档案#{others[0].get('档案编号')}关联，待档案员确认"
                    )
                    pending += 1
                self._mirror_to_archive(archive_id, credential)
                continue

            if credential.get("关联状态") == ASSOCIATED and _digits(
                credential.get("来源验收记录")
            ) == int(acceptance["id"]):
                # 已关联且来源未变：重跑迁移不新增凭据、不重复写事件。
                reused += 1
                self._mirror_to_archive(archive_id, credential)
                continue

            # 迁移不覆盖档案的名称/类别/成文日期，只补来源与版本。
            self._link_credential(
                credential,
                acceptance,
                mode="迁移",
                action="迁移关联",
                remark=f"由旧式档案关联迁移，来源验收#{acceptance['id']}",
            )
            linked += 1

        state["状态"] = "已迁移"
        state["迁移次数"] = int(state.get("迁移次数", 0)) + 1
        state["更新时间"] = _now()
        state["触发方式"] = triggered_by
        return {
            "ok": True,
            "message": (
                f"档案入库凭据迁移完成：扫描{scanned}份，"
                f"新建{created}份，关联{linked}份，待确认{pending}份，复用{reused}份"
            ),
            "state": state["状态"],
            "scanned": scanned,
            "created": created,
            "linked": linked,
            "pending": pending,
            "reused": reused,
            "credentials": len(self.rows()),
        }

    # ── 档案员确认存量来源 ──
    @staticmethod
    def _extract_source(values: dict[str, Any]) -> int | None:
        for key in SOURCE_KEYS:
            if key in values:
                found = _digits(values.get(key))
                if found is not None:
                    return found
        return None

    def confirm(
        self, archive_id: int, values: dict[str, Any] | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        archive = store.find("archive_mgmt", archive_id)
        if archive is None:
            return None, f"修缮档案 {archive_id} 不存在或已归档"
        source_id = self._extract_source(values)
        if source_id is None:
            return None, "请提供待确认的来源验收记录编号（来源验收记录）"
        acceptance = store.find("acceptance_record", source_id)
        if acceptance is None:
            return None, f"来源验收记录 {source_id} 不存在，无法确认存量来源"

        if not store.acquire_resource(f"archive_intake:archive:{archive_id}"):
            return None, "档案正在被其他操作处理，请稍后重试"
        try:
            return self._confirm_locked(archive_id, acceptance, source_id, remark)
        finally:
            store.release_resource(f"archive_intake:archive:{archive_id}")

    def _confirm_locked(self, archive_id, acceptance, source_id, remark):
        credential = self.by_archive(archive_id)
        if credential is None:
            credential = self._new_credential(store.find("archive_mgmt", archive_id))
            credential["追溯线索"] = "档案员确认存量来源"
        if (
            credential.get("关联状态") == ASSOCIATED
            and _digits(credential.get("来源验收记录")) == source_id
        ):
            self._mirror_to_archive(archive_id, credential)
            return credential, f"档案#{archive_id}已关联来源验收#{source_id}，无需重复确认"

        # 同名同日验收不能靠标题判断：同一来源已被另一份档案占用时，拒绝直接覆盖，
        # 两份凭据各自留痕，由档案员改走补录或调整。
        occupied = [
            row for row in self.by_source(source_id)
            if int(row.get("档案编号", 0)) != archive_id
        ]
        if occupied:
            return None, (
                f"来源验收#{source_id}已由档案#{occupied[0].get('档案编号')}关联，"
                "同名档案请先核对再确认"
            )

        note = remark or f"档案员确认档案#{archive_id}的存量来源为验收#{source_id}"
        self._link_credential(
            credential,
            acceptance,
            mode="人工确认",
            action="确认存量来源",
            remark=note,
        )
        return credential, f"已将档案#{archive_id}的存量来源确认为验收#{source_id}"

    # ── 验收签署同步 ──
    def record_sign(
        self,
        acceptance: dict[str, Any],
        *,
        actor: str = "",
        remark: str = "",
    ) -> dict[str, Any]:
        """签署回执落库：按来源验收编号写入档案入库清单，不按标题匹配。"""
        acceptance_id = int(acceptance.get("id", 0))
        if not store.acquire_resource(f"archive_intake:source:{acceptance_id}"):
            raise RuntimeError("验收签署同步正在被其他操作处理，请稍后重试")
        try:
            return self._record_sign_locked(acceptance, actor=actor, remark=remark)
        finally:
            store.release_resource(f"archive_intake:source:{acceptance_id}")

    def _record_sign_locked(self, acceptance, *, actor="", remark=""):
        acceptance_id = int(acceptance.get("id", 0))
        signature_version = int(acceptance.get("签署版本", 0) or 0) + 1
        acceptance["签署版本"] = signature_version
        action_no = self._next_action_no()
        acceptance["签署动作号"] = action_no
        acceptance["签署时间"] = _now()

        candidate_rows = [
            row for row in self.rows()
            if _digits(row.get("来源验收记录")) == acceptance_id
        ]
        # 同一来源优先复用已确认的凭据；其次复用未回滚的签署凭据；
        # 再次复用回滚留档的凭据（避免新插一份同源档案）。
        candidate_rows.sort(
            key=lambda row: (
                0 if row.get("关联方式") == "人工确认" else
                1 if row.get("关联状态") != REVOKED else 2,
                int(row.get("id", 0)),
            )
        )
        credential = candidate_rows[0] if candidate_rows else None
        if credential is None:
            # 档案行上已有同一来源的镜像时，复用该档案的凭据，避免重复入库。
            for archive_row in store.rows("archive_mgmt"):
                if _digits(archive_row.get("来源验收记录")) != acceptance_id:
                    continue
                candidate = self.by_archive(
                    int(archive_row.get("id", 0)), include_revoked=True
                )
                if candidate is not None:
                    credential = candidate
                    break
        if credential is None:
            # 没有可复用的凭据时才新建档案；标题相同的其他档案一律不碰。
            process = acceptance.get("验收工序", "")
            arch_rows = store.rows("archive_mgmt")
            new_archive = {
                "id": max((int(r.get("id", 0)) for r in arch_rows), default=0) + 1,
                "档案名称": f"{process}-验收报告",
                "档案类别": "验收档案",
                "形成日期": acceptance.get("验收日期", ""),
                "保管期限": "永久",
                "密级等级": "内部",
                "归档人": acceptance.get("验收人", ""),
                "归档状态": "待整理",
                "备注说明": f"验收签署#{acceptance_id}自动触发",
                "status": "待整理",
                "pending": True,
                "abnormal": False,
            }
            arch_rows.append(new_archive)
            credential = self._new_credential(new_archive)
            credential["追溯线索"] = f"验收签署#{acceptance_id}自动触发"

        # 签署只在既有凭据上追加版本，关联方式保留档案员最初的认定。
        previous_mode = credential.get("关联方式")
        self._link_credential(
            credential,
            acceptance,
            mode="签署",
            action="验收签署同步",
            remark=(
                remark
                or f"验收#{acceptance_id}第{signature_version}次签署，动作号{action_no}"
            ),
            signature_version=signature_version,
        )
        if previous_mode and previous_mode != "迁移":
            # 人工确认/补录过的凭据，签署后仍标注最初的关联方式。
            credential["关联方式"] = previous_mode
        credential["签署人"] = acceptance.get("验收人", "")
        credential["签署日期"] = acceptance.get("验收日期", "")
        credential["来源验收结论"] = acceptance.get("验收结论", "")
        self._mirror_to_archive(int(credential["档案编号"]), credential)
        return credential

    # ── 归档入库只联动凭据指向的验收 ──
    def acceptance_for_archive(self, archive_id: int) -> dict[str, Any] | None:
        credential = self.by_archive(archive_id)
        if credential is None:
            return None
        source_id = _digits(credential.get("来源验收记录"))
        if source_id is None:
            return None
        return store.find("acceptance_record", source_id)

    # ── 回滚 / 恢复 ──
    def rollback(self, *, triggered_by: str = "rollback") -> dict[str, Any]:
        """撤销迁移写入的凭据，保留人工确认/签署产生的关联和验收状态。"""
        if not store.acquire_resource("archive_intake:migration"):
            return {"ok": False, "message": "迁移正在被其他操作处理，请稍后重试"}
        try:
            return self._rollback_locked(triggered_by)
        finally:
            store.release_resource("archive_intake:migration")

    def _rollback_locked(self, triggered_by: str) -> dict[str, Any]:
        state = self._state_row()
        revoked = kept = 0
        for credential in list(self.rows()):
            if credential.get("关联方式") != "迁移":
                kept += 1
                continue
            if credential.get("关联状态") == REVOKED:
                kept += 1
                continue
            archive_id = int(credential.get("档案编号", 0))
            self._record_event(
                credential=credential,
                action="回滚迁移",
                source_id=_digits(credential.get("来源验收记录")),
                linked_status=REVOKED,
                remark="回滚迁移：撤销迁移凭据，不动验收签署状态",
            )
            # 保留凭据行与事件，便于回滚后仍能查到曾建立过什么关联。
            credential["关联状态"] = REVOKED
            credential["备注说明"] = "迁移已回滚，凭据留档待恢复"
            self._mirror_to_archive(archive_id, None)
            revoked += 1
        state["状态"] = "已回滚"
        state["回滚次数"] = int(state.get("回滚次数", 0)) + 1
        state["更新时间"] = _now()
        state["触发方式"] = triggered_by
        return {
            "ok": True,
            "message": f"已回滚迁移凭据{revoked}份，保留签署/确认关联{kept}份",
            "state": state["状态"],
            "removed": revoked,
            "kept": kept,
            "credentials": len(self.rows()),
        }

    def restore(self, *, triggered_by: str = "restore") -> dict[str, Any]:
        """恢复迁移：重新扫描旧档案，已有签署/确认关联不降级。"""
        result = self.migrate(triggered_by=triggered_by)
        result["message"] = "迁移已恢复：" + str(result.get("message", ""))
        return result

    def ensure_migrated(self) -> None:
        """升级后首次读取入库清单时自动补齐迁移，仅执行一次。"""
        if self._state_row().get("状态") == "未迁移":
            self.migrate(triggered_by="lazy-upgrade")


intake_service = ArchiveIntakeService()
