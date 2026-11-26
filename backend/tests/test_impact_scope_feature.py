"""差异影响范围受理、幂等与批次恢复的场景测试。"""
from __future__ import annotations

import pytest

from app.services import SERVICES
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store.reload_seed()
    yield store
    store.reload_seed()


def impact():
    return SERVICES["impact_scope"]


def test_initial_search_only_accepts_jia_007_and_keeps_bing_rejected():
    result = impact().query_candidates({"source": "甲", "old_expression": "amount"})
    assert [row["批次短号"] for row in result["candidates"]] == ["007"]
    assert [row["批次短号"] for row in result["ineligible"]] == ["丙批"]
    assert result["late_evidence"]["applied"] is False


def test_late_evidence_expands_008_as_pending_not_completed():
    svc = impact()
    ok, _message, result = svc.receive_late_evidence({"batch": "008", "note": "late log"})
    assert ok is True
    assert [row["批次短号"] for row in result["candidates"]] == ["007", "008"]
    hit_008 = next(row for row in store.rows("expression_hit") if row["批次短号"] == "008")
    assert hit_008["处理状态"] == "待处理"
    assert hit_008["status"] == "待处理"


def test_application_is_idempotent_and_traces_filter_and_template():
    svc = impact()
    svc.receive_late_evidence({"batch": "008"})
    payload = {"source": "甲来源", "old_expression": "amount", "request_key": "same-request"}
    ok, _msg, receipt, duplicate = svc.submit_application(payload)
    assert ok is True and duplicate is False
    assert [item["batch_short"] for item in receipt["items"]] == ["007", "008"]
    assert [item["batch_short"] for item in receipt["unaccepted_items"]] == ["丙批"]
    assert "甲来源" in receipt["trace"]["original_filter"]
    assert receipt["trace"]["template_version"] == "v1.4.0"
    assert receipt["task"]["核准字段清单"] == ["amount"]

    ok, _msg, receipt2, duplicate2 = svc.submit_application(payload)
    assert ok is True and duplicate2 is True
    assert receipt2["scope_no"] == receipt["scope_no"]
    scopes = [row for row in store.rows("impact_scope") if row.get("幂等键") == "same-request"]
    assert len(scopes) == 1


def test_batch_failure_recovers_without_duplicating_task():
    svc = impact()
    svc.receive_late_evidence({"batch": "008"})
    _, _, receipt, _ = svc.submit_application({"source": "甲", "request_key": "k"})
    scope_id = receipt["scope"]["id"]

    ok, _msg, failed = svc.execute_scope(scope_id, {"fail_batch": "007"})
    assert ok is False
    statuses = {item["batch_short"]: item["status"] for item in failed["items"]}
    assert statuses == {"007": "处理失败", "008": "已完成"}

    ok, _msg, recovered = svc.retry_batch(scope_id, "007")
    assert ok is True
    assert all(item["status"] == "已完成" for item in recovered["items"])
    assert recovered["task"]["status"] == "已完成"
    assert recovered["next_claimable"]["counts"]["claimable"] == 0

    diff = next(row for row in store.rows("tmpl_diff") if row["差异编号"] == "TD-AMOUNT")
    assert "FI-JIA-007" in diff["重解析回写记录"]
    assert diff["已完成批次数"] == "2"


def test_bing_cannot_be_supplemented_into_approved_scope():
    svc = impact()
    svc.receive_late_evidence({"batch": "008"})
    _, _, receipt, _ = svc.submit_application({"source": "甲", "request_key": "k"})
    scope_id = receipt["scope"]["id"]
    ok, _msg, result = svc.supplement_unaccepted(
        scope_id, "丙批", {"note": "someone claims same field name"},
    )
    assert ok is True
    assert result["accepted"] is False
    assert "仍不符合条件" in result["item"]["status"]
    assert len(receipt["scope"]["items"]) == 2
