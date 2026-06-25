import os
import sqlite3

from dotenv import load_dotenv
import psycopg2


def sqlite_counts(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        counts = {}
        for table in tables:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {"tables": tables, "counts": counts}
    finally:
        conn.close()


def pg_counts(database_url: str) -> dict:
    conn = psycopg2.connect(database_url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
            ORDER BY table_name
            """
        )
        tables = [r[0] for r in cur.fetchall()]
        counts = {}
        for table in tables:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            counts[table] = int(cur.fetchone()[0])
        return {"tables": tables, "counts": counts}
    finally:
        conn.close()


def main() -> None:
    load_dotenv("c:/Dashboard_Operaciones/.env")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL missing")

    local = sqlite_counts("c:/Dashboard_Operaciones/dashboard.db")
    remote = pg_counts(database_url)

    common = sorted(set(local["tables"]) & set(remote["tables"]))
    mismatches = [
        (t, local["counts"].get(t), remote["counts"].get(t))
        for t in common
        if local["counts"].get(t) != remote["counts"].get(t)
    ]

    print(f"local_tables={len(local['tables'])} remote_tables={len(remote['tables'])}")
    print(f"common_tables={len(common)} mismatches={len(mismatches)}")

    if mismatches:
        for t, l, r in mismatches:
            print(f"{t}: local={l} remote={r}")

    checks = [
        "usuarios",
        "roles_funcionales",
        "vehiculos",
        "choferes",
        "personal",
        "viajes",
        "recursos_viaje",
        "ordenes_salida",
        "productos",
        "stock",
        "movimientos",
        "documentos",
    ]
    for table in checks:
        print(
            f"check {table}: local={local['counts'].get(table)} remote={remote['counts'].get(table)}"
        )


if __name__ == "__main__":
    main()
