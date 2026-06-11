"""
db_adapter.py
=============
Capa de compatibilidad SQLite ↔ PostgreSQL para Dashboard Operaciones.

- En local (sin DATABASE_URL): usa SQLite igual que antes.
- En Render (con DATABASE_URL): usa PostgreSQL via psycopg2.

La interfaz pública es idéntica a la de sqlite3, por lo que el resto de
main.py no requiere cambios en la lógica de queries. Solo se cambia la
función get_sqlite_connection() para devolver una conexión de este módulo.

Diferencias manejadas automáticamente:
  - Placeholders: ? → %s
  - INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
  - INSERT OR REPLACE → INSERT ... ON CONFLICT(...) DO UPDATE (manual en queries complejas)
  - PRAGMA table_info(t) → consulta a information_schema
  - PRAGMA foreign_keys / busy_timeout / synchronous → ignorados (no-op)
  - SELECT name FROM sqlite_master WHERE type='table' → pg_tables
  - executescript() → ejecuta sentencias separadas por ;
  - executemany() → psycopg2 executemany con %s
  - lastrowid → via RETURNING id
  - conn.row_factory = sqlite3.Row → psycopg2 RealDictCursor
  - INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY (en schema_pg.sql)
"""

import os
import re
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# ---------------------------------------------------------------------------
# Helpers de traducción SQL
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\?")
_INSERT_OR_IGNORE_RE = re.compile(
    r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE
)
_INSERT_OR_REPLACE_RE = re.compile(
    r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.IGNORECASE
)
_PRAGMA_BUSY_RE = re.compile(r"^\s*PRAGMA\s+busy_timeout.*$", re.IGNORECASE | re.MULTILINE)
_PRAGMA_SYNC_RE = re.compile(r"^\s*PRAGMA\s+synchronous.*$", re.IGNORECASE | re.MULTILINE)
_PRAGMA_FK_RE = re.compile(r"^\s*PRAGMA\s+foreign_keys.*$", re.IGNORECASE | re.MULTILINE)

