"""Translate persisted proxy state into safe workspace views."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from telegram_connector.proxies import ProxyConfig

from app.modules.shared.commands import TenantContext

from .models import ProxyView


class ProxyWorkspaceRepository(Protocol):
    async def list_for_organization(self, organization_id: UUID) -> tuple[dict[str, object], ...]: ...

    async def put_proxy(
        self, proxy: ProxyConfig, *, default: bool, organization_id: UUID
    ) -> None: ...


class WorkspaceProxyService:
    """Owner-facing proxy listing; credentials cannot cross this boundary."""

    def __init__(self, repository: ProxyWorkspaceRepository) -> None:
        self._repository = repository

    async def list(self, principal: TenantContext) -> tuple[ProxyView, ...]:
        if "company_owner" not in principal.roles:
            raise PermissionError("company owner required")
        rows = await self._repository.list_for_organization(principal.organization_id)
        return tuple(
            ProxyView(
                proxy_id=row["proxy_id"],
                endpoint=row["endpoint"],
                protocol=str(row["endpoint"]).split(":", 1)[0],
                capacity=row["capacity"],
                is_default=row["is_default"],
                assignment_count=row["assignment_count"],
                health=row["health"],
            )
            for row in rows
        )

    async def create(
        self, principal: TenantContext, url: str, capacity: int, default: bool
    ) -> ProxyView:
        self._require_owner(principal)
        proxy = ProxyConfig(proxy_id=uuid4(), url=url, capacity=capacity)
        await self._repository.put_proxy(
            proxy, default=default, organization_id=principal.organization_id
        )
        return ProxyView(
            proxy_id=proxy.proxy_id,
            endpoint=proxy.endpoint,
            protocol=proxy.endpoint.split(":", 1)[0],
            capacity=proxy.capacity,
            is_default=default,
            assignment_count=0,
            health="awaiting_check",
        )

    @staticmethod
    def _require_owner(principal: TenantContext) -> None:
        if "company_owner" not in principal.roles:
            raise PermissionError("company owner required")
