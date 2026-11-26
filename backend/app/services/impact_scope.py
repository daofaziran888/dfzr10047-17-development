"""差异字段影响范围与领取受理数据集服务。

核心约束：页面勾选和补证说明都不能直接制造受影响批次；是否受理始终由
``expression_hit`` 中的真实命中记录、获准模板版本和获准来源共同裁决。
本模块同时维护影响范围单、领取回执、执行名单和失败恢复记录等内存数据集。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).replace("，", ",").replace(";", ",").replace("；", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _contains_identifier(hit: dict[str, Any], ref: Any) -> bool:
    if ref is None:
        return False
    target = str(ref).strip().casefold()
    if not target:
        return False
    candidates = [
        hit.get("id"),
        hit.get("命中编号"),
        hit.get("证据编号"),
        hit.get("批次编号"),
        hit.get("批次短号"),
        hit.get("展示批次"),
        hit.get("item_key"),
        hit.get("hit_id"),
        hit.get("batch_id"),
        hit.get("batch_short"),
        hit.get("batch_no"),
    ]
    for value in candidates:
        text = str(value or "").strip().casefold()
        if not text:
            continue
        if text == target or target in text:
            return True
    # 允许用 “甲的008” / “批次008” 这类自然语言引用定位。
    short = str(hit.get("批次短号") or "").strip().casefold()
    return bool(short and short in target)


class ImpactScopeService(BaseModuleService):
    MODULE = "impact_scope"
    ENTITY = "影响范围单"
    OPERATION_TERM = "受理"
    LIST_FIELDS = [
        "范围单号", "领取回执号", "差异编号", "来源渠道", "差异字段", "旧表达式",
        "模板编号", "模板版本", "原筛选范围", "核准批次", "未受理批次",
        "关联重解析任务", "幂等键", "受理时间",
    ]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = ["待确认", "已受理", "处理中", "部分失败", "已完成"]
    ACTION_RULES = {
        "查看回执": "已受理",
        "执行全部": "处理中",
        "执行本次名单": "处理中",
        "重交原范围": "已受理",
        "恢复失败批次": "处理中",
    }

    # ------------------------------------------------------------------
    # 基础查找与筛选条件
    # ------------------------------------------------------------------
    def _hits(self) -> list[dict[str, Any]]:
        return store.rows("expression_hit")

    def _find_diff(self, diff_ref: Any = None, field_name: str = "") -> dict[str, Any] | None:
        rows = store.rows("tmpl_diff")
        if diff_ref not in (None, ""):
            text = str(diff_ref).strip()
            if text.isdigit():
                found = store.find("tmpl_diff", int(text))
                if found is not None:
                    return found
            found = next(
                (row for row in rows
                 if str(row.get("差异编号") or "").strip().casefold() == text.casefold()),
                None,
            )
            if found is not None:
                return found
        if field_name:
            return next(
                (row for row in rows
                 if field_name in _split_values(str(row.get("差异字段") or "").replace(",", ","))),
                None,
            )
        return next((row for row in rows if row.get("差异编号") == "TD-AMOUNT"), None)

    @staticmethod
    def _pick(payload: dict[str, Any], *keys: str, default: Any = "") -> Any:
        for key in keys:
            if key in payload and payload[key] not in (None, ""):
                return payload[key]
        return default

    def normalize_criteria(self, payload: dict[str, Any]) -> dict[str, Any]:
        diff_ref = self._pick(payload, "diff_id", "差异编号", "diff_no", default="")
        field_name = str(self._pick(
            payload, "field_name", "差异字段", "字段名称", default="amount"
        )).strip()
        diff = self._find_diff(diff_ref, field_name)
        if diff is not None and not diff_ref:
            diff_ref = diff.get("差异编号", "")
        source = str(self._pick(payload, "source", "来源渠道", "来源", default="甲来源")).strip()
        old_expression = str(self._pick(
            payload, "old_expression", "旧表达式", "expression", default="amount"
        )).strip()
        template_id = str(self._pick(
            payload, "template_id", "模板编号",
            default=(diff or {}).get("获准模板编号") or (diff or {}).get("旧模板编号") or "ET-006",
        )).strip()
        template_version = str(self._pick(
            payload, "template_version", "模板版本", "旧模板版本",
            default=(diff or {}).get("获准模板版本") or (diff or {}).get("旧模板版本") or "v1.4.0",
        )).strip()
        include_completed = payload.get("include_completed", payload.get("包含已完成", True))
        if isinstance(include_completed, str):
            include_completed = include_completed not in {"0", "false", "False", "否"}
        return {
            "diff_id": str(diff_ref or (diff or {}).get("差异编号") or ""),
            "source": source,
            "old_expression": old_expression,
            "field_name": field_name,
            "template_id": template_id,
            "template_version": template_version,
            "include_completed": bool(include_completed),
        }

    @staticmethod
    def _source_matches(value: Any, query: str) -> bool:
        if not query:
            return True
        actual = str(value or "")
        q = query.strip()
        aliases = {q, q.removesuffix("来源"), f"{q}来源" if not q.endswith("来源") else q}
        return any(alias and alias in actual for alias in aliases)

    @staticmethod
    def _expression_matches(hit: dict[str, Any], expression: str, field_name: str) -> bool:
        expr = str(hit.get("旧表达式") or "")
        field = str(hit.get("字段名称") or "")
        q = (expression or "").strip().casefold()
        if not q:
            return field == field_name if field_name else True
        if q in expr.casefold() or q in field.casefold():
            return True
        # 兼容测试或运维把“amount 旧表达式”“旧 amount”整段贴入的情况。
        identifiers = [m.group(0).casefold() for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", q)]
        return any(token in expr.casefold() or token == field.casefold() for token in identifiers)

    def _template_ok(self, hit: dict[str, Any], criteria: dict[str, Any], diff: dict[str, Any] | None) -> bool:
        approved_ids = _split_values((diff or {}).get("获准模板编号")) or _split_values((diff or {}).get("旧模板编号"))
        approved_versions = _split_values((diff or {}).get("获准模板版本")) or _split_values((diff or {}).get("旧模板版本"))
        if criteria.get("template_id"):
            approved_ids.append(criteria["template_id"])
        if criteria.get("template_version"):
            approved_versions.append(criteria["template_version"])
        hit_id = str(hit.get("模板编号") or "").strip()
        hit_version = str(hit.get("模板版本") or "").strip()
        id_ok = not approved_ids or hit_id in approved_ids
        version_ok = not approved_versions or hit_version in approved_versions
        return id_ok and version_ok

    def _approved_source_ok(self, hit: dict[str, Any], criteria: dict[str, Any], diff: dict[str, Any] | None) -> bool:
        approved = _split_values((diff or {}).get("获准来源"))
        if not approved:
            return self._source_matches(hit.get("来源渠道"), criteria["source"])
        return any(self._source_matches(hit.get("来源渠道"), item) for item in approved)

    @staticmethod
    def _evidence_ok(hit: dict[str, Any]) -> bool:
        if str(hit.get("核准命中") or "") == "是":
            return True
        state = str(hit.get("证据状态") or "")
        return ("已核验" in state or "已采信" in state) and "尚未" not in state and "待" not in state

    def _active_claimed_batches(self) -> set[str]:
        claimed: set[str] = set()
        for scope in store.rows(self.MODULE):
            if scope.get("status") == "已完成":
                continue
            for item in scope.get("items") or []:
                if item.get("status") != "已完成":
                    for key in ("batch_id", "batch_short", "batch_no"):
                        value = str(item.get(key) or "")
                        if value:
                            claimed.add(value.casefold())
        return claimed

    def query_candidates(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        criteria = self.normalize_criteria(payload or {})
        diff = self._find_diff(criteria["diff_id"], criteria["field_name"])
        claimed = self._active_claimed_batches()
        candidates: list[dict[str, Any]] = []
        completed: list[dict[str, Any]] = []
        ineligible: list[dict[str, Any]] = []

        for hit in self._hits():
            if not self._expression_matches(hit, criteria["old_expression"], criteria["field_name"]):
                continue
            reasons: list[str] = []
            requested_source_ok = self._source_matches(hit.get("来源渠道"), criteria["source"])
            approved_source_ok = self._approved_source_ok(hit, criteria, diff)
            template_ok = self._template_ok(hit, criteria, diff)
            evidence_ok = self._evidence_ok(hit)
            if not requested_source_ok:
                reasons.append(
                    f"来源渠道「{hit.get('来源渠道')}」不符合筛选来源「{criteria['source']}」"
                )
            if not approved_source_ok and requested_source_ok:
                reasons.append(f"来源渠道「{hit.get('来源渠道')}」未获该差异核准")
            elif not approved_source_ok:
                reasons.append(f"来源渠道「{hit.get('来源渠道')}」不在获准来源范围内")
            if not template_ok:
                reasons.append(
                    f"模板 {hit.get('模板编号')}/{hit.get('模板版本')} 不是本差异获准模板版本"
                )
            if not evidence_ok:
                # 未送达的迟到证据在采信前不进入候选，也不放到不合格清单，避免页面误列 008。
                continue

            processing = str(hit.get("处理状态") or hit.get("status") or "待处理")
            row = dict(hit)
            row["不符合原因"] = "；".join(reasons)
            row["already_claimed"] = any(_contains_identifier(hit, key) for key in claimed)

            if reasons:
                ineligible.append(row)
            elif processing == "已完成":
                completed.append(row)
            elif processing == "不予受理":
                reasons.append("命中记录已被裁决不予受理")
                row["不符合原因"] = "；".join(reasons)
                ineligible.append(row)
            elif processing == "处理失败":
                row["recoverable"] = True
                candidates.append(row)
            else:
                candidates.append(row)

        evidence_rows = store.rows("source_evidence")
        late = self._late_evidence_state(evidence_rows)
        template_snapshot = self._template_snapshot(criteria, diff)
        claimable = [row for row in candidates if not row.get("already_claimed")]
        return {
            "ok": True,
            "criteria": criteria,
            "diff": dict(diff) if diff else None,
            "template_snapshot": template_snapshot,
            "candidates": candidates,
            "claimable": claimable,
            "items": candidates,
            "batches": candidates,
            "eligible_batches": candidates,
            "claimable_batches": claimable,
            "completed": completed if criteria["include_completed"] else [],
            "ineligible": ineligible,
            "late_evidence": late,
            "counts": {
                "candidates": len(candidates),
                "claimable": len(claimable),
                "completed": len(completed),
                "ineligible": len(ineligible),
                "late_expanded": len(late.get("expanded_batches") or []),
            },
            "queried_at": _now(),
        }

    def _late_evidence_state(self, evidence_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = evidence_rows if evidence_rows is not None else store.rows("source_evidence")
        pending = next((row for row in rows if "待" in str(row.get("送达状态") or "")), None)
        if pending:
            return {
                "applied": False,
                "available": True,
                "evidence_no": pending.get("证据编号"),
                "batch": pending.get("批次短号") or pending.get("批次编号"),
                "expanded_batches": [],
                "title": "有一份迟到源证据待同步，采信前候选不扩充",
                "description": pending.get("证据说明"),
            }
        applied_rows = [
            row for row in rows
            if "已" in str(row.get("送达状态") or "")
            and ("迟到" in str(row.get("证据类型") or "") or "LATE" in str(row.get("证据编号") or "").upper())
        ]
        applied_rows.sort(key=lambda row: str(row.get("采信时间") or row.get("登记时间") or ""), reverse=True)
        expanded = [str(row.get("批次短号") or row.get("批次编号") or "") for row in applied_rows]
        if applied_rows:
            latest = applied_rows[0]
            return {
                "applied": True,
                "available": True,
                "evidence_no": latest.get("证据编号"),
                "batch": latest.get("批次短号") or latest.get("批次编号"),
                "expanded_batches": expanded,
                "title": f"迟到证据已扩充候选：{'、'.join(expanded)}",
                "description": latest.get("采信说明") or latest.get("证据说明"),
                "applied_at": latest.get("采信时间"),
            }
        return {
            "applied": False,
            "available": False,
            "expanded_batches": [],
            "title": "暂无迟到证据，候选未扩充",
            "description": "",
        }

    def _template_snapshot(self, criteria: dict[str, Any], diff: dict[str, Any] | None) -> dict[str, Any]:
        template = next((
            row for row in store.rows("extract_tmpl")
            if str(row.get("模板编号")) == criteria["template_id"]
            or str(row.get("版本号")) == criteria["template_version"]
        ), None)
        return {
            "diff_id": criteria["diff_id"],
            "template_id": criteria["template_id"],
            "template_version": criteria["template_version"],
            "template_name": (template or {}).get("模板名称", ""),
            "approved_source": (diff or {}).get("获准来源") or criteria["source"],
            "old_expression": criteria["old_expression"],
            "field_name": criteria["field_name"],
            "new_template_id": (diff or {}).get("新模板编号", ""),
            "new_template_version": (diff or {}).get("新模板版本", ""),
        }

    # ------------------------------------------------------------------
    # 迟到证据
    # ------------------------------------------------------------------
    def receive_late_evidence(self, payload: dict[str, Any] | None = None) -> tuple[bool, str, dict[str, Any]]:
        data = payload or {}
        batch_ref = self._pick(data, "batch", "batch_no", "批次", "批次短号", "批次编号", default="008")
        note = str(self._pick(data, "note", "evidence_note", "证据说明", default="")).strip()
        evidence = next((row for row in store.rows("source_evidence") if _contains_identifier(row, batch_ref)), None)
        hit = next((row for row in self._hits() if _contains_identifier(row, batch_ref)), None)
        if evidence is None and hit is None:
            return False, f"找不到批次 {batch_ref} 的迟到源证据", self.query_candidates(data)

        if evidence is not None and str(evidence.get("送达状态")) != "已采信":
            evidence["送达状态"] = "已采信"
            evidence["status"] = "已核查"
            evidence["pending"] = False
            evidence["采信时间"] = _now()
            if note:
                evidence["采信说明"] = note
            else:
                evidence["采信说明"] = evidence.get("证据说明")

        if hit is not None:
            hit["核准命中"] = "是"
            hit["证据状态"] = "迟到证据已采信：源日志与表达式快照可追溯"
            hit["status"] = "待处理"
            hit["pending"] = True
            hit["late_evidence_received_at"] = _now()
            if note:
                hit["迟到证据说明"] = note

        result = self.query_candidates(data)
        diff = self._find_diff(result["criteria"].get("diff_id"), result["criteria"].get("field_name"))
        if diff is not None:
            actionable = len(result["candidates"]) + len(result["completed"])
            diff["影响批次数"] = str(actionable)
            diff["待处理批次数"] = str(len(result["candidates"]))
            diff["已完成批次数"] = str(len(result["completed"]))
        expanded = "、".join(result["late_evidence"].get("expanded_batches") or [])
        return True, f"迟到证据已采信，候选扩充：{expanded or batch_ref}", result

    # ------------------------------------------------------------------
    # 影响范围单与领取回执
    # ------------------------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """通用 POST /api/impact_scope 入口：直接按筛选申请执行受理裁决。"""
        ok, message, result, _duplicate = self.submit_application(values or {})
        if not ok or not isinstance(result, dict) or not result.get("scope"):
            return None, [message]
        return result["scope"], []

    def _scope_key(self, criteria: dict[str, Any]) -> str:
        raw = "|".join([
            criteria["diff_id"], criteria["source"], criteria["old_expression"],
            criteria["field_name"], criteria["template_id"], criteria["template_version"],
        ])
        return "scope-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _next_scope_no(self) -> str:
        rows = store.rows(self.MODULE)
        nums = []
        for row in rows:
            text = str(row.get("范围单号") or "")
            digits = "".join(ch for ch in text if ch.isdigit())
            if digits:
                nums.append(int(digits))
        return f"IS-{max(nums, default=0) + 1:03d}"

    def find_scope(self, scope_ref: Any) -> dict[str, Any] | None:
        if scope_ref in (None, ""):
            return None
        if isinstance(scope_ref, int) or str(scope_ref).isdigit():
            found = store.find(self.MODULE, int(scope_ref))
            if found is not None:
                return found
        text = str(scope_ref).strip().casefold()
        for scope in store.rows(self.MODULE):
            values = [scope.get("范围单号"), scope.get("领取回执号"), scope.get("幂等键"), scope.get("关联重解析任务")]
            if any(text == str(v or "").casefold() for v in values):
                return scope
            if text in str(scope.get("范围单号") or "").casefold():
                return scope
        return None

    def submit_application(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None, bool]:
        criteria = self.normalize_criteria(payload)
        criteria_key = self._scope_key(criteria)
        client_key = str(self._pick(
            payload, "request_key", "idempotency_key", "requestId", "request_id", default="",
        )).strip()

        query = self.query_candidates({**payload, **criteria})
        selected_refs = self._pick(
            payload, "selected_batches", "batch_numbers", "batches", "selected", "ids", default=[]
        )
        selected_refs = _split_values(selected_refs) if not isinstance(selected_refs, list) else selected_refs
        candidates = query["candidates"]
        if selected_refs:
            accepted_hits = [hit for hit in candidates if any(_contains_identifier(hit, ref) for ref in selected_refs)]
        else:
            accepted_hits = list(candidates)

        items: list[dict[str, Any]] = []
        for index, hit in enumerate(accepted_hits, start=1):
            items.append({
                "item_id": index,
                "hit_id": hit.get("命中编号"),
                "batch_id": hit.get("批次编号"),
                "batch_short": hit.get("批次短号"),
                "batch_no": hit.get("展示批次") or hit.get("批次短号"),
                "source": hit.get("来源渠道"),
                "field": hit.get("字段名称"),
                "approved_field": criteria["field_name"],
                "approved_expression": hit.get("旧表达式"),
                "template_id": hit.get("模板编号"),
                "template_version": hit.get("模板版本"),
                "late_evidence": str(hit.get("是否迟到证据")) == "是" or "迟到证据已采信" in str(hit.get("证据状态")),
                "status": "待处理",
                "accepted": True,
                "attempts": 0,
                "error": "",
                "result": "",
            })

        unaccepted: list[dict[str, Any]] = []
        # 服务端始终保留因来源/模板不符而未受理的项目，例如丙批；前端无法把它勾选进修复范围。
        for hit in query["ineligible"]:
            unaccepted.append(self._unaccepted_item(hit, hit.get("不符合原因") or "不符合获准范围"))
        if selected_refs:
            known = {str(item["batch_id"]).casefold() for item in items}
            known.update(str(item["batch_short"]).casefold() for item in items)
            for ref in selected_refs:
                hit = next((row for row in self._hits() if _contains_identifier(row, ref)), None)
                if hit is None:
                    unaccepted.append({
                        "item_key": str(ref), "batch_no": str(ref), "status": "未受理",
                        "reason": "找不到真实命中记录，不能凭申请文本受理", "evidence_notes": [],
                    })
                elif not any(_contains_identifier(hit, key) for key in known):
                    reason = next((
                        row.get("不符合原因") for row in query["ineligible"]
                        if _contains_identifier(row, ref)
                    ), "该命中尚未通过证据或授权核验")
                    unaccepted.append(self._unaccepted_item(hit, reason))

        # 同名丙批可能同时来自服务端裁决清单和前端显式选择；按命中键去重，避免回执重复。
        deduped_unaccepted: list[dict[str, Any]] = []
        seen_unaccepted: set[str] = set()
        for item in unaccepted:
            key = str(item.get("item_key") or item.get("hit_id") or item.get("batch_id") or id(item))
            if key in seen_unaccepted:
                continue
            seen_unaccepted.add(key)
            deduped_unaccepted.append(item)
        unaccepted = deduped_unaccepted

        if not items:
            requested_ids_set: set[str] = set()
            for ref in selected_refs:
                hit = next((row for row in self._hits() if _contains_identifier(row, ref)), None)
                requested_ids_set.add(str((hit or {}).get("批次编号") or (hit or {}).get("批次短号") or ref))
            requested_ids = sorted(requested_ids_set)
            replay_fingerprint = "app-" + hashlib.sha256(
                (criteria_key + "|" + ",".join(requested_ids)).encode("utf-8")
            ).hexdigest()[:16]
            replay = None
            if client_key:
                replay = next((row for row in store.rows(self.MODULE) if row.get("幂等键") == client_key), None)
            if replay is None and requested_ids:
                replay = next((
                    row for row in store.rows(self.MODULE)
                    if row.get("申请指纹") == replay_fingerprint
                    or row.get("幂等键") == replay_fingerprint
                ), None)
            if replay is None and not selected_refs:
                replay = next((
                    row for row in reversed(store.rows(self.MODULE))
                    if row.get("criteria_key") == criteria_key
                ), None)
            if replay is not None:
                receipt = self.receipt(replay.get("id"))
                return True, (
                    f"原筛选申请已存在：{replay.get('范围单号')}，未重复建单或排任务"
                ), receipt, True
            return False, "没有服务端核验通过的待处理真实命中，未创建影响范围单", {
                "query": query, "unaccepted_items": unaccepted,
            }, False

        batch_ids = sorted(str(item.get("batch_id") or item.get("batch_short") or "") for item in items)
        batch_fingerprint = "app-" + hashlib.sha256(
            (criteria_key + "|" + ",".join(batch_ids)).encode("utf-8")
        ).hexdigest()[:16]
        idempotency_key = client_key or batch_fingerprint

        existing: dict[str, Any] | None = None
        if client_key:
            existing = next((row for row in store.rows(self.MODULE) if row.get("幂等键") == client_key), None)
        if existing is None:
            existing = next((
                row for row in store.rows(self.MODULE)
                if row.get("申请指纹") == batch_fingerprint
                or row.get("幂等键") == batch_fingerprint
                or (
                    row.get("criteria_key") == criteria_key
                    and sorted(str(x) for x in row.get("pending_batches") or []) == batch_ids
                )
            ), None)
        if existing is None:
            # 同一筛选同时只允许有一份未完成的有效范围；完成后若出现新命中批次，可建后继范围。
            active_statuses = {"待确认", "已受理", "处理中", "部分失败"}
            existing = next((
                row for row in store.rows(self.MODULE)
                if row.get("criteria_key") == criteria_key and row.get("status") in active_statuses
            ), None)
        if existing is not None:
            receipt = self.receipt(existing.get("id"))
            return True, (
                f"相同筛选申请已受理：{existing.get('范围单号')} 为有效范围，"
                f"关联任务 {existing.get('关联重解析任务')}，未重复建单或排任务"
            ), receipt, True

        rows = store.rows(self.MODULE)
        scope_no = self._next_scope_no()
        scope_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        batch_labels = [str(item["batch_no"]) for item in items]
        rejected_labels = [str(item.get("batch_no") or item.get("item_key")) for item in unaccepted]
        scope: dict[str, Any] = {
            "id": scope_id,
            "status": "已受理",
            "pending": True,
            "abnormal": False,
            "范围单号": scope_no,
            "领取回执号": f"RCP-{scope_no}",
            "差异编号": criteria["diff_id"],
            "来源渠道": criteria["source"],
            "差异字段": criteria["field_name"],
            "旧表达式": criteria["old_expression"],
            "模板编号": criteria["template_id"],
            "模板版本": criteria["template_version"],
            "原筛选范围": (
                f"来源={criteria['source']}；字段={criteria['field_name']}；"
                f"旧表达式={criteria['old_expression']}；"
                f"模板={criteria['template_id']}/{criteria['template_version']}"
            ),
            "criteria": criteria,
            "criteria_key": criteria_key,
            "申请指纹": batch_fingerprint,
            "filter_snapshot": criteria,
            "approved_fields": [criteria["field_name"]],
            "approved_batches": batch_labels,
            "pending_batches": [str(item.get("batch_id")) for item in items],
            "template_snapshot": query["template_snapshot"],
            "核准批次": "、".join(batch_labels),
            "未受理批次": "、".join(x for x in rejected_labels if x),
            "幂等键": idempotency_key,
            "受理时间": _now(),
            "items": items,
            "unaccepted_items": unaccepted,
            "late_evidence": query["late_evidence"],
            "关联重解析任务": "",
        }
        rows.append(scope)
        task_no, task_message, task = self._create_reparse_task(scope)
        scope["关联重解析任务"] = task_no
        scope["task_message"] = task_message
        receipt = self.receipt(scope_id)
        return True, f"影响范围单 {scope_no} 已受理，{task_message}", receipt, False

    @staticmethod
    def _unaccepted_item(hit: dict[str, Any], reason: Any) -> dict[str, Any]:
        return {
            "item_key": hit.get("命中编号") or hit.get("批次编号"),
            "hit_id": hit.get("命中编号"),
            "batch_id": hit.get("批次编号"),
            "batch_short": hit.get("批次短号"),
            "batch_no": hit.get("展示批次") or hit.get("批次短号") or hit.get("批次编号"),
            "template_id": hit.get("模板编号"),
            "template_version": hit.get("模板版本"),
            "source": hit.get("来源渠道"),
            "field": hit.get("字段名称"),
            "status": "未受理",
            "reason": str(reason),
            "evidence_notes": [],
            "opened": False,
        }

    def _create_reparse_task(self, scope: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
        from app.services import service_for

        task_service = service_for("reparse_task")
        assert task_service is not None
        seq = int("".join(ch for ch in str(scope["范围单号"]) if ch.isdigit()) or "1")
        task_no = f"RT-IMP-{seq:03d}"
        batch_ids = [str(item["batch_id"]) for item in scope["items"] if item.get("batch_id")]
        positions: dict[str, str] = {}
        for batch_id in batch_ids:
            intake = next((row for row in store.rows("file_intake") if row.get("批次编号") == batch_id), None)
            if intake and intake.get("投递时间"):
                positions[batch_id] = str(intake["投递时间"])
            else:
                hit = next((row for row in self._hits() if row.get("批次编号") == batch_id), None)
                positions[batch_id] = str((hit or {}).get("命中时间") or _now())
        reason = (
            f"依据{scope['范围单号']}核准字段 {scope['差异字段']} 与待处理批次执行重解析；"
            f"批次={','.join(batch_ids)}；原筛选：{scope['原筛选范围']}"
        )
        ok, message, task = task_service.plan_position_range(  # type: ignore[attr-defined]
            task_no, reason, "旧投递保留", batch_ids, positions,
        )
        if not ok or task is None:
            # 兜底也要保证影响范围单有可执行名单，不能因通用位点规则阻断领取。
            rows = store.rows("reparse_task")
            task = {
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "status": "待执行", "pending": True, "abnormal": False,
                "重解析编号": task_no, "关联批次": batch_ids[0] if batch_ids else "",
                "重解析原因": reason, "覆盖策略": "旧投递保留",
                "起始位点": min(positions.values(), default=""),
                "截止位点": max(positions.values(), default=""),
                "受影响投递数": str(len(batch_ids)),
                "受影响批次": ",".join(batch_ids), "重解析结论": "",
            }
            rows.append(task)
            message = "已创建可执行重解析任务"

        # plan_position_range 返回的是浅拷贝；额外快照必须写回 store 中的任务行。
        stored_task = next(
            (row for row in store.rows("reparse_task") if row.get("重解析编号") == task_no),
            task,
        )
        stored_task.update({
            "范围单号": scope["范围单号"],
            "核准字段": scope["差异字段"],
            "核准字段清单": [scope["差异字段"]],
            "approved_fields": [scope["差异字段"]],
            "pending_batches": [item.get("batch_id") for item in scope["items"] if item.get("status") != "已完成"],
            "核准表达式": scope["旧表达式"],
            "模板编号": scope["模板编号"],
            "模板版本": scope["模板版本"],
            "filter_snapshot": scope.get("criteria"),
            "执行名单": scope["items"],
            "status": "待执行",
            "pending": True,
        })
        return task_no, message, stored_task

    def receipt(self, scope_ref: Any) -> dict[str, Any] | None:
        scope = self.find_scope(scope_ref)
        if scope is None:
            return None
        task_no = scope.get("关联重解析任务")
        task = next((row for row in store.rows("reparse_task") if row.get("重解析编号") == task_no), None)
        diff = self._find_diff(scope.get("差异编号"), scope.get("差异字段"))
        return {
            "ok": True,
            "receipt_no": scope.get("领取回执号"),
            "scope_no": scope.get("范围单号"),
            "scope": scope,
            "task": task,
            "diff": dict(diff) if diff else None,
            "items": scope.get("items") or [],
            "unaccepted_items": scope.get("unaccepted_items") or [],
            "trace": {
                "original_filter": scope.get("原筛选范围"),
                "criteria": scope.get("criteria"),
                "template_snapshot": scope.get("template_snapshot"),
                "diff_id": scope.get("差异编号"),
                "template_version": scope.get("模板版本"),
                "idempotency_key": scope.get("幂等键"),
            },
        }

    def open_unaccepted(self, scope_ref: Any, item_ref: Any) -> tuple[bool, str, dict[str, Any] | None]:
        scope = self.find_scope(scope_ref)
        if scope is None:
            return False, "影响范围单不存在", None
        item = self._find_unaccepted(scope, item_ref)
        if item is None:
            return False, "未受理项目不存在", self.receipt(scope.get("id"))
        item["opened"] = True
        item["opened_at"] = _now()
        return True, f"已打开未受理项目 {item.get('batch_no') or item.get('item_key')}", {
            "item": item, "receipt": self.receipt(scope.get("id")),
        }

    @staticmethod
    def _find_unaccepted(scope: dict[str, Any], ref: Any) -> dict[str, Any] | None:
        text = str(ref).strip().casefold()
        for item in scope.get("unaccepted_items") or []:
            values = [item.get("item_key"), item.get("hit_id"), item.get("batch_id"),
                      item.get("batch_short"), item.get("batch_no")]
            if any(text == str(v or "").casefold() or text in str(v or "").casefold() for v in values):
                return item
        return None

    @staticmethod
    def _find_item(scope: dict[str, Any], ref: Any) -> dict[str, Any] | None:
        text = str(ref).strip().casefold()
        for item in scope.get("items") or []:
            values = [item.get("item_id"), item.get("hit_id"), item.get("batch_id"),
                      item.get("batch_short"), item.get("batch_no")]
            if any(text == str(v or "").casefold() or text in str(v or "").casefold() for v in values):
                return item
        return None

    def supplement_unaccepted(
        self, scope_ref: Any, item_ref: Any, payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        data = payload or {}
        scope = self.find_scope(scope_ref)
        if scope is None:
            return False, "影响范围单不存在", None
        item = self._find_unaccepted(scope, item_ref)
        if item is None:
            return False, "未受理项目不存在", self.receipt(scope.get("id"))
        note = str(self._pick(data, "note", "证据说明", "evidence_note", default="运维补充说明")).strip()
        item.setdefault("evidence_notes", []).append({"note": note, "at": _now()})
        item["opened"] = True

        hit = next((row for row in self._hits() if _contains_identifier(row, item.get("hit_id") or item.get("batch_id"))), None)
        if hit is not None and "证据" in str(item.get("reason") or ""):
            hit["核准命中"] = "是"
            hit["证据状态"] = f"补充证据已采信：{note}"
        criteria = scope.get("criteria") or {}
        diff = self._find_diff(criteria.get("diff_id"), criteria.get("field_name"))
        now_eligible = bool(
            hit is not None
            and self._source_matches(hit.get("来源渠道"), criteria.get("source", ""))
            and self._approved_source_ok(hit, criteria, diff)
            and self._template_ok(hit, criteria, diff)
            and self._evidence_ok(hit)
        )
        if not now_eligible:
            if hit is not None:
                reasons = []
                if not self._approved_source_ok(hit, criteria, diff):
                    reasons.append("来源仍不在获准范围")
                if not self._template_ok(hit, criteria, diff):
                    reasons.append("模板版本仍不在获准范围")
                if not self._evidence_ok(hit):
                    reasons.append("真实使用证据仍不足")
                item["reason"] = "；".join(reasons) or item.get("reason")
                item["status"] = "仍不符合条件"
            return True, f"{item.get('batch_no')} 复核后仍不符合条件，不能带入修复", {
                "accepted": False, "item": item, "receipt": self.receipt(scope.get("id")),
            }

        new_item = {
            "item_id": len(scope.get("items") or []) + 1,
            "hit_id": hit.get("命中编号"), "batch_id": hit.get("批次编号"),
            "batch_short": hit.get("批次短号"), "batch_no": hit.get("展示批次"),
            "source": hit.get("来源渠道"), "field": hit.get("字段名称"),
            "approved_field": criteria.get("field_name"),
            "approved_expression": hit.get("旧表达式"),
            "template_id": hit.get("模板编号"), "template_version": hit.get("模板版本"),
            "late_evidence": True, "status": "待处理", "accepted": True,
            "attempts": 0, "error": "", "result": note,
        }
        scope.setdefault("items", []).append(new_item)
        scope["unaccepted_items"] = [x for x in scope.get("unaccepted_items") or [] if x is not item]
        self._sync_task_items(scope)
        return True, f"{new_item['batch_no']} 已补充受理为待处理项目，未被算作完成", {
            "accepted": True, "item": new_item, "receipt": self.receipt(scope.get("id")),
        }

    def _sync_task_items(self, scope: dict[str, Any]) -> None:
        task_no = scope.get("关联重解析任务")
        task = next((row for row in store.rows("reparse_task") if row.get("重解析编号") == task_no), None)
        if task is None:
            _, _, task = self._create_reparse_task(scope)
            scope["关联重解析任务"] = task.get("重解析编号") if task else ""
            return
        task["执行名单"] = scope.get("items") or []
        task["pending_batches"] = [
            item.get("batch_id") for item in scope.get("items") or []
            if item.get("status") != "已完成"
        ]
        batch_ids = [str(item.get("batch_id")) for item in scope.get("items") or [] if item.get("batch_id")]
        task["受影响批次"] = ",".join(batch_ids)
        task["受影响投递数"] = str(len(batch_ids))

    # ------------------------------------------------------------------
    # 执行、失败恢复、结果回写与下一次筛选
    # ------------------------------------------------------------------
    def execute_item(
        self, scope_ref: Any, batch_ref: Any, payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        data = payload or {}
        scope = self.find_scope(scope_ref)
        if scope is None:
            return False, "影响范围单不存在", None
        item = self._find_item(scope, batch_ref)
        if item is None:
            return False, f"执行名单中找不到批次 {batch_ref}", self.receipt(scope.get("id"))

        outcome = str(self._pick(data, "outcome", "result", "action", default="success")).lower()
        should_fail = outcome in {"fail", "failed", "failure", "error", "模拟失败", "处理失败"}
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["last_attempt_at"] = _now()
        if should_fail:
            item["status"] = "处理失败"
            item["error"] = str(data.get("error") or data.get("message") or f"批次 {item.get('batch_no')} 重解析处理失败，可从该批次恢复")
            scope["status"] = "部分失败" if any(x.get("status") == "已完成" for x in scope["items"]) else "处理失败"
            scope["pending"] = True
            self._sync_task_status_after_item(scope)
            self._sync_hit_and_diff(scope, item, ok=False, message=item["error"])
            return False, item["error"], self.receipt(scope.get("id"))

        item["status"] = "执行中"
        parse_batch = next((
            row for row in store.rows("parse_batch")
            if row.get("关联投递批次") == item.get("batch_id")
        ), None)
        row_count = (parse_batch or {}).get("成功行数") or (parse_batch or {}).get("总行数") or "0"
        item["status"] = "已完成"
        item["error"] = ""
        item["completed_at"] = _now()
        item["result"] = f"已按核准表达式重解析 {row_count} 行并同步差异服务"

        hit = next((row for row in self._hits() if _contains_identifier(row, item.get("hit_id") or item.get("batch_id"))), None)
        if hit is not None:
            hit["处理状态"] = "已完成"
            hit["status"] = "已完成"
            hit["pending"] = False
            hit["abnormal"] = False
            hit["completed_at"] = item["completed_at"]
        self._sync_hit_and_diff(scope, item, ok=True, message=item["result"])

        if all(x.get("status") == "已完成" for x in scope.get("items") or []):
            self._complete_task(scope)
            scope["status"] = "已完成"
            scope["pending"] = False
        else:
            scope["status"] = "处理中"
        self._sync_task_status_after_item(scope)
        receipt = self.receipt(scope.get("id"))
        if receipt is not None:
            receipt["next_claimable"] = self.next_claimable(scope.get("id"))
        return True, item["result"], receipt

    def _sync_hit_and_diff(self, scope: dict[str, Any], item: dict[str, Any], *, ok: bool, message: str) -> None:
        from app.services import service_for
        diff_service = service_for("tmpl_diff")
        if diff_service is not None:
            diff_service.sync_reparse_result(  # type: ignore[attr-defined]
                scope.get("差异编号"),
                str(item.get("batch_id") or item.get("batch_no")),
                str(item.get("approved_field") or scope.get("差异字段")),
                {"ok": ok, "message": message},
            )

    def _sync_task_status_after_item(self, scope: dict[str, Any]) -> None:
        task = next((
            row for row in store.rows("reparse_task")
            if row.get("重解析编号") == scope.get("关联重解析任务")
        ), None)
        if task is None:
            return
        statuses = [item.get("status") for item in scope.get("items") or []]
        if any(status == "处理失败" for status in statuses):
            task["status"] = "执行中"
            task["pending"] = True
            task["abnormal"] = True
        elif all(status == "已完成" for status in statuses):
            task["status"] = "已完成"
            task["pending"] = False
            task["abnormal"] = False
        else:
            task["status"] = "执行中"
            task["pending"] = True
        task["执行名单"] = scope.get("items") or []
        task["pending_batches"] = [
            item.get("batch_id") for item in scope.get("items") or []
            if item.get("status") != "已完成"
        ]

    def _complete_task(self, scope: dict[str, Any]) -> None:
        from app.services import service_for
        task_service = service_for("reparse_task")
        task_no = str(scope.get("关联重解析任务") or "")
        if task_service is None or not task_no:
            return
        task = task_service._find(task_no)  # type: ignore[attr-defined]
        if task is None or task.get("status") == "已完成":
            return
        if task.get("status") == "待执行":
            task_service.start_task(task_no)  # type: ignore[attr-defined]
        count = len(scope.get("items") or [])
        task_service.complete_task(  # type: ignore[attr-defined]
            task_no, count, f"影响范围单 {scope.get('范围单号')} 核准批次全部处理成功",
        )

    def execute_scope(self, scope_ref: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str, dict[str, Any] | None]:
        data = payload or {}
        fail_batch = self._pick(data, "fail_batch", "failed_batch", "失败批次", default="")
        scope = self.find_scope(scope_ref)
        if scope is None:
            return False, "影响范围单不存在", None
        results = []
        any_failure = False
        for item in list(scope.get("items") or []):
            if item.get("status") == "已完成":
                continue
            should_fail = bool(fail_batch and _contains_identifier(item, fail_batch))
            ok, message, receipt = self.execute_item(
                scope.get("id"), item.get("batch_id") or item.get("batch_short"),
                {"outcome": "fail" if should_fail else "success", "error": data.get("error")},
            )
            results.append({"batch": item.get("batch_no"), "ok": ok, "message": message})
            any_failure = any_failure or not ok
        receipt = self.receipt(scope.get("id"))
        if receipt is not None:
            receipt["execution_results"] = results
            receipt["next_claimable"] = self.next_claimable(scope.get("id"))
        if any_failure:
            return False, "本次执行名单存在失败批次，可从具体批次恢复", receipt
        return True, "本次执行名单已全部处理完成，结果已同步差异服务", receipt

    def retry_batch(self, scope_ref: Any, batch_ref: Any, payload: dict[str, Any] | None = None):
        data = dict(payload or {})
        data.setdefault("outcome", "success")
        return self.execute_item(scope_ref, batch_ref, data)

    def next_claimable(self, scope_ref: Any = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if payload:
            criteria_payload = payload
        elif scope_ref not in (None, ""):
            scope = self.find_scope(scope_ref)
            criteria_payload = (scope or {}).get("criteria") or {}
        else:
            criteria_payload = {}
        result = self.query_candidates(criteria_payload)
        return {
            "criteria": result["criteria"],
            "claimable": result["claimable"],
            "items": result["claimable"],
            "batches": result["claimable"],
            "claimable_batches": result["claimable"],
            "completed": result["completed"],
            "ineligible": result["ineligible"],
            "counts": result["counts"],
            "message": (
                f"下一次可领取 {len(result['claimable'])} 个批次"
                if result["claimable"] else "当前没有下一批符合获准范围且待处理的批次"
            ),
        }

    def resubmit_original(self, scope_ref: Any, payload: dict[str, Any] | None = None):
        scope = self.find_scope(scope_ref)
        if scope is None:
            return False, "原影响范围单不存在", None, False
        # 原范围重交是幂等回执重放：即使批次已经完成，也不能把它当新候选再排任务。
        receipt = self.receipt(scope.get("id"))
        message = (
            f"重交原范围：{scope.get('范围单号')} 已存在，"
            f"关联任务 {scope.get('关联重解析任务')} 不重复排单"
        )
        return True, message, receipt, True

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        scope = self.find_scope(entry_id)
        if scope is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        if action in {"查看回执", "重交原范围"}:
            if action == "重交原范围":
                _, message, _, _ = self.resubmit_original(entry_id)
                return scope, message
            return scope, f"已打开{scope.get('范围单号')}领取回执"
        if action in {"执行全部", "执行本次名单"}:
            ok, message, _ = self.execute_scope(entry_id)
            return scope, message
        if action == "恢复失败批次":
            failed = next((item for item in scope.get("items") or [] if item.get("status") == "处理失败"), None)
            if failed is None:
                return scope, "没有需要恢复的失败批次"
            ok, message, _ = self.retry_batch(entry_id, failed.get("batch_id"))
            return scope, message
        return super().run_action(entry_id, action)


class ExpressionHitService(BaseModuleService):
    MODULE = "expression_hit"
    ENTITY = "表达式真实命中记录"
    LIST_FIELDS = [
        "命中编号", "来源渠道", "批次编号", "批次短号", "展示批次", "模板编号",
        "模板版本", "字段名称", "旧表达式", "命中凭证", "证据状态", "核准命中",
        "处理状态", "是否迟到证据", "命中时间",
    ]
    REQUIRED_FIELDS = ["命中编号", "来源渠道", "批次编号", "字段名称", "旧表达式"]
    STATUS_ORDER = ["待处理", "待补证", "已完成", "不符合条件"]
    ACTION_RULES = {"标记完成": "已完成", "标记不符合": "不符合条件"}


class SourceEvidenceService(BaseModuleService):
    MODULE = "source_evidence"
    ENTITY = "源证据"
    LIST_FIELDS = [
        "证据编号", "来源渠道", "批次编号", "批次短号", "证据类型",
        "证据说明", "送达状态", "登记时间",
    ]
    REQUIRED_FIELDS = ["证据编号", "来源渠道", "批次编号"]
    STATUS_ORDER = ["待送达", "已核查"]
    ACTION_RULES = {"采信证据": "已核查"}


# 常见命名别名；不要加入 SERVICES 字典，否则通用快照测试会要求别名 key 与 MODULE 相同。
ImpactOrderService = ImpactScopeService
ImpactScopeOrderService = ImpactScopeService
AffectedScopeService = ImpactScopeService
AffectedBatchService = ImpactScopeService