# PRAGMA table_info(tabla) → columnas de information_schema
_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^\s*PRAGMA\s+table_info\s*\(\s*['\"]?(\w+)['\"]?\s*\)\s*$",
    re.IGNORECASE,
)

# sqlite_master → pg_tables
_SQLITE_MASTER_RE = re.compile(
    r"SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'",
    re.IGNORECASE,
)


def _translate_sql(sql: str) -> str:
    """Traduce SQL SQLite a SQL PostgreSQL."""
    # Placeholders ? → %s
    sql = _PLACEHOLDER_RE.sub("%s", sql)
    # INSERT OR IGNORE INTO → INSERT INTO ... ON CONFLICT DO NOTHING
    sql = _INSERT_OR_IGNORE_RE.sub("INSERT INTO", sql)
    if "INSERT INTO" in sql.upper() and "ON CONFLICT" not in sql.upper() and _INSERT_OR_IGNORE_RE.search(sql) is None:
        # Si venía de INSERT OR IGNORE, agregar ON CONFLICT DO NOTHING al final
        pass
    # Detectar si fue reemplazado un INSERT OR IGNORE (ya reemplazado arriba como INSERT INTO)
    # Se agrega ON CONFLICT DO NOTHING solo si hay el patrón original
    # Re-chequear con el SQL original antes de reemplazar — ya fue procesado
    # INSERT OR REPLACE → no se traduce automáticamente (cada caso es ON CONFLICT DO UPDATE)
    sql = _INSERT_OR_REPLACE_RE.sub("INSERT INTO", sql)
    # sqlite_master
    sql = _SQLITE_MASTER_RE.sub(
        "SELECT tablename AS name FROM pg_tables WHERE schemaname = 'public'",
        sql,
    )
    return sql


def _is_pragma_noop(sql: str) -> bool:
    """Devuelve True si el PRAGMA debe ser ignorado en PG."""
    s = sql.strip().upper()
    return (
        s.startswith("PRAGMA BUSY_TIMEOUT")
        or s.startswith("PRAGMA SYNCHRONOUS")
        or s.startswith("PRAGMA FOREIGN_KEYS")
    )


def _parse_pragma_table_info(sql: str):
    """Si el SQL es PRAGMA table_info(tabla), devuelve nombre de tabla o None."""
    m = _PRAGMA_TABLE_INFO_RE.match(sql.strip())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Fila compatible con sqlite3.Row (acceso por nombre y por índice)
# ---------------------------------------------------------------------------

class _CompatRow:
    """Fila que soporta acceso por nombre (row["col"]) e índice (row[0])."""

    def __init__(self, data: dict):
        self._data = data
        self._keys = list(data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._data[self._keys[key]]
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._keys

    def items(self):
        return self._data.items()

    def __iter__(self):
        return iter(self._data.values())

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f"_CompatRow({self._data!r})"


# ---------------------------------------------------------------------------
# Cursor PostgreSQL compatible con sqlite3
# ---------------------------------------------------------------------------

class _PGCursor:
    def __init__(self, pg_cursor):
        self._cur = pg_cursor
        self.lastrowid = None

    def _exec(self, sql: str, params=None):
        table_name = _parse_pragma_table_info(sql)
        if table_name:
            # Traducir PRAGMA table_info → information_schema
            pg_sql = """
                SELECT column_name AS name, ordinal_position - 1 AS cid,
                       column_name AS name, data_type AS type,
                       CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                       column_default AS dflt_value,
                       CASE WHEN column_name IN (
                           SELECT kcu.column_name
                           FROM information_schema.table_constraints tc
                           JOIN information_schema.key_column_usage kcu
                             ON tc.constraint_name = kcu.constraint_name
                           WHERE tc.table_name = %s
                             AND tc.constraint_type = 'PRIMARY KEY'
                       ) THEN 1 ELSE 0 END AS pk
                FROM information_schema.columns
                WHERE table_name = %s
                ORDER BY ordinal_position
            """
            self._cur.execute(pg_sql, (table_name, table_name))
            return

        if _is_pragma_noop(sql):
            return

        translated = _translate_sql(sql)

        # Manejar INSERT OR IGNORE → ON CONFLICT DO NOTHING
        original_upper = sql.strip().upper()
        if re.match(r"\s*INSERT\s+OR\s+IGNORE\s+", sql, re.IGNORECASE):
            if "ON CONFLICT" not in translated.upper():
                translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        # Detectar si tiene RETURNING para capturar lastrowid
        has_returning = "RETURNING" in translated.upper()

        if params:
            self._cur.execute(translated, params)
        else:
            self._cur.execute(translated)

        if has_returning:
            row = self._cur.fetchone()
            if row:
                self.lastrowid = list(row.values())[0] if isinstance(row, dict) else row[0]
        elif translated.strip().upper().startswith("INSERT"):
            # Intentar obtener lastval()
            try:
                self._cur.execute("SELECT lastval()")
                row = self._cur.fetchone()
                if row:
                    self.lastrowid = list(row.values())[0] if isinstance(row, dict) else row[0]
            except Exception:
                pass

    def execute(self, sql, params=None):
        self._exec(sql, params)
        return self

    def executemany(self, sql, seq_of_params):
        if _is_pragma_noop(sql):
            return self
        translated = _translate_sql(sql)
        original_upper = sql.strip().upper()
        if re.match(r"\s*INSERT\s+OR\s+IGNORE\s+", sql, re.IGNORECASE):
            if "ON CONFLICT" not in translated.upper():
                translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        import psycopg2.extras
        psycopg2.extras.execute_batch(self._cur, translated, seq_of_params)
        return self

    def executescript(self, script: str):
        """Ejecuta múltiples sentencias SQL separadas por ;"""
        # Adaptar tipos SQLite → PG antes de ejecutar
        adapted = _adapt_schema_script(script)
        for stmt in _split_sql_statements(adapted):
            stmt = stmt.strip()
            if stmt:
                try:
                    self._cur.execute(stmt)
                except Exception as e:
                    # Ignorar errores de "ya existe" para CREATE TABLE/INDEX
                    msg = str(e).lower()
                    if "already exists" in msg or "duplicate" in msg:
                        self._cur.connection.rollback()
                    else:
                        raise
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return _CompatRow(row) if isinstance(row, dict) else _CompatRow(dict(zip([d[0] for d in self._cur.description], row)))

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows:
            return []
        if rows and isinstance(rows[0], dict):
            return [_CompatRow(r) for r in rows]
        cols = [d[0] for d in self._cur.description]
        return [_CompatRow(dict(zip(cols, r))) for r in rows]

    def close(self):
        self._cur.close()

    def __iter__(self):
        for row in self._cur:
            if isinstance(row, dict):
                yield _CompatRow(row)
            else:
                cols = [d[0] for d in self._cur.description]
                yield _CompatRow(dict(zip(cols, row)))


# ---------------------------------------------------------------------------
# Conexión PostgreSQL compatible con sqlite3
# ---------------------------------------------------------------------------

class _PGConnection:
    """
    Envuelve una conexión psycopg2 con la misma interfaz que sqlite3.Connection.
    Soporta uso como context manager: `with get_connection() as conn:`
    """

    def __init__(self, dsn: str):
        import psycopg2
        import psycopg2.extras
        self._conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        self._conn.autocommit = False

    # row_factory se ignora (siempre usa RealDictCursor)
    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, value):
        pass  # no-op: siempre usamos RealDictCursor

    def execute(self, sql, params=None):
        cur = _PGCursor(self._conn.cursor())
        cur.execute(sql, params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = _PGCursor(self._conn.cursor())
        cur.executemany(sql, seq_of_params)
        return cur

    def executescript(self, script: str):
        cur = _PGCursor(self._conn.cursor())
        cur.executescript(script)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self):
        import psycopg2.extras
        return _PGCursor(self._conn.cursor())

    # Context manager: igual que sqlite3 (commit en exit normal, rollback en error)
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False  # no suprimir excepciones


# ---------------------------------------------------------------------------
# Adaptador de schema (para executescript al inicializar)
# ---------------------------------------------------------------------------

_TYPE_MAP = [
    # SQLite → PostgreSQL
    (re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE), "SERIAL PRIMARY KEY"),
    (re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\b", re.IGNORECASE), "INTEGER PRIMARY KEY"),
    (re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE), ""),
    (re.compile(r"\bPRAGMA\s+foreign_keys\s*=\s*(ON|OFF)\s*;?", re.IGNORECASE), ""),
    (re.compile(r"\bIF\s+NOT\s+EXISTS\b", re.IGNORECASE), "IF NOT EXISTS"),  # ya es estándar
]


