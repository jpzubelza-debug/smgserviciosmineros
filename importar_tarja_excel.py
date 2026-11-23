"""Importa el histórico de la hoja «TARJA GENERAL» (Logistica SMG.xlsm) a tarja_registros.

Proceso aparte: por defecto SOLO SIMULA y genera el reporte de excepciones; graba únicamente con --aplicar.

    python importar_tarja_excel.py "Logistica SMG.xlsm" --db C:\\ruta\\dashboard.db --reporte excepciones.xlsx
    python importar_tarja_excel.py "Logistica SMG.xlsm" --db C:\\ruta\\dashboard.db --reporte excepciones.xlsx --aplicar

Reglas (ver tarja.mapear_celda_excel):
- Fila 1 = fecha de cada columna (desde la col. D). Datos desde la fila 7. Col. A = legajo, B = nombre, C = obra (no se importa).
- Se cruza por legajo con la tabla personal. Filas sin legajo, legajos repetidos o inexistentes → excepciones.
- Números = ID de centro de costo, si existe en ESTA base (los inexistentes quedan vacíos y se reportan).
- Textos: D, V, F, Ausente→A, Lic→LIC, Permiso→PER, Sanción→SAN, Viaje→VJ, Curso/Capacitación→CAP, Dev/Compensar→COMP, Fin→BAJA.
  Obras sin centro confirmado (P210-RB, GEMAR, PM, …) → excepciones. Basura (x, c, `, ?) → se ignora y se reporta.
  Cualquier otro texto → PER con el texto original como justificación (observaciones).
- Los días ya cargados en el sistema no se pisan (se reportan). Después se recalculan turnos y horas.
"""
import argparse
import collections
import os
import sqlite3
import sys
from datetime import date, datetime

import tarja

FILA_FECHAS = 1
FILA_DATOS = 7
COL_PRIMER_DIA = 4  # D
USUARIO = "importacion_excel"


def _legajo(valor):
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor).strip()


def leer_planilla(ruta, hoja="TARJA GENERAL"):
    from openpyxl import load_workbook
    wb = load_workbook(ruta, read_only=True, data_only=True)
    if hoja not in wb.sheetnames:
        raise SystemExit(f"La planilla no tiene la hoja «{hoja}»")
    ws = wb[hoja]
    filas = ws.iter_rows(min_row=1, values_only=True)
    cab = next(filas)
    fechas = {}
    for i, v in enumerate(cab):
        if i + 1 < COL_PRIMER_DIA:
            continue
        if isinstance(v, datetime):
            fechas[i] = v.date().isoformat()
        elif isinstance(v, date):
            fechas[i] = v.isoformat()
    datos = []
    for nro, fila in enumerate(ws.iter_rows(min_row=FILA_DATOS, values_only=True), FILA_DATOS):
        datos.append((nro, fila))
    wb.close()
    return fechas, datos


