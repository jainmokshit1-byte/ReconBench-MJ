"""Compare a migrated PostgreSQL schema with metadata and catalog-only contracts."""

from __future__ import annotations

import hashlib
import json
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

migration_url = import_module("reconbench_app.db.migration_config").migration_url

CONTRACT_FILE = BACKEND / "reconbench_app" / "db" / "schema_contract.json"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _catalog_contract(connection: Any) -> dict[str, Any]:
    inspector = inspect(connection)
    contract: dict[str, Any] = {}
    contract["postgres_major"] = (
        int(
            connection.scalar(
                text("SELECT current_setting('server_version_num')::integer")
            )
        )
        // 10_000
    )

    contract["extensions"] = {
        row["extname"]: {"version": row["extversion"], "schema": row["schema"]}
        for row in connection.execute(
            text(
                "SELECT e.extname, e.extversion, n.nspname AS schema "
                "FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
                "ORDER BY e.extname"
            )
        ).mappings()
    }
    contract["enums"] = {
        row["name"]: row["labels"] for row in inspector.get_enums(schema="public")
    }
    contract["domains"] = {
        row["name"]: {
            key: row.get(key)
            for key in ("type", "nullable", "default", "constraints", "collation")
        }
        for row in inspector.get_domains(schema="public")
    }
    contract["schema_type_owners"] = {
        row["name"]: {"kind": row["kind"], "owner": row["owner"]}
        for row in connection.execute(
            text(
                "SELECT type.typname AS name, type.typtype::text AS kind, owner.rolname AS owner "
                "FROM pg_type type "
                "JOIN pg_namespace namespace ON namespace.oid=type.typnamespace "
                "JOIN pg_roles owner ON owner.oid=type.typowner "
                "WHERE namespace.nspname='public' AND type.typtype IN ('e','d') "
                "ORDER BY type.typname"
            )
        ).mappings()
    }
    contract["views"] = {
        name: {
            "sha256": _sha256(
                inspector.get_view_definition(name, schema="public") or ""
            )
        }
        for name in inspector.get_view_names(schema="public")
    }
    contract["materialized_views"] = {
        name: {
            "sha256": _sha256(
                inspector.get_view_definition(name, schema="public") or ""
            )
        }
        for name in inspector.get_materialized_view_names(schema="public")
    }
    contract["relations"] = {
        row["name"]: {
            "kind": row["kind"],
            "is_partition": row["is_partition"],
            "owner": row["owner"],
        }
        for row in connection.execute(
            text(
                "SELECT c.relname AS name, c.relkind::text AS kind, owner.rolname AS owner, "
                "c.relispartition AS is_partition "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_roles owner ON owner.oid=c.relowner "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f') "
                "ORDER BY c.relname"
            )
        ).mappings()
    }

    routines = connection.execute(
        text(
            "SELECT p.prokind::text AS kind, p.proname AS name, "
            "pg_get_function_identity_arguments(p.oid) AS identity_args, "
            "pg_get_functiondef(p.oid) AS definition, p.prosecdef AS security_definer, "
            "p.provolatile::text AS volatility, p.proisstrict AS strict, "
            "p.proparallel::text AS parallel, COALESCE(p.proconfig, ARRAY[]::text[]) AS config, "
            "owner.rolname AS owner "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "JOIN pg_roles owner ON owner.oid=p.proowner "
            "WHERE n.nspname='public' AND p.prokind IN ('f','p') "
            "ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)"
        )
    ).mappings()
    contract["routines"] = {
        f"{row['name']}({row['identity_args']})": {
            "kind": row["kind"],
            "owner": row["owner"],
            "sha256": _sha256(row["definition"]),
            "security_definer": row["security_definer"],
            "volatility": row["volatility"],
            "strict": row["strict"],
            "parallel": row["parallel"],
            "config": list(row["config"]),
        }
        for row in routines
    }

    triggers = connection.execute(
        text(
            "SELECT t.tgname AS name, c.relname AS table_name, t.tgenabled::text AS enabled, "
            "pg_get_triggerdef(t.oid,true) AS definition, p.proname AS routine_name, "
            "pg_get_function_identity_arguments(p.oid) AS routine_args "
            "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid "
            "WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"
        )
    ).mappings()
    contract["triggers"] = {
        f"{row['table_name']}.{row['name']}": {
            "enabled": row["enabled"],
            "routine": f"{row['routine_name']}({row['routine_args']})",
            "sha256": _sha256(row["definition"]),
        }
        for row in triggers
    }

    roles = connection.execute(
        text(
            "SELECT rolname,rolcanlogin,rolsuper,rolinherit,rolcreaterole,rolcreatedb,"
            "rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil::text AS valid_until "
            "FROM pg_roles WHERE rolname LIKE 'reconbench_%' ORDER BY rolname"
        )
    ).mappings()
    role_fields = (
        "rolcanlogin",
        "rolsuper",
        "rolinherit",
        "rolcreaterole",
        "rolcreatedb",
        "rolreplication",
        "rolbypassrls",
        "rolconnlimit",
        "valid_until",
    )
    contract["roles"] = {
        row["rolname"]: {field: row[field] for field in role_fields} for row in roles
    }
    memberships = connection.execute(
        text(
            "SELECT parent.rolname AS role, child.rolname AS member, "
            "m.admin_option,m.inherit_option,m.set_option "
            "FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid "
            "JOIN pg_roles child ON child.oid=m.member "
            "WHERE parent.rolname LIKE 'reconbench_%' OR child.rolname LIKE 'reconbench_%' "
            "ORDER BY parent.rolname,child.rolname"
        )
    ).mappings()
    contract["role_memberships"] = [dict(row) for row in memberships]

    sequences = connection.execute(
        text(
            "SELECT s.relname AS name, format_type(q.seqtypid,NULL) AS type, "
            "owner.rolname AS owner, "
            "q.seqstart AS start, q.seqincrement AS increment, q.seqmin AS min, "
            "q.seqmax AS max, q.seqcache AS cache, q.seqcycle AS cycle, "
            "tbl.relname AS owned_by_table, att.attname AS owned_by_column, "
            "d.deptype::text AS ownership_type "
            "FROM pg_class s JOIN pg_namespace n ON n.oid=s.relnamespace "
            "JOIN pg_roles owner ON owner.oid=s.relowner "
            "JOIN pg_sequence q ON q.seqrelid=s.oid "
            "LEFT JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=s.oid "
            "AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i') "
            "LEFT JOIN pg_class tbl ON tbl.oid=d.refobjid "
            "LEFT JOIN pg_attribute att ON att.attrelid=d.refobjid AND att.attnum=d.refobjsubid "
            "WHERE n.nspname='public' AND s.relkind='S' ORDER BY s.relname"
        )
    ).mappings()
    sequence_fields = (
        "type",
        "owner",
        "start",
        "increment",
        "min",
        "max",
        "cache",
        "cycle",
        "owned_by_table",
        "owned_by_column",
        "ownership_type",
    )
    contract["sequences"] = {
        row["name"]: {field: row[field] for field in sequence_fields}
        for row in sequences
    }

    foreign_keys: list[dict[str, Any]] = []
    table_names = inspector.get_table_names(schema="public")
    for table_name in table_names:
        if table_name == "alembic_version":
            continue
        for fk in inspector.get_foreign_keys(table_name, schema="public"):
            foreign_keys.append(
                {
                    "table": table_name,
                    "name": fk.get("name"),
                    "columns": fk.get("constrained_columns"),
                    "referred_schema": fk.get("referred_schema"),
                    "referred_table": fk.get("referred_table"),
                    "referred_columns": fk.get("referred_columns"),
                    "options": fk.get("options") or {},
                }
            )
    contract["foreign_keys"] = sorted(
        foreign_keys, key=lambda row: (row["table"], row["name"] or "")
    )

    acl_queries = (
        (
            "SELECT 'schema'::text kind,n.nspname AS object,"
            "CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE g.rolname END AS grantee,"
            "x.privilege_type,x.is_grantable FROM pg_namespace n "
            "CROSS JOIN LATERAL aclexplode(n.nspacl) x LEFT JOIN pg_roles g ON g.oid=x.grantee "
            "WHERE n.nspname='public' AND n.nspacl IS NOT NULL AND x.grantee<>n.nspowner"
        ),
        (
            "SELECT CASE c.relkind WHEN 'S' THEN 'sequence' WHEN 'v' THEN 'view' "
            "WHEN 'm' THEN 'materialized_view' ELSE 'table' END AS kind,c.relname AS object,"
            "CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE g.rolname END AS grantee,"
            "x.privilege_type,x.is_grantable FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(c.relacl) x LEFT JOIN pg_roles g ON g.oid=x.grantee "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f') "
            "AND c.relacl IS NOT NULL AND x.grantee<>c.relowner"
        ),
        (
            "SELECT 'column'::text kind,c.relname||'.'||a.attname AS object,"
            "CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE g.rolname END AS grantee,"
            "x.privilege_type,x.is_grantable FROM pg_attribute a "
            "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(a.attacl) x LEFT JOIN pg_roles g ON g.oid=x.grantee "
            "WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped "
            "AND a.attacl IS NOT NULL"
        ),
        (
            "SELECT 'routine'::text kind,format('%I.%I(%s)',n.nspname,p.proname,"
            "pg_get_function_identity_arguments(p.oid)) AS object,"
            "CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE g.rolname END AS grantee,"
            "x.privilege_type,x.is_grantable FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "CROSS JOIN LATERAL aclexplode(p.proacl) x LEFT JOIN pg_roles g ON g.oid=x.grantee "
            "WHERE n.nspname='public' AND p.proacl IS NOT NULL AND x.grantee<>p.proowner"
        ),
    )
    acls: list[dict[str, Any]] = []
    for query in acl_queries:
        acls.extend(dict(row) for row in connection.execute(text(query)).mappings())
    contract["explicit_acls"] = sorted(
        acls,
        key=lambda row: (
            row["kind"],
            row["object"],
            row["grantee"],
            row["privilege_type"],
        ),
    )

    default_privileges = connection.execute(
        text(
            "SELECT owner.rolname AS owner, COALESCE(namespace.nspname, '*') AS schema, "
            "defaults.defaclobjtype::text AS object_type, "
            "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END AS grantee, "
            "acl.privilege_type, acl.is_grantable "
            "FROM pg_default_acl defaults "
            "JOIN pg_roles owner ON owner.oid=defaults.defaclrole "
            "LEFT JOIN pg_namespace namespace ON namespace.oid=defaults.defaclnamespace "
            "CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl "
            "LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            "WHERE owner.rolname LIKE 'reconbench_%' "
            "ORDER BY owner.rolname, namespace.nspname, defaults.defaclobjtype, "
            "grantee.rolname, acl.privilege_type"
        )
    ).mappings()
    contract["default_privileges"] = [dict(row) for row in default_privileges]

    contract["object_counts"] = {
        "application_tables": len(
            [name for name in table_names if name != "alembic_version"]
        ),
        "extensions": len(contract["extensions"]),
        "views": len(contract["views"]),
        "materialized_views": len(contract["materialized_views"]),
        "routines": len(contract["routines"]),
        "triggers": len(contract["triggers"]),
        "enums": len(contract["enums"]),
        "domains": len(contract["domains"]),
        "sequences": len(contract["sequences"]),
        "foreign_keys": len(contract["foreign_keys"]),
        "explicit_acls": len(contract["explicit_acls"]),
        "default_privileges": len(contract["default_privileges"]),
    }
    return contract


