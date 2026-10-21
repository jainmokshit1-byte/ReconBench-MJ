"""Argon2id passwords, database sessions, CSRF and login throttling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.engine import Connection, Engine

from reconbench_app.ai.session_keys import delete_session_keys
from reconbench_app.authorization import public_endpoint, requires, resolve_effective_role
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.settings import Settings

COOKIE_NAME = "reconbench_session"
LOGIN_PATH = "/api/v1/auth/login"
_DUMMY_PASSWORD = secrets.token_urlsafe(48)


class AuthSecurityConfig(BaseModel):
    """Validated session and login-throttle values from app.security."""

    model_config = ConfigDict(extra="ignore", strict=True)

    session_idle_minutes: int = Field(default=30, gt=0)
    session_absolute_hours: int = Field(default=12, gt=0)
    login_email_max_failures: int = Field(default=10, gt=0)
    login_email_window_minutes: int = Field(default=15, gt=0)
    login_email_lockout_minutes: int = Field(default=15, gt=0)
    login_ip_max_failures: int = Field(default=30, gt=0)
    login_ip_window_minutes: int = Field(default=15, gt=0)
    login_ip_throttle_minutes: int = Field(default=15, gt=0)

    @classmethod
    def from_config_body(cls, body: object) -> AuthSecurityConfig:
        if not isinstance(body, Mapping):
            raise AuthConfigurationError("active config body is not an object")
        app = body.get("app", {})
        if not isinstance(app, Mapping):
            raise AuthConfigurationError("active config app section is not an object")
        security = app.get("security", {})
        if not isinstance(security, Mapping):
            raise AuthConfigurationError("active config app.security section is not an object")
        try:
            return cls.model_validate(dict(security))
        except ValidationError as exc:
            raise AuthConfigurationError("active app.security values are invalid") from exc


class AuthConfigurationError(RuntimeError):
    """The active authentication configuration cannot be used safely."""


class LoginRateLimited(Exception):
    """A generic email or IP bucket has reached its approved limit."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("login attempts are temporarily limited")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class LoginSession:
    user_id: UUID
    workspace_id: UUID
    display_name: str
    role: str
    token: str
    csrf_token: str
    absolute_expires_at: datetime
    max_age_seconds: int
    # The persisted role; ``role`` is the P0 effective role. Only the capabilities in
    # ``STORED_ROLE_CAPABILITIES`` (``ai.manage``, O-18) are checked against it.
    stored_role: str | None = None


@dataclass(frozen=True, slots=True)
class AuthContext:
    session_id: UUID
    workspace_id: UUID
    user_id: UUID
    display_name: str
    role: str
    csrf_token: str
    stored_role: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticationCheck:
    context: AuthContext | None
    csrf_valid: bool


@lru_cache(maxsize=16)
def _dummy_hash(
    time_cost: int,
    memory_cost: int,
    parallelism: int,
    hash_len: int,
    salt_len: int,
) -> str:
    """Cache a process-local dummy hash at the configured Argon2 cost."""

    hasher = PasswordHasher(
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        hash_len=hash_len,
        salt_len=salt_len,
    )
    return hasher.hash(_DUMMY_PASSWORD)


