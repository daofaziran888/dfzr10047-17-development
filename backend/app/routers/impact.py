"""差异影响范围工作流接口。

除了语义化 REST 路径，也保留下划线/连字符、单数/复数别名，方便不同调用方接入。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.services import service_for
from app.store import store

router = APIRouter(tags=["差异影响范围"])

PREFIXES = (
    "/api/impact-scope",
    "/api/impact_scope",
    "/api/impact-scopes",
    "/api/impact_scopes",
    "/api/impact-order",
    "/api/impact_order",
    "/api/impact-orders",
    "/api/impact_orders",
    "/api/affected-batch",
    "/api/affected_batch",
    "/api/affected-batches",
    "/api/affected_batches",
    "/api/tmpl_diff/impact",
    "/api/diff-impact",
    "/api/diff-impacts",
)


def _service():
    svc = service_for("impact_scope")
    assert svc is not None
    return svc


async def _body(request: Request) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                data.update(parsed)
                if isinstance(parsed.get("values"), dict):
                    data.update(parsed["values"])
        except Exception:
            pass
    data.update({key: value for key, value in request.query_params.items()})
    return data


def _wrap(ok: bool, message: str, result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        # 业务裁决的 ok/message 必须位于展开之后，避免 receipt 内的 ok=True 覆盖失败状态。
        return {**result, "ok": ok, "message": message}
    return {"ok": ok, "message": message, "result": result}


async def query_impact(request: Request) -> dict[str, Any]:
    return _service().query_candidates(await _body(request))


async def late_evidence(request: Request) -> dict[str, Any]:
    ok, message, result = _service().receive_late_evidence(await _body(request))
    return _wrap(ok, message, result)


async def apply_impact(request: Request) -> dict[str, Any]:
    body = await _body(request)
    header_key = request.headers.get("idempotency-key") or request.headers.get("x-request-id")
    if header_key and not body.get("request_key"):
        body["request_key"] = header_key
    ok, message, receipt, duplicate = _service().submit_application(body)
    response = _wrap(ok, message, receipt)
    response["duplicate"] = duplicate
    return response


async def receipt(request: Request, scope_ref: str) -> dict[str, Any]:
    result = _service().receipt(scope_ref)
    if result is None:
        raise HTTPException(status_code=404, detail=f"影响范围单 {scope_ref} 不存在")
    return result


async def next_claimable(request: Request, scope_ref: str | None = None) -> dict[str, Any]:
    body = await _body(request)
    return _service().next_claimable(scope_ref, body or None)


async def execute_all(request: Request, scope_ref: str) -> dict[str, Any]:
    ok, message, result = _service().execute_scope(scope_ref, await _body(request))
    return _wrap(ok, message, result)


async def resubmit(request: Request, scope_ref: str) -> dict[str, Any]:
    ok, message, result, duplicate = _service().resubmit_original(scope_ref, await _body(request))
    response = _wrap(ok, message, result)
    response["duplicate"] = duplicate
    return response


async def unaccepted_open(request: Request, scope_ref: str, item_ref: str) -> dict[str, Any]:
    ok, message, result = _service().open_unaccepted(scope_ref, item_ref)
    return _wrap(ok, message, result)


async def unaccepted_supplement(request: Request, scope_ref: str, item_ref: str) -> dict[str, Any]:
    ok, message, result = _service().supplement_unaccepted(scope_ref, item_ref, await _body(request))
    return _wrap(ok, message, result)


async def item_execute(request: Request, scope_ref: str, item_ref: str, action: str) -> dict[str, Any]:
    body = await _body(request)
    if action in {"fail", "failure", "simulate-failure", "模拟失败"}:
        body["outcome"] = "fail"
    elif action in {"retry", "recover", "recovery", "恢复", "retry-batch", "recover-batch"}:
        body["outcome"] = "success"
    elif action in {"execute", "run", "process"}:
        body.setdefault("outcome", "success")
    ok, message, result = _service().execute_item(scope_ref, item_ref, body)
    return _wrap(ok, message, result)


async def reset_state() -> dict[str, Any]:
    store.reload_seed()
    return {"ok": True, "message": "示例状态已重置"}


# 固定路径必须先于 /{scope_ref} 形式注册。
for prefix in PREFIXES:
    for path in ("/query", "/candidates", "/search"):
        router.add_api_route(prefix + path, query_impact, methods=["GET", "POST"])
    for path in ("/late-evidence", "/late_evidence", "/evidence/late", "/evidence"):
        router.add_api_route(prefix + path, late_evidence, methods=["POST"])
    for path in ("/apply", "/applications", "/submit", "/claim", "/"):
        router.add_api_route(prefix + path, apply_impact, methods=["POST"])
    router.add_api_route(prefix + "/next", next_claimable, methods=["GET", "POST"])

    router.add_api_route(prefix + "/{scope_ref}/receipt", receipt, methods=["GET"])
    router.add_api_route(prefix + "/{scope_ref}", receipt, methods=["GET"])
    router.add_api_route(prefix + "/{scope_ref}/next", next_claimable, methods=["GET", "POST"])
    for path in ("/execute", "/execute-all", "/run-all"):
        router.add_api_route(prefix + "/{scope_ref}" + path, execute_all, methods=["POST"])
    for path in ("/resubmit", "/reapply", "/duplicate"):
        router.add_api_route(prefix + "/{scope_ref}" + path, resubmit, methods=["POST"])

    for open_path in (
        "/{scope_ref}/unaccepted/{item_ref}/open",
        "/{scope_ref}/items/{item_ref}/open",
        "/{scope_ref}/unaccepted/{item_ref}",
    ):
        router.add_api_route(prefix + open_path, unaccepted_open, methods=["POST", "GET"])
    for supp_path in (
        "/{scope_ref}/unaccepted/{item_ref}/supplement",
        "/{scope_ref}/unaccepted/{item_ref}/evidence",
        "/{scope_ref}/items/{item_ref}/supplement",
        "/{scope_ref}/items/{item_ref}/evidence",
    ):
        router.add_api_route(prefix + supp_path, unaccepted_supplement, methods=["POST"])
    router.add_api_route(
        prefix + "/{scope_ref}/items/{item_ref}/{action}",
        item_execute, methods=["POST"],
    )
    router.add_api_route(
        prefix + "/{scope_ref}/batches/{item_ref}/{action}",
        item_execute, methods=["POST"],
    )

async def diff_scoped_query(request: Request, diff_ref: str) -> dict[str, Any]:
    body = await _body(request)
    body["diff_id"] = diff_ref
    return _service().query_candidates(body)


async def diff_scoped_apply(request: Request, diff_ref: str) -> dict[str, Any]:
    body = await _body(request)
    body["diff_id"] = diff_ref
    ok, message, result, duplicate = _service().submit_application(body)
    response = _wrap(ok, message, result)
    response["duplicate"] = duplicate
    return response


async def task_batch_action(
    request: Request, task_ref: str, item_ref: str, action: str,
) -> dict[str, Any]:
    scope = _service().find_scope(task_ref)
    scope_ref = scope.get("id") if scope else task_ref
    return await item_execute(request, str(scope_ref), item_ref, action)


for path in ("/api/tmpl_diff/{diff_ref}/impact", "/api/tmpl_diff/{diff_ref}/impact/query",
             "/api/tmpl_diff/{diff_ref}/candidates", "/api/tmpl_diff/{diff_ref}/affected-batches"):
    router.add_api_route(path, diff_scoped_query, methods=["GET", "POST"])
for path in ("/api/tmpl_diff/{diff_ref}/impact/apply", "/api/tmpl_diff/{diff_ref}/claim",
             "/api/tmpl_diff/{diff_ref}/applications"):
    router.add_api_route(path, diff_scoped_apply, methods=["POST"])

for prefix in ("/api/reparse_task", "/api/reparse-tasks"):
    router.add_api_route(
        prefix + "/{task_ref}/batches/{item_ref}/{action}", task_batch_action, methods=["POST"],
    )
    router.add_api_route(
        prefix + "/{task_ref}/items/{item_ref}/{action}", task_batch_action, methods=["POST"],
    )

for reset_path in ("/api/reset", "/api/test/reset", "/api/reset-seed", "/api/seed/reset"):
    router.add_api_route(reset_path, reset_state, methods=["POST", "GET"])
