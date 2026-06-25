import argparse
import os
import sqlite3
from typing import List

from dotenv import load_dotenv
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values


EXCLUDED_TABLES = {"sqlite_sequence"}


def get_sqlite_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        ORDER BY name
        """
    ).fetchall()
    return [r[0] for r in rows if r[0] not in EXCLUDED_TABLES]


def get_sqlite_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def reset_sequence_if_needed(pg_cur, table: str, columns: List[str]) -> None:
    if "id" not in columns:
        return
    pg_cur.execute(
        """
        SELECT pg_get_serial_sequence(%s, 'id')
        """,
        (table,),
    )
    seq = pg_cur.fetchone()[0]
    if not seq:
        return
    pg_cur.execute(
        sql.SQL("SELECT COALESCE(MAX(id), 0) FROM {}")
        .format(sql.Identifier(table))
    )
    max_id = pg_cur.fetchone()[0] or 0
    if max_id > 0:
        pg_cur.execute("SELECT setval(%s, %s, %s)", (seq, max_id, True))
    else:
        # For empty tables, keep next nextval() at 1.
        pg_cur.execute("SELECT setval(%s, %s, %s)", (seq, 1, False))


def copy_table(sqlite_conn: sqlite3.Connection, pg_cur, table: str, truncate: bool) -> int:
    columns = get_sqlite_columns(sqlite_conn, table)
    if not columns:
        return 0

    select_sql = f"SELECT {', '.join(columns)} FROM {table}"
    sqlite_rows = sqlite_conn.execute(select_sql).fetchall()

    if truncate:
        try:
            pg_cur.execute(
                sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(sql.Identifier(table))
            )
        except Exception as exc:
            # Render/managed PG can hold concurrent locks; fallback to DELETE to reduce lock contention.
            if getattr(exc, "pgcode", None) in {"40P01", "55P03"}:
                pg_cur.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
            else:
                raise

    if sqlite_rows:
        insert_sql = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        )
        values = [tuple(row) for row in sqlite_rows]
        execute_values(pg_cur, insert_sql.as_string(pg_cur.connection), values, page_size=500)

    reset_sequence_if_needed(pg_cur, table, columns)
    return len(sqlite_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync SQLite data into PostgreSQL (Render)")
    parser.add_argument("--sqlite", default="dashboard.db", help="Path to local SQLite file")
    parser.add_argument(
        "--tables",
        default="",
        help="Comma-separated tables to sync (default: all tables)",
    )
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Do not TRUNCATE destination tables before inserting",
    )
    args = parser.parse_args()

    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise SystemExit("DATABASE_URL is not configured. Set it in .env or environment variables.")

    if not os.path.exists(args.sqlite):
        raise SystemExit(f"SQLite file not found: {args.sqlite}")

    sqlite_conn = sqlite3.connect(args.sqlite)
    sqlite_conn.row_factory = sqlite3.Row

    if args.tables.strip():
        tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    else:
        tables = get_sqlite_tables(sqlite_conn)

    # Respect dependencies: copy parent tables first where possible.
    preferred_order = [
        "usuarios",
        "roles_funcionales",
        "usuario_roles_funcionales",
        "historial_accesos",
        "personal",
        "choferes",
        "vehiculos",
        "viajes",
        "recursos_viaje",
        "recurso_acompanantes",
        "ordenes_salida",
        "gestion_operativa",
        "categorias",
        "tipos_producto",
        "unidades_medida",
        "ubicaciones",
        "familias",
        "marcas",
        "modelos",
        "proyectos",
        "instalaciones",
        "productos",
        "stock",
        "stock_consumibles",
        "remitos_ingreso",
        "remitos_ingreso_detalle",
        "remitos_entrega",
        "remitos_entrega_detalle",
        "movimientos_stock",
        "inventarios",
        "inventarios_detalle",
        "ajustes_stock",
        "roles_almacen",
        "personal_roles_almacen",
        "documentos",
        "documentos_detalle",
        "movimientos",
        "auditoria",
        "adjuntos",
    ]

    ordered = [t for t in preferred_order if t in tables]
    ordered += [t for t in tables if t not in ordered]

    with psycopg2.connect(db_url) as pg_conn:
        with pg_conn.cursor() as pg_cur:
            pg_cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                """
            )
            remote_tables = {str(r[0]) for r in pg_cur.fetchall()}

            skipped = [t for t in ordered if t not in remote_tables]
            if skipped:
                print(f"Skipping tables not found in PostgreSQL: {', '.join(skipped)}")

            ordered = [t for t in ordered if t in remote_tables]

            total = 0
            for table in ordered:
                try:
                    n = copy_table(sqlite_conn, pg_cur, table, truncate=not args.no_truncate)
                    total += n
                    print(f"{table}: {n} rows")
                except Exception as exc:
                    pg_conn.rollback()
                    raise RuntimeError(f"Failed on table '{table}': {exc}") from exc

            pg_conn.commit()
            print(f"Done. Total rows copied: {total}")

    sqlite_conn.close()


if __name__ == "__main__":
    main()
