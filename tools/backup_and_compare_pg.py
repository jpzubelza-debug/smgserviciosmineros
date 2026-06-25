import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import psycopg2


def get_sqlite_counts(sqlite_path: str) -> dict:
    conn = sqlite3.connect(sqlite_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        counts = {}
        for table in tables:
            try:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except Exception:
                counts[table] = None
        return {"tables": tables, "counts": counts}
    finally:
        conn.close()


def get_pg_counts_and_rows(database_url: str) -> dict:
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
        rows_dump = {}
        for table in tables:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            counts[table] = int(cur.fetchone()[0])

            cur.execute(f'SELECT * FROM "{table}"')
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            rows_dump[table] = [dict(zip(cols, row)) for row in rows]

        return {"tables": tables, "counts": counts, "rows": rows_dump}
    finally:
        conn.close()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is missing in .env")

    sqlite_path = root / "dashboard.db"
    if not sqlite_path.exists():
        raise SystemExit("dashboard.db not found")

    local = get_sqlite_counts(str(sqlite_path))
    remote = get_pg_counts_and_rows(database_url)

    backup_dir = root / "backups"
    backup_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"pg_backup_{ts}.json"

    payload = {
        "created_at": datetime.now().isoformat(),
        "remote_tables": remote["tables"],
        "remote_counts": remote["counts"],
        "rows": remote["rows"],
    }

    with backup_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, default=str)

    print("connection_ok=True")
    print(f"backup_file={backup_path}")
    print(f"local_tables={len(local['tables'])} remote_tables={len(remote['tables'])}")

    interesting = [
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
    for table in interesting:
        print(f"{table}: local={local['counts'].get(table)} remote={remote['counts'].get(table)}")


if __name__ == "__main__":
    main()