def csrf_token_for_session(session_token: str) -> str:
    """Derive a stable, independent 256-bit CSRF token from the bearer secret."""

    digest = hmac.new(
        session_token.encode("ascii"), b"ReconBench CSRF token v1", hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def digest_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthService:
    """Synchronous PostgreSQL authentication service, called from FastAPI's thread pool."""

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=settings.argon2_hash_len,
            salt_len=settings.argon2_salt_len,
        )

    @property
    def _dummy_hash(self) -> str:
        return _dummy_hash(
            self.settings.argon2_time_cost,
            self.settings.argon2_memory_cost_kib,
            self.settings.argon2_parallelism,
            self.settings.argon2_hash_len,
            self.settings.argon2_salt_len,
        )

    @requires("users.manage")
    def hash_password(self, password: str) -> str:
        return self.hasher.hash(password)

    def _verify_password(self, password: str, encoded_hash: str) -> bool:
        try:
            return self.hasher.verify(encoded_hash, password)
        except VerifyMismatchError:
            return False
        except (InvalidHashError, VerificationError):
            # A malformed stored hash must not create a fast account-enumeration path.
            if encoded_hash != self._dummy_hash:
                with suppress(VerifyMismatchError):
                    self.hasher.verify(self._dummy_hash, password)
            return False

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    @public_endpoint
    def login(
        self,
        email: str,
        password: str,
        source_ip: str,
        user_agent: str | None = None,
    ) -> LoginSession | None:
        """Return a new session, None for generic bad credentials, or a generic 429."""

        normalized_email = self._normalize_email(email)
        peer_ip = ipaddress.ip_address(source_ip).compressed
        now = self._now()
        workspace = metadata.tables["workspace"]
        app_user = metadata.tables["app_user"]
        event = metadata.tables["rate_limit_event"]
        session = metadata.tables["session"]

        with self.engine.begin() as connection:
            workspace_ids = connection.scalars(sa.select(workspace.c.id).limit(2)).all()
            if not workspace_ids:
                self._verify_password(password, self._dummy_hash)
                return None
            if len(workspace_ids) != 1:
                raise AuthConfigurationError("P0 authentication requires exactly one workspace")
            workspace_id = workspace_ids[0]
            self._lock_login_buckets(connection, workspace_id, normalized_email, peer_ip)
            now = self._monotonic_bucket_time(
                connection, workspace_id, normalized_email, peer_ip, now
            )
            policy = self._load_policy(connection, workspace_id)

            user = (
                connection.execute(
                    sa.select(app_user)
                    .where(
                        app_user.c.workspace_id == workspace_id,
                        app_user.c.email == normalized_email,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            email_failures, email_locked = self._email_bucket(
                connection, workspace_id, normalized_email, now, policy
            )
            ip_locked = self._ip_bucket(connection, workspace_id, peer_ip, now, policy)
            user_locked = bool(user and user["locked_until"] and user["locked_until"] > now)

            blocked = (
                email_failures >= policy.login_email_max_failures
                or email_locked
                or ip_locked
                or user_locked
            )
            if blocked:
                # Equalize 429 timing/body for known and unknown emails, without recording
                # throttled traffic as a fresh failure that would extend its own lock.
                self._verify_password(password, self._dummy_hash)
                raise LoginRateLimited(self._retry_after(policy))

            encoded_hash = user["password_hash"] if user else self._dummy_hash
            valid_password = self._verify_password(password, encoded_hash)
            if user is None or not user["is_active"] or not valid_password:
                self._record_attempt(
                    connection,
                    event,
                    workspace_id=workspace_id,
                    user_id=user["id"] if user else None,
                    email=normalized_email,
                    source_ip=peer_ip,
                    succeeded=False,
                    occurred_at=now,
                )
                if user is not None:
                    failure_count = email_failures + 1
                    locked_until = (
                        now + timedelta(minutes=policy.login_email_lockout_minutes)
                        if failure_count >= policy.login_email_max_failures
                        else None
                    )
                    connection.execute(
                        sa.update(app_user)
                        .where(
                            app_user.c.workspace_id == workspace_id,
                            app_user.c.id == user["id"],
                        )
                        .values(
                            failed_logins=failure_count,
                            locked_until=locked_until,
                            version=app_user.c.version + 1,
                        )
                    )
                return None

            self._record_attempt(
                connection,
                event,
                workspace_id=workspace_id,
                user_id=user["id"],
                email=normalized_email,
                source_ip=peer_ip,
                succeeded=True,
                occurred_at=now,
            )
            connection.execute(
                sa.update(app_user)
                .where(
                    app_user.c.workspace_id == workspace_id,
                    app_user.c.id == user["id"],
                )
                .values(
                    failed_logins=0,
                    locked_until=None,
                    last_login_at=now,
                    version=app_user.c.version + 1,
                )
            )
            revoked_ids = list(
                connection.scalars(
                    sa.update(session)
                    .where(
                        session.c.workspace_id == workspace_id,
                        session.c.user_id == user["id"],
                        session.c.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                    .returning(session.c.id)
                )
            )
            delete_session_keys(connection, revoked_ids, now=now)

            token = secrets.token_urlsafe(32)
            csrf_token = csrf_token_for_session(token)
            absolute_expires_at = now + timedelta(hours=policy.session_absolute_hours)
            connection.execute(
                sa.insert(session).values(
                    id=new_id(),
                    workspace_id=workspace_id,
                    user_id=user["id"],
                    token_hash=digest_token(token),
                    csrf_hash=digest_token(csrf_token),
                    created_at=now,
                    last_seen_at=now,
                    idle_expires_at=min(
                        now + timedelta(minutes=policy.session_idle_minutes),
                        absolute_expires_at,
                    ),
                    abs_expires_at=absolute_expires_at,
                    ip=peer_ip,
                    user_agent=user_agent[:1024] if user_agent else None,
                )
            )
            return LoginSession(
                user_id=user["id"],
                workspace_id=workspace_id,
                display_name=user["display_name"],
                role=resolve_effective_role(str(user["role"])),
                token=token,
                csrf_token=csrf_token,
                absolute_expires_at=absolute_expires_at,
                max_age_seconds=policy.session_absolute_hours * 60 * 60,
                stored_role=str(user["role"]),
            )

    @requires("data.view")
    def authenticate(
        self,
        session_token: str | None,
        csrf_token: str | None = None,
        *,
        require_csrf: bool = False,
    ) -> AuthenticationCheck:
        """Validate and touch a session; do not extend idle expiry on a bad CSRF request."""

        if not session_token:
            return AuthenticationCheck(None, False)
        now = self._now()
        session = metadata.tables["session"]
        app_user = metadata.tables["app_user"]
        stmt = (
            sa.select(
                session.c.id.label("session_id"),
                session.c.workspace_id,
                session.c.user_id,
                session.c.csrf_hash,
                session.c.idle_expires_at,
                session.c.abs_expires_at,
                session.c.revoked_at,
                app_user.c.display_name,
                app_user.c.role,
                app_user.c.is_active,
            )
            .select_from(
                session.join(
                    app_user,
                    sa.and_(
                        session.c.workspace_id == app_user.c.workspace_id,
                        session.c.user_id == app_user.c.id,
                    ),
                )
            )
            .where(
                session.c.token_hash == digest_token(session_token),
                session.c.revoked_at.is_(None),
            )
            .with_for_update(of=session)
        )
        with self.engine.begin() as connection:
            row = connection.execute(stmt).mappings().one_or_none()
            if row is None:
                return AuthenticationCheck(None, False)
            if (
                not row["is_active"]
                or now >= row["idle_expires_at"]
                or now >= row["abs_expires_at"]
            ):
                connection.execute(
                    sa.update(session)
                    .where(session.c.id == row["session_id"], session.c.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
                delete_session_keys(connection, [row["session_id"]], now=now)
                return AuthenticationCheck(None, False)

            derived_csrf = csrf_token_for_session(session_token)
            stored_csrf_hash = row["csrf_hash"]
            csrf_integrity_ok = hmac.compare_digest(digest_token(derived_csrf), stored_csrf_hash)
            if not csrf_integrity_ok:
                connection.execute(
                    sa.update(session)
                    .where(session.c.id == row["session_id"], session.c.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
                delete_session_keys(connection, [row["session_id"]], now=now)
                return AuthenticationCheck(None, False)

            csrf_valid = True
            if require_csrf:
                csrf_valid = bool(
                    csrf_token and hmac.compare_digest(digest_token(csrf_token), stored_csrf_hash)
                )
                if not csrf_valid:
                    context = self._auth_context(row, derived_csrf)
                    return AuthenticationCheck(context, False)

            policy = self._load_policy(connection, row["workspace_id"])
            idle_expires_at = min(
                now + timedelta(minutes=policy.session_idle_minutes), row["abs_expires_at"]
            )
            connection.execute(
                sa.update(session)
                .where(session.c.id == row["session_id"], session.c.revoked_at.is_(None))
                .values(last_seen_at=now, idle_expires_at=idle_expires_at)
            )
            return AuthenticationCheck(self._auth_context(row, derived_csrf), csrf_valid)

    @requires("data.view")
    def revoke_session(self, session_id: Any) -> None:
        session = metadata.tables["session"]
        now = self._now()
        with self.engine.begin() as connection:
            connection.execute(
                sa.update(session)
                .where(session.c.id == session_id, session.c.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            delete_session_keys(connection, [session_id], now=now)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authentication clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _auth_context(row: Any, csrf_token: str) -> AuthContext:
        return AuthContext(
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            display_name=row["display_name"],
            role=resolve_effective_role(str(row["role"])),
            csrf_token=csrf_token,
            stored_role=str(row["role"]),
        )

    @staticmethod
    def _retry_after(policy: AuthSecurityConfig) -> int:
        # One constant retry delay for both buckets avoids revealing which fired.
        return (
            max(
                policy.login_email_lockout_minutes,
                policy.login_ip_throttle_minutes,
            )
            * 60
        )

    @staticmethod
    def _lock_login_buckets(
        connection: Connection,
        workspace_id: Any,
        email: str,
        source_ip: str,
    ) -> None:
        lock_names = sorted(
            (
                f"reconbench:login:email:{workspace_id}:{email}",
                f"reconbench:login:ip:{workspace_id}:{source_ip}",
            )
        )
        for lock_name in lock_names:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                {"lock_name": lock_name},
            )

    @staticmethod
    def _monotonic_bucket_time(
        connection: Connection,
        workspace_id: Any,
        email: str,
        source_ip: str,
        now: datetime,
    ) -> datetime:
        """Order serialized bucket events even when the injected clock ties."""

        event = metadata.tables["rate_limit_event"]
        last_event = cast(
            datetime | None,
            connection.scalar(
                sa.select(sa.func.max(event.c.occurred_at)).where(
                    event.c.workspace_id == workspace_id,
                    event.c.kind == "login",
                    sa.or_(event.c.email == email, event.c.ip == source_ip),
                )
            ),
        )
        if last_event is not None and now <= last_event:
            return (last_event + timedelta(microseconds=1)).astimezone(UTC)
        return now

    @staticmethod
    def _load_policy(connection: Connection, workspace_id: Any) -> AuthSecurityConfig:
        config_active = metadata.tables["config_active"]
        config_version = metadata.tables["config_version"]
        body = connection.scalar(
            sa.select(config_version.c.body)
            .select_from(
                config_active.join(
                    config_version,
                    sa.and_(
                        config_active.c.workspace_id == config_version.c.workspace_id,
                        config_active.c.config_version_id == config_version.c.id,
                    ),
                )
            )
            .where(config_active.c.workspace_id == workspace_id)
        )
        if body is None:
            return AuthSecurityConfig()
        return AuthSecurityConfig.from_config_body(body)

    @staticmethod
    def _record_attempt(
        connection: Connection,
        event: Any,
        *,
        workspace_id: Any,
        user_id: Any,
        email: str,
        source_ip: str,
        succeeded: bool,
        occurred_at: datetime,
    ) -> None:
        connection.execute(
            sa.insert(event).values(
                id=new_id(),
                workspace_id=workspace_id,
                kind="login",
                user_id=user_id,
                email=email,
                ip=source_ip,
                succeeded=succeeded,
                occurred_at=occurred_at,
            )
        )

    @staticmethod
    def _email_bucket(
        connection: Connection,
        workspace_id: Any,
        email: str,
        now: datetime,
        policy: AuthSecurityConfig,
    ) -> tuple[int, bool]:
        event = metadata.tables["rate_limit_event"]
        last_success = connection.scalar(
            sa.select(sa.func.max(event.c.occurred_at)).where(
                event.c.workspace_id == workspace_id,
                event.c.kind == "login",
                event.c.email == email,
                event.c.succeeded.is_(True),
            )
        )
        failed = sa.and_(
            event.c.workspace_id == workspace_id,
            event.c.kind == "login",
            event.c.email == email,
            event.c.succeeded.is_(False),
        )
        since_success = (
            event.c.occurred_at > last_success if last_success is not None else sa.true()
        )
        rolling_start = now - timedelta(minutes=policy.login_email_window_minutes)
        rolling_count = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(event)
                .where(failed, since_success, event.c.occurred_at > rolling_start)
            )
            or 0
        )
        lock_start = now - timedelta(minutes=policy.login_email_lockout_minutes)
        latest_failure = connection.scalar(
            sa.select(sa.func.max(event.c.occurred_at)).where(
                failed, since_success, event.c.occurred_at > lock_start
            )
        )
        event_locked = False
        if (
            latest_failure is not None
            and latest_failure + timedelta(minutes=policy.login_email_lockout_minutes) > now
        ):
            trigger_start = latest_failure - timedelta(minutes=policy.login_email_window_minutes)
            trigger_count = connection.scalar(
                sa.select(sa.func.count())
                .select_from(event)
                .where(
                    failed,
                    since_success,
                    event.c.occurred_at > trigger_start,
                    event.c.occurred_at <= latest_failure,
                )
            )
            event_locked = int(trigger_count or 0) >= policy.login_email_max_failures
        return rolling_count, event_locked

    @staticmethod
    def _ip_bucket(
        connection: Connection,
        workspace_id: Any,
        source_ip: str,
        now: datetime,
        policy: AuthSecurityConfig,
    ) -> bool:
        event = metadata.tables["rate_limit_event"]
        failed = sa.and_(
            event.c.workspace_id == workspace_id,
            event.c.kind == "login",
            event.c.ip == source_ip,
            event.c.succeeded.is_(False),
        )
        rolling_start = now - timedelta(minutes=policy.login_ip_window_minutes)
        rolling_count = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(event)
                .where(failed, event.c.occurred_at > rolling_start)
            )
            or 0
        )
        if rolling_count >= policy.login_ip_max_failures:
            return True

        lock_start = now - timedelta(minutes=policy.login_ip_throttle_minutes)
        latest_failure = connection.scalar(
            sa.select(sa.func.max(event.c.occurred_at)).where(
                failed, event.c.occurred_at > lock_start
            )
        )
        if (
            latest_failure is None
            or latest_failure + timedelta(minutes=policy.login_ip_throttle_minutes) <= now
        ):
            return False
        trigger_start = latest_failure - timedelta(minutes=policy.login_ip_window_minutes)
        trigger_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(event)
            .where(
                failed,
                event.c.occurred_at > trigger_start,
                event.c.occurred_at <= latest_failure,
            )
        )
        return int(trigger_count or 0) >= policy.login_ip_max_failures


__all__ = [
    "AuthContext",
    "AuthSecurityConfig",
    "AuthService",
    "AuthenticationCheck",
    "AuthConfigurationError",
    "COOKIE_NAME",
    "LOGIN_PATH",
    "LoginRateLimited",
    "LoginSession",
    "csrf_token_for_session",
    "digest_token",
]
