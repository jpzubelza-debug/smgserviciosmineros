"""Importación masiva de partes a horas_maquinas desde un archivo SQLite (ver exportar_horas_maquinas.py).

Pensado para pasar a Render los partes cargados en la base local: la base de Render vive en el disco persistente
y no se puede tocar desde afuera, así que el archivo se sube por /admin/importar-horas-maquinas.

Modos:
- agregar: inserta solo las filas que no estén ya en la tabla (se compara el contenido de todas las columnas salvo
  id y created_at, respetando repeticiones); los id los asigna la base.
- reemplazar: vacía la tabla y copia todas las filas del archivo conservando sus id.

Todo corre en una sola transacción: si algo falla no queda nada a medias. Con simular=True se calcula el
resultado y se deshace.
"""
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

TABLA = "horas_maquinas"
MODOS = ("agregar", "reemplazar")
NO_COMPARABLES = {"id", "created_at"}
CABECERA_SQLITE = b"SQLite format 3\x00"


class ErrorImportacion(ValueError):
    """Archivo o parámetros inválidos: se informa al usuario tal cual."""


def _columnas(conn):
    return [fila[1] for fila in conn.execute(f"PRAGMA table_info({TABLA})")]


def _abrir_origen(ruta):
    with open(ruta, "rb") as f:
        if f.read(len(CABECERA_SQLITE)) != CABECERA_SQLITE:
            raise ErrorImportacion("El archivo no es una base SQLite (.db). Generarlo con exportar_horas_maquinas.py.")
    origen = sqlite3.connect(Path(ruta).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        existe = origen.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLA,)).fetchone()
    except sqlite3.DatabaseError as exc:
        origen.close()
        raise ErrorImportacion(f"No se pudo leer el archivo: {exc}") from exc
    if not existe:
        origen.close()
        raise ErrorImportacion(f"El archivo no tiene la tabla {TABLA}.")
    return origen


def respaldar_base(ruta_db, carpeta):
    """Copia consistente de la base completa (API de backup de SQLite) antes de importar."""
    os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, f"dashboard_pre_import_horas_{datetime.now():%Y%m%d_%H%M%S}.db")
    origen = sqlite3.connect(ruta_db, timeout=20)
    copia = sqlite3.connect(destino)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()
    return destino


def importar(conn, ruta_origen, modo, simular=False):
    """Importa las filas de horas_maquinas del archivo en `conn`. Devuelve el resumen de la operación."""
    if modo not in MODOS:
        raise ErrorImportacion(f"Modo inválido: {modo!r}. Usar 'agregar' o 'reemplazar'.")

    origen = _abrir_origen(ruta_origen)
    try:
        columnas_origen = set(_columnas(origen))
        columnas_destino = _columnas(conn)
        if "fecha_parte" not in columnas_origen:
            raise ErrorImportacion("La tabla del archivo no tiene la columna fecha_parte.")
        comunes = [c for c in columnas_destino if c in columnas_origen and (modo == "reemplazar" or c != "id")]
        ignoradas = sorted(columnas_origen - set(columnas_destino))
        lista = ", ".join(f'"{c}"' for c in comunes)
        filas = origen.execute(f"SELECT {lista} FROM {TABLA} ORDER BY rowid").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ErrorImportacion(f"No se pudo leer la tabla {TABLA} del archivo: {exc}") from exc
    finally:
        origen.close()

    i_fecha = comunes.index("fecha_parte")
    comparables = [i for i, c in enumerate(comunes) if c not in NO_COMPARABLES]
    clave = lambda fila: tuple(fila[i] for i in comparables)  # noqa: E731

    antes = conn.execute(f"SELECT COUNT(*) FROM {TABLA}").fetchone()[0]
    existentes = Counter()
    if modo == "agregar":
        lista_comparables = ", ".join(f'"{comunes[i]}"' for i in comparables)
        existentes.update(tuple(f) for f in conn.execute(f"SELECT {lista_comparables} FROM {TABLA}"))

    a_insertar, omitidas, sin_fecha = [], 0, 0
    for fila in filas:
        if fila[i_fecha] is None:
            sin_fecha += 1
            continue
        k = clave(fila)
        if existentes[k] > 0:
            existentes[k] -= 1
            omitidas += 1
            continue
        a_insertar.append(fila)

    try:
        if modo == "reemplazar":
            conn.execute(f"DELETE FROM {TABLA}")
        conn.executemany(
            f"INSERT INTO {TABLA} ({lista}) VALUES ({', '.join('?' for _ in comunes)})",
            a_insertar,
        )
        despues = conn.execute(f"SELECT COUNT(*) FROM {TABLA}").fetchone()[0]
        if simular:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "modo": modo,
        "simulacion": bool(simular),
        "filas_archivo": len(filas),
        "insertadas": len(a_insertar),
        "omitidas_existentes": omitidas,
        "rechazadas_sin_fecha": sin_fecha,
        "filas_antes": antes,
        "filas_despues": despues,
        "columnas_ignoradas": ignoradas,
    }