def _differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[str] = []
        for key in sorted(expected.keys() | actual.keys()):
            child = f"{path}.{key}" if path else str(key)
            if key not in expected:
                differences.append(f"unexpected {child}")
            elif key not in actual:
                differences.append(f"missing {child}")
            else:
                differences.extend(_differences(expected[key], actual[key], child))
        return differences
    if expected != actual:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return []


def main() -> int:
    expected = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    engine = create_engine(migration_url())
    try:
        config = Config(str(BACKEND / "alembic.ini"))
        scripts = ScriptDirectory.from_config(config)
        with engine.connect() as connection:
            current_heads = set(
                MigrationContext.configure(connection).get_current_heads()
            )
        expected_heads = set(scripts.get_heads())
        if current_heads != expected_heads:
            raise RuntimeError(
                "Database is not at Alembic head: "
                f"current={sorted(current_heads)!r}, expected={sorted(expected_heads)!r}"
            )
        print(f"Migration head: {', '.join(sorted(current_heads))}")

        command.check(config)
        print("Alembic autogenerate: no metadata drift detected")

        with engine.connect() as connection:
            observed = _catalog_contract(connection)
        diffs = _differences(expected, observed)
        if diffs:
            print("Catalog contract: drift detected")
            for difference in diffs[:50]:
                print(f"  - {difference}")
            if len(diffs) > 50:
                print(f"  ... and {len(diffs) - 50} more differences")
            return 1
        print("Catalog contract: all explicit checks passed")
        print(f"Catalog objects checked: {expected['object_counts']}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
