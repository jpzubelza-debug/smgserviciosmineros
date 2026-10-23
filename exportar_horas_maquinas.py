"""Genera un archivo .db con solo la tabla horas_maquinas, para subirlo en /admin/importar-horas-maquinas.

La base de origen se abre en solo lectura. Uso:

    python exportar_horas_maquinas.py [origen.db] [destino.db]

Por defecto: dashboard.db -> horas_maquinas_export.db (ambos en la carpeta del proyecto).
"""
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TABLA = "horas_maquinas"


def exportar(origen, destino):
    if os.path.exists(destino):
        os.remove(destino)
    src = sqlite3.connect(Path(origen).resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(destino)
    try:
        (ddl,) = src.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLA,)).fetchone()
        dst.execute(ddl)
        columnas = [f[1] for f in src.execute(f"PRAGMA table_info({TABLA})")]
        lista = ", ".join(f'"{c}"' for c in columnas)
        filas = src.execute(f"SELECT {lista} FROM {TABLA} ORDER BY id").fetchall()
        dst.executemany(f"INSERT INTO {TABLA} ({lista}) VALUES ({', '.join('?' for _ in columnas)})", filas)
        dst.commit()
        return len(filas)
    finally:
        dst.close()
        src.close()


if __name__ == "__main__":
    origen = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, "dashboard.db")
    destino = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE_DIR, "horas_maquinas_export.db")
    total = exportar(origen, destino)
    print(f"{total} filas exportadas a {destino} ({os.path.getsize(destino) / 1024 / 1024:.1f} MB)")