def procesar(conn, ruta, aplicar=False):
    fechas, datos = leer_planilla(ruta)
    centros = {f["id"] for f in conn.execute("SELECT id FROM centros_costos")}
    personal = {f["legajo"]: f["nombre"] for f in conn.execute("SELECT legajo, nombre FROM personal")}
    codigos = {f["codigo"]: f["id"] for f in conn.execute("SELECT id, codigo FROM tarja_codigos_estado")}
    existentes = {(f["legajo"], f["fecha"]) for f in conn.execute("SELECT legajo, fecha FROM tarja_registros")}

    excepciones = []  # fila, legajo, nombre, fecha, columna, valor, motivo
    filas_ok = []
    conteo_leg = collections.Counter(_legajo(f[0]) for _, f in datos if f and _legajo(f[0]))

    for nro, f in datos:
        if not f or not any(v not in (None, "") for v in f[:3]):
            continue
        leg, nombre = _legajo(f[0]), (str(f[1]).strip() if len(f) > 1 and f[1] is not None else "")
        con_datos = sum(1 for i in fechas if i < len(f) and f[i] not in (None, ""))
        if not leg:
            excepciones.append((nro, "", nombre, "", "", f"{con_datos} días con datos", "fila sin legajo"))
            continue
        if conteo_leg[leg] > 1:
            excepciones.append((nro, leg, nombre, "", "", f"{con_datos} días con datos", f"legajo repetido ({conteo_leg[leg]} filas)"))
            continue
        if leg not in personal:
            excepciones.append((nro, leg, nombre, "", "", f"{con_datos} días con datos", "legajo inexistente en personal"))
            continue
        filas_ok.append((nro, leg, nombre, f))

    from openpyxl.utils import get_column_letter
    a_insertar, ya_cargados = [], 0
    mapeados = collections.Counter()
    sin_mapear = collections.Counter()
    for nro, leg, nombre, f in filas_ok:
        for i, fecha in fechas.items():
            v = f[i] if i < len(f) else None
            tipo, dato, obs = tarja.mapear_celda_excel(v, centros)
            if tipo is None:
                continue
            col = get_column_letter(i + 1)
            if tipo in ("excepcion", "ignorar"):
                excepciones.append((nro, leg, nombre, fecha, col, str(v), dato if tipo == "excepcion" else f"ignorado: {dato}"))
                sin_mapear[(str(v).strip(), dato if tipo == "excepcion" else f"ignorado: {dato}")] += 1
                continue
            if (leg, fecha) in existentes:
                ya_cargados += 1
                excepciones.append((nro, leg, nombre, fecha, col, str(v), "ya cargado en el sistema (no se pisa)"))
                continue
            if tipo == "cc":
                a_insertar.append((leg, fecha, dato, None, None))
                mapeados[f"CC {dato}"] += 1
            else:
                a_insertar.append((leg, fecha, None, codigos[dato], obs))
                mapeados[dato + (" (con justificación)" if obs else "")] += 1

    resumen = {
        "columnas_de_dias": len(fechas),
        "desde": min(fechas.values()) if fechas else None,
        "hasta": max(fechas.values()) if fechas else None,
        "filas_importables": len(filas_ok),
        "dias_a_importar": len(a_insertar),
        "ya_cargados": ya_cargados,
        "excepciones": len(excepciones),
        "aplicado": False,
    }
    if aplicar and a_insertar:
        ahora = tarja._ahora()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany("""INSERT INTO tarja_registros (legajo, fecha, centro_costo_id, codigo_estado_id, observaciones,
                creado_por, creado_en, actualizado_por, actualizado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             [(*r, USUARIO, ahora, USUARIO, ahora) for r in a_insertar])
            legajos = sorted({r[0] for r in a_insertar})
            tarja.recalcular_todo(conn, legajos)
            tarja.historial_evento(conn, None, None, "IMPORTACION_EXCEL", None,
                                   {"archivo": os.path.basename(ruta), "dias": len(a_insertar), "operadores": len(legajos)}, USUARIO)
            conn.commit()
            resumen["aplicado"] = True
        except Exception:
            conn.rollback()
            raise
    return resumen, excepciones, mapeados, sin_mapear


def escribir_reporte(ruta, resumen, excepciones, mapeados, sin_mapear):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    ws.append(["Importación TARJA GENERAL", "APLICADA" if resumen["aplicado"] else "SIMULACIÓN (no se grabó nada)"])
    ws["A1"].font = Font(bold=True, size=13)
    for k, v in resumen.items():
        ws.append([k.replace("_", " "), v])
    ws.append([])
    ws.append(["Mapeado a", "Días"])
    for k, v in mapeados.most_common():
        ws.append([k, v])
    cab = PatternFill("solid", fgColor="DCE8F7")
    ws2 = wb.create_sheet("Códigos sin importar")
    ws2.append(["Valor en la planilla", "Motivo", "Celdas"])
    for (valor, motivo), n in sorted(sin_mapear.items(), key=lambda x: -x[1]):
        ws2.append([valor, motivo, n])
    ws3 = wb.create_sheet("Excepciones")
    ws3.append(["Fila", "Legajo", "Nombre", "Fecha", "Columna", "Valor", "Motivo"])
    for r in excepciones:
        ws3.append(list(r))
    for hoja in (ws2, ws3):
        for c in hoja[1]:
            c.font, c.fill = Font(bold=True), cab
        hoja.freeze_panes = "A2"
    ws3.auto_filter.ref = f"A1:G{max(1, len(excepciones) + 1)}"
    for col, ancho in zip("ABCDEFG", (7, 9, 34, 12, 9, 22, 52)):
        ws3.column_dimensions[col].width = ancho
    ws2.column_dimensions["A"].width = 26
    ws2.column_dimensions["B"].width = 52
    ws.column_dimensions["A"].width = 30
    wb.save(ruta)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("planilla")
    ap.add_argument("--db", required=True, help="ruta de dashboard.db (usar primero una COPIA)")
    ap.add_argument("--reporte", required=True, help="xlsx de salida con el reporte de excepciones")
    ap.add_argument("--aplicar", action="store_true", help="graba los días (sin esto solo simula)")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN")
    tarja.migrar(conn)
    conn.execute("COMMIT")
    resumen, exc, mapeados, sin_mapear = procesar(conn, a.planilla, a.aplicar)
    conn.close()
    escribir_reporte(a.reporte, resumen, exc, mapeados, sin_mapear)
    for k, v in resumen.items():
        print(f"{k}: {v}")
    print(f"Reporte: {a.reporte}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
