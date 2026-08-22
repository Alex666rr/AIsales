"""Session-authenticated routes for redacted proxy workspace data."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.shared.commands import TenantContext

from .models import ProxyView


WorkspacePrincipalDependency = Callable[[], Awaitable[TenantContext]]


class WorkspaceProxyRoutes(Protocol):
    async def list(self, principal: TenantContext) -> tuple[ProxyView, ...]: ...

    async def create(self, principal: TenantContext, url: str, capacity: int, default: bool) -> ProxyView: ...


class ProxyCreateRequest(BaseModel):
    """Credential-bearing input is accepted once and never rendered back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1, max_length=1024, repr=False)
    capacity: int = Field(default=1, ge=1, le=5)
    default: bool = False


def build_workspace_proxy_router(
    proxies: WorkspaceProxyRoutes,
    *,
    principal_dependency: WorkspacePrincipalDependency,
) -> APIRouter:
    """Expose only redacted proxy health to one server-authenticated workspace."""
    router = APIRouter(prefix="/workspace/telegram/proxies", tags=["workspace-telegram-proxies"])

    @router.get("", response_model=tuple[ProxyView, ...])
    async def list_proxies(
        principal: TenantContext = Depends(principal_dependency),
    ) -> tuple[ProxyView, ...]:
        try:
            return await proxies.list(principal)
        except PermissionError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="company owner required") from None
        except Exception:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="proxy workspace unavailable") from None

    @router.post("", response_model=ProxyView, status_code=status.HTTP_201_CREATED)
    async def create_proxy(
        request: ProxyCreateRequest,
        principal: TenantContext = Depends(principal_dependency),
    ) -> ProxyView:
        try:
            return await proxies.create(principal, request.url, request.capacity, request.default)
        except PermissionError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="company owner required") from None
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="proxy configuration rejected") from None
        except Exception:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="proxy workspace unavailable") from None

    return router
