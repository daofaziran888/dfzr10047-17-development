"""模板差异领域服务：逐字段比对模板新旧版本抽取表达式变更，产出差异条目数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TmplDiffService(BaseModuleService):
    MODULE = "tmpl_diff"
    ENTITY = "差异条目"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["差异编号", "旧模板版本", "新模板版本", "差异字段", "差异类别", "影响批次数", "修正方案", "登记时间"]
    REQUIRED_FIELDS = ["差异编号", "旧模板版本", "新模板版本"]
    STATUS_ORDER = ["待比对", "比对中", "已收敛", "偏离超限"]
    ACTION_RULES = {"启动比对": "比对中", "确认收敛": "已收敛", "标记超限": "偏离超限"}
    NEGATIVE_ACTIONS = ["标记超限"]

    # 差异字段数超过该上限时判定偏离超限，必须分批重解析而非一次性覆盖
    MAX_SINGLE_PASS_FIELDS = 6

    def _find_tmpl(self, version_no: str) -> dict[str, Any] | None:
        for row in store.rows("extract_tmpl"):
            if row.get("版本号") == version_no:
                return row
        return None

    def compare_versions(self, diff_no: str, old_version: str, new_version: str,
                         old_fields: list[str], new_fields: list[str],
                         changed_fields: list[str], affected_batches: int,
                         register_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """逐字段比对新旧版本，按差异字段数判定收敛或偏离超限。

        差异条目记录新增/删除/变更字段清单，影响批次数决定重解析位点范围。
        """
        old_tmpl = self._find_tmpl(old_version)
        new_tmpl = self._find_tmpl(new_version)
        if old_tmpl is None or new_tmpl is None:
            missing = old_version if old_tmpl is None else new_version
            return False, f"模板版本 {missing} 在抽取模板中不存在，无法比对", None

        added = sorted(set(new_fields) - set(old_fields))
        removed = sorted(set(old_fields) - set(new_fields))
        changed = sorted(set(changed_fields) & (set(old_fields) & set(new_fields)))
        diff_count = len(added) + len(removed) + len(changed)

        categories: list[str] = []
        if added:
            categories.append(f"新增{len(added)}")
        if removed:
            categories.append(f"删除{len(removed)}")
        if changed:
            categories.append(f"变更{len(changed)}")

        converged = diff_count <= self.MAX_SINGLE_PASS_FIELDS
        status = "已收敛" if converged else "偏离超限"
        if diff_count == 0:
            plan = "新旧版本抽取表达式一致，无需重解析"
        elif converged:
            plan = f"差异 {diff_count} 个字段在单趟上限内，按位点范围一次性重解析 {affected_batches} 个批次"
        else:
            chunks = -(-diff_count // self.MAX_SINGLE_PASS_FIELDS)
            plan = (
                f"差异 {diff_count} 个字段超过单趟上限 {self.MAX_SINGLE_PASS_FIELDS}，"
                f"影响 {affected_batches} 个批次须分 {chunks} 批重解析，禁止一次性覆盖"
            )

        rows = store.rows(self.MODULE)
        target = next((r for r in rows if r.get("差异编号") == diff_no), None)
        if target is None:
            entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            target = {"id": entry_id, "差异编号": diff_no}
            rows.append(target)
        target.update({
            "旧模板版本": old_version,
            "新模板版本": new_version,
            "差异字段": ",".join(added + removed + changed),
            "差异类别": ",".join(categories) or "无差异",
            "影响批次数": str(affected_batches),
            "修正方案": plan,
            "登记时间": register_time,
            "新增字段": ",".join(added),
            "删除字段": ",".join(removed),
            "变更字段": ",".join(changed),
            "差异字段数": str(diff_count),
            "status": status,
            "pending": not converged,
            "abnormal": not converged,
        })
        return converged, (
            f"模板 {old_version}→{new_version} 差异 {diff_count} 个字段，"
            f"判定{status}：{plan}"
        ), dict(target)

    def affected_batches_of(self, diff_no: str) -> int:
        """输出差异条目影响批次数，供重解析位点范围规划消费。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") == diff_no:
                return self._int_value(row.get("影响批次数"))
        return 0

    @staticmethod
    def _int_value(value: Any) -> int:
        text = str(value or "").strip()
        return int(text) if text.isdigit() else 0

    def diff_fields_of(self, diff_no: str) -> list[str]:
        """输出差异字段清单，供重解析时确定需要重新抽取的字段。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") == diff_no:
                return [f.strip() for f in str(row.get("差异字段") or "").split(",") if f.strip()]
        return []

    def requires_split_reparse(self, diff_no: str) -> tuple[bool, int]:
        """判断该差异是否必须分批重解析，返回 (是否分批, 建议分批数)。"""
        for row in store.rows(self.MODULE):
            if row.get("差异编号") != diff_no:
                continue
            diff_count = self._int_value(row.get("差异字段数"))
            if diff_count <= self.MAX_SINGLE_PASS_FIELDS:
                return False, 1
            return True, -(-diff_count // self.MAX_SINGLE_PASS_FIELDS)
        return False, 1

    def unresolved_diffs(self) -> list[dict[str, Any]]:
        """列出偏离超限未收敛的差异条目，供治理看板消费。"""
        return [
            {
                "差异编号": r.get("差异编号"),
                "旧模板版本": r.get("旧模板版本"),
                "新模板版本": r.get("新模板版本"),
                "差异字段": r.get("差异字段"),
                "差异字段数": r.get("差异字段数"),
                "影响批次数": r.get("影响批次数"),
                "status": r.get("status"),
            }
            for r in store.rows(self.MODULE)
            if r.get("status") == "偏离超限"
        ]

    def sync_reparse_result(
        self,
        diff_ref: str | int | None,
        batch_no: str,
        field_name: str,
        result: dict[str, Any] | str,
    ) -> dict[str, Any] | None:
        """接收重解析实际结果，更新差异条目的下一次筛选水位。

        这里只认真实批次执行结果；补录证据形成的候选在执行成功前仍保持待处理，
        因而不会被误算成已完成。
        """
        diff: dict[str, Any] | None = None
        if diff_ref not in (None, ""):
            if isinstance(diff_ref, int) or str(diff_ref).isdigit():
                diff = store.find(self.MODULE, int(diff_ref))
            if diff is None:
                diff = next(
                    (row for row in store.rows(self.MODULE)
                     if str(row.get("差异编号")) == str(diff_ref)),
                    None,
                )
        if diff is None and field_name:
            diff = next(
                (row for row in store.rows(self.MODULE)
                 if field_name in str(row.get("差异字段") or "").split(",")),
                None,
            )
        if diff is None:
            return None

        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        state = "成功" if ok else "失败"
        history = [
            line for line in str(diff.get("重解析回写记录") or "").split(" | ")
            if line
        ]
        history.append(f"{batch_no}:{field_name}:{state}")
        diff["重解析回写记录"] = " | ".join(history[-50:])
        diff["最近同步批次"] = batch_no
        diff["最近同步字段"] = field_name
        diff["最近同步结果"] = (
            result.get("message") if isinstance(result, dict) else str(result)
        ) or f"{batch_no} {state}"

        completed = {
            str(hit.get("批次编号"))
            for hit in store.rows("expression_hit")
            if str(hit.get("字段名称")) == str(diff.get("差异字段") or field_name)
            and hit.get("处理状态") == "已完成"
        }
        pending = {
            str(hit.get("批次编号"))
            for hit in store.rows("expression_hit")
            if str(hit.get("字段名称")) == str(diff.get("差异字段") or field_name)
            and hit.get("处理状态") not in {"已完成", "不予受理"}
            and hit.get("核准命中") == "是"
        }
        diff["已完成批次数"] = str(len(completed))
        diff["待处理批次数"] = str(len(pending))
        return diff

    def impact_candidates(
        self,
        source: str = "",
        old_expression: str = "",
        diff_no: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """转发到影响范围服务，便于从模板差异上下文直接筛选真实命中批次。"""
        from app.services import service_for

        impact = service_for("impact_scope")
        assert impact is not None
        payload = {
            "source": source or kwargs.get("来源渠道") or "甲来源",
            "old_expression": old_expression or kwargs.get("旧表达式") or "",
            "diff_id": diff_no or kwargs.get("diff_id") or kwargs.get("差异编号"),
            **kwargs,
        }
        return impact.query_candidates(payload)  # type: ignore[attr-defined]

    query_affected_batches = impact_candidates
    filter_affected_batches = impact_candidates

    def claim_impact_batches(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None, bool]:
        """从模板差异上下文提交影响范围领取申请。"""
        from app.services import service_for

        impact = service_for("impact_scope")
        assert impact is not None
        return impact.submit_application(payload)  # type: ignore[attr-defined]
