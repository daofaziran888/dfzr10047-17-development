"""路由汇总：差异影响范围工作流先于通用 /api/{module} 动态路由注册。"""
from __future__ import annotations

from app.routers import impact, resource

ROUTERS = [impact, resource]