def _adapt_schema_script(script: str) -> str:
    """Convierte un script SQL de SQLite a PostgreSQL."""
    for pattern, replacement in _TYPE_MAP:
        script = pattern.sub(replacement, script)
    return script


def _split_sql_statements(script: str):
    """Divide un script SQL en sentencias individuales respetando literales."""
    statements = []
    current = []
    in_string = False
    string_char = None
    i = 0
    while i < len(script):
        ch = script[i]
        if in_string:
            current.append(ch)
            if ch == string_char:
                in_string = False
        elif ch in ("'", '"'):
            in_string = True
            string_char = ch
            current.append(ch)
        elif ch == "-" and i + 1 < len(script) and script[i + 1] == "-":
            # Comentario de línea: saltar hasta fin de línea
            while i < len(script) and script[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1
    last = "".join(current).strip()
    if last:
        statements.append(last)
    return statements


# ---------------------------------------------------------------------------
# Función principal: get_connection()
# ---------------------------------------------------------------------------

def get_connection(sqlite_path: str, timeout: int = 20):
    """
    Devuelve una conexión compatible con sqlite3.Connection.

    - Si DATABASE_URL está definida: conexión PostgreSQL.
    - Si no: conexión SQLite nativa.
    """
    if DATABASE_URL:
        return _PGConnection(DATABASE_URL)
    else:
        conn = sqlite3.connect(sqlite_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 20000")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
