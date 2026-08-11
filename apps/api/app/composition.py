"""Production-only dependency composition for the Stage 0 control API."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated, Callable, cast
from uuid import UUID

from fastapi import Header, HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from telegram_connector.adapters import (
    AdapterRegistry,
    BotAdapter,
    DefaultDenyTDataConverter,
    PhoneAdapter,
    QRAdapter,
    TDataAdapter,
    TelegramBotApiClient,
    TelethonAuthorizationClientFactory,
    TelethonFileAdapter,
    TelethonRuntimeClientFactory,
    TelethonStringAdapter,
    VettedTelethonSessionConverter,
)
from telegram_connector.config import decode_session_encryption_key
from telegram_connector.gateway import _InboundDecoder, _compose_registered_gateway
from telegram_connector.persistence import (
    ProxyCredentialCipher,
    SqlAlchemyCiphertextSessionRepository,
    SqlAlchemyCompatibilityRegistry,
    SqlAlchemyConnectionRepository,
    SqlAlchemyMessageDeliveryRepository,
    SqlAlchemyProxyAssignmentRepository,
    SqlAlchemyTelegramAccountRepository,
)
from telegram_connector.session_store import EncryptedSessionStore

from .config import ApiSettings
from .modules.policy.models import (
    AiOperation,
    AiOperationContext,
    ChannelType,
    DataCategory,
    PlatformOwnerPrincipal,
    TermsRevision,
)
from .modules.policy.repository import (
    SqlAlchemyApprovalRepository,
    _SqlAlchemyApprovalWriter,
)
from .modules.policy.routes import build_policy_router
from .modules.policy.service import (
    ApprovalAdministrationService,
    PlatformOwnerAuthority,
    PolicyAuthorizationError,
    PolicyContextAuthority,
    PolicyGate,
    PolicyProtectedMessageLoader,
)
from .modules.telegram_connections.routes import build_connection_router
from .modules.telegram_connections.service import (
    ConnectionAttemptService,
    PhoneAttemptAdapter,
    QrAttemptAdapter,
)


REQUIRED_SCHEMA_REVISIONS = frozenset({"0004_telegram_identity"})


class TelegramPolicyContextIssuer:
    """Issue real-Telegram policy contexts from authoritative persisted ownership."""

    def __init__(
        self,
        account_repository: SqlAlchemyTelegramAccountRepository,
        context_authority: PolicyContextAuthority,
    ) -> None:
        self._accounts = account_repository
        self._authority = context_authority

    async def real_telegram(
        self,
        *,
        account_id: UUID,
        channel_type: ChannelType,
        data_category: DataCategory,
        operation: AiOperation,
    ) -> AiOperationContext:
        organization_id = await self._accounts.organization_for_async(account_id)
        if organization_id is None:
            raise PolicyAuthorizationError("account ownership unavailable")
        return self._authority.real_telegram(
            organization_id=organization_id,
            channel_type=channel_type,
            data_category=data_category,
            operation=operation,
        )


class OwnerBearerAuthenticator:
    """Authenticate the one configured owner and mint a server-side capability."""

    def __init__(
        self,
        authority: PlatformOwnerAuthority,
        *,
        owner_id: UUID,
        bearer_token: str,
    ) -> None:
        if not bearer_token:
            raise ValueError("platform owner bearer token is required")
        self._authority = authority
        self._owner_id = owner_id
        self._bearer_token = bearer_token

    async def __call__(
        self,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> PlatformOwnerPrincipal:
        scheme, separator, supplied = (authorization or "").partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not supplied
            or not hmac.compare_digest(supplied, self._bearer_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return self._authority.issue(self._owner_id)


class ComposedGatewayFactory:
    """Bind every concrete gateway to durable delivery and compatibility repositories."""

    def __init__(self, repository, compatibility) -> None:
        self._repository = repository
        self._compatibility = compatibility

    def create(
        self,
        *,
        client: object,
        proxy_id: UUID | None,
        connection_is_active: Callable[[], bool],
    ):
        return _compose_registered_gateway(
            adapter=client,
            repository=self._repository,
            compatibility=self._compatibility,
            proxy_id=proxy_id,
            connection_is_active=connection_is_active,
        )


@dataclass(frozen=True)
class ApplicationComposition:
    """All production services; every mutable connector boundary is PostgreSQL-backed."""

    account_repository: SqlAlchemyTelegramAccountRepository
    session_store: EncryptedSessionStore
    connection_repository: SqlAlchemyConnectionRepository
    proxy_repository: SqlAlchemyProxyAssignmentRepository
    gateway_repository: SqlAlchemyMessageDeliveryRepository
    compatibility_repository: SqlAlchemyCompatibilityRegistry
    gateway_factory: ComposedGatewayFactory
    telegram_client_factory: TelethonRuntimeClientFactory
    adapter_registry: AdapterRegistry
    policy_context_issuer: TelegramPolicyContextIssuer
    policy_gate: PolicyGate
    protected_message_loader: PolicyProtectedMessageLoader
    policy_router: object
    connection_router: object
    sync_engine: Engine | None = None
    async_engine: AsyncEngine | None = None

    def database_is_ready(self) -> bool:
        """Require a reachable database whose Alembic revision is current."""
        if self.sync_engine is None:
            return False
        with self.sync_engine.connect() as connection:
            revisions = set(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )
        return revisions == REQUIRED_SCHEMA_REVISIONS

    async def close(self) -> None:
        if self.async_engine is not None:
            await self.async_engine.dispose()
        if self.sync_engine is not None:
            self.sync_engine.dispose()


def build_application_composition(
    settings: ApiSettings,
    *,
    sync_sessions: sessionmaker[Session],
    async_sessions: async_sessionmaker[AsyncSession],
    sync_engine: Engine | None = None,
    async_engine: AsyncEngine | None = None,
) -> ApplicationComposition:
    """Bind reviewed concrete services; tests inject only persistence/client boundaries."""
    key = decode_session_encryption_key(settings.session_encryption_key.get_secret_value())
    key_ring = {settings.session_encryption_key_version: key}
    account_repository = SqlAlchemyTelegramAccountRepository(sync_sessions)
    session_store = EncryptedSessionStore(
        key_ring,
        active_key_version=settings.session_encryption_key_version,
        repository=SqlAlchemyCiphertextSessionRepository(sync_sessions),
    )
    connection_repository = SqlAlchemyConnectionRepository(sync_sessions)
    proxy_repository = SqlAlchemyProxyAssignmentRepository(
        sync_sessions,
        credential_cipher=ProxyCredentialCipher(
            key_ring,
            active_key_version=settings.session_encryption_key_version,
        ),
    )
    gateway_repository = SqlAlchemyMessageDeliveryRepository(sync_sessions)
    compatibility_repository = SqlAlchemyCompatibilityRegistry(sync_sessions)
    gateway_factory = ComposedGatewayFactory(gateway_repository, compatibility_repository)

    api_hash = settings.telegram_api_hash.get_secret_value()
    authorization_clients = TelethonAuthorizationClientFactory(
        settings.telegram_api_id, api_hash
    )
    converter = VettedTelethonSessionConverter()
    adapter_registry = AdapterRegistry(
        phone=PhoneAdapter(authorization_clients.phone),
        qr=QRAdapter(authorization_clients.qr),
        tdata=TDataAdapter(DefaultDenyTDataConverter()),
        telethon_file=TelethonFileAdapter(converter),
        telethon_string=TelethonStringAdapter(converter),
        bot=BotAdapter(TelegramBotApiClient()),
    )
    telegram_client_factory = TelethonRuntimeClientFactory(
        session_store,
        api_id=settings.telegram_api_id,
        api_hash=api_hash,
    )

    context_authority = PolicyContextAuthority()
    policy_repository = SqlAlchemyApprovalRepository(async_sessions)
    policy_gate = PolicyGate(
        repository=policy_repository,
        context_authority=context_authority,
        current_terms_revision=TermsRevision(settings.current_terms_revision),
    )
    protected_loader = PolicyProtectedMessageLoader(policy_gate)
    owner_authority = PlatformOwnerAuthority()
    administration = ApprovalAdministrationService(
        writer=_SqlAlchemyApprovalWriter(async_sessions),
        owner_authority=owner_authority,
    )
    authenticator = OwnerBearerAuthenticator(
        owner_authority,
        owner_id=settings.platform_owner_id,
        bearer_token=settings.platform_owner_token.get_secret_value(),
    )
    policy_router = build_policy_router(
        administration,
        principal_dependency=authenticator,
    )
    connection_attempts = ConnectionAttemptService(
        phone=cast(PhoneAttemptAdapter, adapter_registry.get("phone")),
        qr=cast(QrAttemptAdapter, adapter_registry.get("qr")),
    )
    connection_router = build_connection_router(
        connection_attempts,
        principal_dependency=authenticator,
    )
    return ApplicationComposition(
        account_repository=account_repository,
        session_store=session_store,
        connection_repository=connection_repository,
        proxy_repository=proxy_repository,
        gateway_repository=gateway_repository,
        compatibility_repository=compatibility_repository,
        gateway_factory=gateway_factory,
        telegram_client_factory=telegram_client_factory,
        adapter_registry=adapter_registry,
        policy_context_issuer=TelegramPolicyContextIssuer(
            account_repository, context_authority
        ),
        policy_gate=policy_gate,
        protected_message_loader=protected_loader,
        policy_router=policy_router,
        connection_router=connection_router,
        sync_engine=sync_engine,
        async_engine=async_engine,
    )


def create_production_composition(
    settings: ApiSettings | None = None,
) -> ApplicationComposition:
    """Fail-fast environment composition; no in-memory repository fallback exists."""
    resolved = settings or ApiSettings()
    database_url = str(resolved.database_url)
    sync_engine = create_engine(database_url, pool_pre_ping=True)
    try:
        async_engine = create_async_engine(database_url, pool_pre_ping=True)
    except Exception:
        sync_engine.dispose()
        raise
    sync_sessions = sessionmaker(sync_engine, expire_on_commit=False)
    async_sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    return build_application_composition(
        resolved,
        sync_sessions=sync_sessions,
        async_sessions=async_sessions,
        sync_engine=sync_engine,
        async_engine=async_engine,
    )
