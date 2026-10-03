"""Capability declarations for P0 authentication and the P1 role matrix."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, ParamSpec, TypeVar
from uuid import UUID

type Capability = Literal[
    "files.upload",
    "links.edit",
    "recon.run",
    "cases.decide",
    "cases.approve_exception",
    "settings.change",
    "ai.manage",
    "users.manage",
    "export.create",
    "data.view",
]
type Role = Literal["ap_executive", "manager", "auditor"]
type AuthorizationKind = Literal["public", "capability"]
P = ParamSpec("P")
R = TypeVar("R")

CAPABILITY_ORDER: tuple[Capability, ...] = (
    "files.upload",
    "links.edit",
    "recon.run",
    "cases.decide",
    "cases.approve_exception",
    "settings.change",
    "ai.manage",
    "users.manage",
    "export.create",
    "data.view",
)

ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    "ap_executive": frozenset(
        {
            "files.upload",
            "links.edit",
            "recon.run",
            "cases.decide",
            "export.create",
            "data.view",
        }
    ),
    "manager": frozenset(CAPABILITY_ORDER),
    "auditor": frozenset({"export.create", "data.view"}),
}

P0_EFFECTIVE_ROLE: Role = "manager"
PUBLIC_ROUTE_ALLOWLIST = frozenset(
    {
        ("/healthz", "GET"),
        ("/readyz", "GET"),
        ("/api/v1/auth/login", "POST"),
    }
)
PUBLIC_SERVICE_ALLOWLIST = frozenset({"AuthService.login"})


@dataclass(frozen=True, slots=True)
class AuthorizationDeclaration:
    kind: AuthorizationKind
    capability: Capability | None = None


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Authenticated actor fields copied into case_action and audit_log rows."""

    workspace_id: UUID
    actor_id: UUID
    actor_role: Role

    def database_values(self) -> dict[str, UUID | Role]:
        """Return the shared actor columns used by the write-side schema."""

        return {
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
        }


class AuthorizationAuditError(AssertionError):
    """Raised when a route or service entry point lacks an access declaration."""


def resolve_effective_role(stored_role: str | None) -> Role:
    """P0 deliberately maps every persisted user to Manager; P1 checks stay off."""

    del stored_role
    return P0_EFFECTIVE_ROLE


def capabilities_for_role(role: Role) -> frozenset[Capability]:
    """Return the complete documented capability set for a role."""

    return ROLE_CAPABILITIES[role]


def requires(
    capability: Capability,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Declare the capability required by an authenticated entry point.

    This decorator records policy metadata only. P0 resolves every user to Manager,
    and capability enforcement is intentionally not enabled until P1.
    """

    if capability not in CAPABILITY_ORDER:
        raise ValueError(f"unknown ReconBench capability: {capability}")

    def declare(target: Callable[P, R]) -> Callable[P, R]:
        return _set_declaration(target, AuthorizationDeclaration("capability", capability))

    return declare


def public_endpoint[**P, R](target: Callable[P, R]) -> Callable[P, R]:
    """Explicitly mark a health check or login endpoint as public."""

    return _set_declaration(target, AuthorizationDeclaration("public"))


def authorization_declaration(target: Any) -> AuthorizationDeclaration | None:
    """Read metadata from functions, methods, or property descriptors."""

    if isinstance(target, (staticmethod, classmethod)):
        target = target.__func__
    elif isinstance(target, property):
        target = target.fget
    if inspect.ismethod(target):
        target = target.__func__
    return getattr(target, "__reconbench_authorization__", None)


def audit_authorization_declarations(app: Any, service_classes: tuple[type[Any], ...]) -> None:
    """Fail closed when any registered route or public service method is undeclared."""

    violations: list[str] = []
    for route, full_path in _iter_routes(app.routes):
        endpoint = getattr(route, "endpoint", None)
        methods = getattr(route, "methods", None) or {"WEBSOCKET"}
        declaration = authorization_declaration(endpoint)
        label = f"route {','.join(sorted(methods))} {full_path}"
        if declaration is None:
            violations.append(f"{label} has no authorization declaration")
            continue
        if declaration.kind == "capability" and declaration.capability is None:
            violations.append(f"{label} has no capability declaration")
        for method in methods:
            if declaration.kind == "public" and (full_path, method) not in PUBLIC_ROUTE_ALLOWLIST:
                violations.append(f"{label} is public outside the health/login allowlist")
            if (full_path, method) in PUBLIC_ROUTE_ALLOWLIST and declaration.kind != "public":
                violations.append(f"{label} must be explicitly marked public")

    for service_class in service_classes:
        for name, descriptor in vars(service_class).items():
            if name.startswith("_"):
                continue
            method = _service_method(descriptor)
            if method is None:
                continue
            declaration = authorization_declaration(descriptor)
            label = f"service {service_class.__name__}.{name}"
            if declaration is None:
                violations.append(f"{label} has no authorization declaration")
            elif declaration.kind == "capability" and declaration.capability is None:
                violations.append(f"{label} has no capability declaration")
            elif declaration.kind == "public" and label.removeprefix("service ") not in (
                PUBLIC_SERVICE_ALLOWLIST
            ):
                violations.append(f"{label} is public outside the login allowlist")

    if violations:
        raise AuthorizationAuditError("\n".join(violations))


def _set_declaration[**P, R](
    target: Callable[P, R], declaration: AuthorizationDeclaration
) -> Callable[P, R]:
    declaration_target: Any = target.__func__ if inspect.ismethod(target) else target
    declaration_target.__reconbench_authorization__ = declaration
    return target


def _service_method(descriptor: Any) -> Any | None:
    if isinstance(descriptor, (staticmethod, classmethod)):
        return descriptor.__func__
    if isinstance(descriptor, property):
        return descriptor.fget
    return descriptor if inspect.isfunction(descriptor) else None


def _iter_routes(routes: list[Any], prefix: str = "") -> list[tuple[Any, str]]:
    """Expand FastAPI included routers and Starlette mounts for complete auditing."""

    flattened: list[tuple[Any, str]] = []
    for route in routes:
        original_router = getattr(route, "original_router", None)
        include_context = getattr(route, "include_context", None)
        if original_router is not None and include_context is not None:
            nested_prefix = prefix + getattr(include_context, "prefix", "")
            flattened.extend(_iter_routes(original_router.routes, nested_prefix))
            continue

        path = getattr(route, "path", None)
        nested_routes = getattr(route, "routes", None)
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None and nested_routes is not None and path is not None:
            flattened.extend(_iter_routes(nested_routes, prefix + path))
            continue
        if path is not None:
            flattened.append((route, prefix + path))
    return flattened


__all__ = [
    "ActorContext",
    "AuthorizationAuditError",
    "AuthorizationDeclaration",
    "CAPABILITY_ORDER",
    "Capability",
    "P0_EFFECTIVE_ROLE",
    "PUBLIC_ROUTE_ALLOWLIST",
    "ROLE_CAPABILITIES",
    "Role",
    "audit_authorization_declarations",
    "authorization_declaration",
    "capabilities_for_role",
    "public_endpoint",
    "requires",
    "resolve_effective_role",
]
