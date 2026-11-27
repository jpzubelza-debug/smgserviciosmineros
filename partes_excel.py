"""Exportación a Excel de los partes diarios del Panel de operaciones (membretado SMG).

Arma un .xlsx con dos hojas: "Resumen" (carátula con indicadores, resumen por equipo y por turno, gráfico)
y "Partes" (el detalle con las columnas elegidas, ordenado fecha → equipo → turno → N.º de parte).
Solo da formato: las filas ya vienen filtradas por main.py con el mismo filtro del panel."""
import io
import os
from copy import copy
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------- columnas
# (campo, encabezado, tipo). tipo: fecha | centro | num | texto | largo | estado | fecha_hora
COLUMNAS = [
    # Identificación
    ("fecha_parte", "Fecha", "fecha"),
    ("ci", "Equipo", "centro"),
    ("turno", "Turno", "centro"),
    ("nro_parte", "N.º parte", "centro"),
    ("legajo_empleado", "Legajo", "centro"),
    ("maquinista", "Operador", "texto"),
    ("obra", "Obra", "texto"),
    ("proyecto", "Proyecto", "texto"),
    ("cliente", "Cliente", "texto"),
    ("orden_compra", "Orden de compra", "texto"),
    ("descripcion_trabajos", "Descripción de trabajos", "largo"),
    ("acoplado", "Acoplado", "texto"),
    # Horas
    ("hs_inicio", "Hs inicio", "num"),
    ("hs_final", "Hs final", "num"),
    ("total_hs", "Total hs", "num"),
    ("hs_desplazamiento", "Hs desplazamiento", "num"),
    ("hs_mantenimiento", "Hs mantenimiento", "num"),
    ("hs_reparaciones", "Hs reparaciones", "num"),
    ("hs_disposicion", "Hs disposición", "num"),
    ("total_hs_control", "Total hs control", "num"),
    ("no_programadas", "No programadas", "num"),
    # Km y consumos
    ("km_inicio", "Km inicio", "num"),
    ("km_final", "Km final", "num"),
    ("km_total", "Km total", "num"),
    ("viajes", "Viajes", "num"),
    ("gasoil", "Gasoil (l)", "num"),
    ("aceite", "Aceite (l)", "num"),
    # Control
    ("novedades", "Novedades", "largo"),
    ("firma_parte", "Firma parte", "texto"),
    ("control_supervisor", "Supervisor SMG", "texto"),
    ("estado", "Estado", "estado"),
    ("certificado", "Certificado", "texto"),
    ("estado_sub_alquiler", "Estado sub-alquiler", "texto"),
    ("origen_carga", "Origen de carga", "texto"),
    ("created_at", "Fecha de carga", "fecha_hora"),
]
COL_DEF = {c[0]: c for c in COLUMNAS}
FIJAS = ("fecha_parte", "ci", "turno")  # definen el orden: siempre van
# Se suman en los subtotales por día y en el total general (los horómetros y odómetros no se suman).
SUMABLES = {"total_hs", "hs_desplazamiento", "hs_mantenimiento", "hs_reparaciones", "hs_disposicion",
            "total_hs_control", "no_programadas", "km_total", "viajes", "gasoil", "aceite"}

# ---------------------------------------------------------------- estilos
AZUL = "1E3A8A"
AZUL_HDR = "2563EB"
CELESTE = "EFF6FF"
GRIS_FILA = "F1F5F9"
GRIS_SUB = "E2E8F0"
BORDE = "CBD5E1"
GRIS_TXT = "64748B"
FUENTE = "Calibri"

_fino = Side(style="thin", color=BORDE)
B_FINO = Border(left=_fino, right=_fino, top=_fino, bottom=_fino)
F_HDR = PatternFill("solid", start_color=AZUL_HDR, end_color=AZUL_HDR)
F_ALT = PatternFill("solid", start_color=GRIS_FILA, end_color=GRIS_FILA)
F_SUB = PatternFill("solid", start_color=GRIS_SUB, end_color=GRIS_SUB)
F_TOT = PatternFill("solid", start_color=AZUL, end_color=AZUL)
F_CEL = PatternFill("solid", start_color=CELESTE, end_color=CELESTE)
ESTADO_ESTILO = {
    "Controlado": (PatternFill("solid", start_color="DCFCE7", end_color="DCFCE7"), "166534"),
    "Pendiente": (PatternFill("solid", start_color="FEF3C7", end_color="FEF3C7"), "92400E"),
}
FMT_FECHA = "dd/mm/yyyy"
FMT_FECHA_HORA = "dd/mm/yyyy hh:mm"
FMT_ENTERO = "#,##0"
FMT_DECIMAL = "#,##0.0"

FILA_HDR = 8  # filas 1–6 membrete, 7 separación, 8 encabezado de la tabla

ORIGEN_TXT = {"app": "App de operadores", "escritorio": "Escritorio / importación", "todos": "Todos"}
ESTADO_TXT = {"pendientes": "Pendientes", "controlados": "Controlados", "": "Todos"}


class _Estilos:
    """Asignar fuente/borde/relleno celda por celda es lento en openpyxl (hashea cada objeto): el estilo de
    cada combinación se arma una vez y en las demás celdas se copia el índice ya registrado."""

    def __init__(self):
        self._cache = {}

    def aplicar(self, celda, clave, armar):
        guardado = self._cache.get(clave)
        if guardado is None:
            armar(celda)
            self._cache[clave] = copy(celda._style)
        else:
            celda._style = copy(guardado)


def _f(size=10, bold=False, color="1E293B", italic=False):
    return Font(name=FUENTE, size=size, bold=bold, color=color, italic=italic)


# ---------------------------------------------------------------- validación y orden
def validar_columnas(texto: str) -> list:
    """'fecha_parte,ci,...' → lista validada contra COLUMNAS (sin repetidos). Agrega las fijas que falten
    al principio. Un nombre desconocido es un error: se informa en lugar de ignorarlo."""
    pedidas = [c.strip() for c in str(texto or "").split(",") if c.strip()]
    desconocidas = [c for c in pedidas if c not in COL_DEF]
    if desconocidas:
        raise ValueError(f"Columnas no válidas: {', '.join(desconocidas[:10])}")
    vistas, salida = set(), []
    for c in pedidas:
        if c not in vistas:
            vistas.add(c)
            salida.append(c)
    faltan = [c for c in FIJAS if c not in vistas]
    return faltan + salida


def _fold(s) -> str:
    t = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).strip().upper()


def turno_normalizado(t) -> str:
    v = _fold(t)
    if v.startswith("D"):
        return "Día"
    if v.startswith("N"):
        return "Noche"
    return str(t or "").strip()


def _clave_natural(s):
    """C-16 < C-22 < CA-04 < M-09 < V-23 (los números se comparan como números)."""
    v = str(s or "").strip().upper()
    if not v:
        return ((2, 0, ""),)
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.findall(r"\d+|\D+", v))


def _clave_turno(t) -> int:
    n = turno_normalizado(t)
    return 0 if n == "Día" else 1 if n == "Noche" else 2


def _clave_nro(n):
    v = str(n or "").strip()
    return (0, int(v), "") if v.isdigit() else (1, 0, v)


def ordenar(filas: list) -> list:
    return sorted(filas, key=lambda f: (str(f.get("fecha_parte") or ""), _clave_natural(f.get("ci")),
                                        _clave_turno(f.get("turno")), _clave_nro(f.get("nro_parte")), f.get("id") or 0))


# ---------------------------------------------------------------- valores
def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    t = str(v).strip().replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return str(v).strip()


def _fecha(v):
    try:
        return datetime.strptime(str(v or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return str(v or "").strip() or None


def _fecha_carga(v):
    """created_at se guarda en UTC; se muestra en hora de Argentina (UTC-3)."""
    t = str(v or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(t[:19], fmt) - timedelta(hours=3)
        except ValueError:
            continue
    return t or None


def _entero_o_texto(v):
    t = str(v or "").strip()
    return int(t) if t.isdigit() and str(int(t)) == t else (t or None)


def valor(fila: dict, campo: str):
    v = fila.get(campo)
    if campo == "fecha_parte":
        return _fecha(v)
    if campo == "created_at":
        return _fecha_carga(v)
    if campo == "turno":
        return turno_normalizado(v) or None
    if campo in ("nro_parte", "legajo_empleado"):
        return _entero_o_texto(v)
    if campo == "maquinista":
        return (str(v or "").strip() or str(fila.get("_nombre_personal") or "").strip()) or None
    if campo == "estado":
        return "Controlado" if str(fila.get("control") or "").strip() else "Pendiente"
    if campo == "origen_carga":
        return "App de operadores" if str(v or "").strip() == "APP_OPERADOR" else "Escritorio / importación"
    if COL_DEF[campo][2] == "num":
        return _num(v)
    t = str(v).strip() if v is not None else ""
    return t or None


def _fmt_num(v) -> str:
    return FMT_ENTERO if float(v).is_integer() else FMT_DECIMAL


def _n(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- helpers de hoja
def _rango(ws, c1, f1, c2, f2, *, fill=None, border=None, font=None, align=None):
    for f in range(f1, f2 + 1):
        for c in range(c1, c2 + 1):
            celda = ws.cell(row=f, column=c)
            if fill is not None:
                celda.fill = fill
            if border is not None:
                celda.border = border
            if font is not None:
                celda.font = font
            if align is not None:
                celda.alignment = align


def _merge(ws, c1, f1, c2, f2, valor=None, **estilo):
    if c2 > c1 or f2 > f1:
        ws.merge_cells(start_row=f1, start_column=c1, end_row=f2, end_column=c2)
    if valor is not None:
        ws.cell(row=f1, column=c1, value=valor)
    if estilo:
        _rango(ws, c1, f1, c2, f2, **estilo)
    return ws.cell(row=f1, column=c1)


def _rich(pares):
    """[("Obra / Centro de costos", "106 - …"), …] → texto con la etiqueta en negrita."""
    gris = InlineFont(rFont=FUENTE, sz=9, color=GRIS_TXT)
    neg = InlineFont(rFont=FUENTE, sz=9, b=True, color=AZUL)
    partes = []
    for i, (et, va) in enumerate(pares):
        if i:
            partes.append(TextBlock(gris, "     ·     "))
        partes.append(TextBlock(neg, f"{et}: "))
        partes.append(TextBlock(gris, str(va)))
    return CellRichText(*partes)


def membrete(ws, ancho: int, titulo: str, meta: dict):
    """Filas 1–6: logo, empresa, título del documento, emisión y fila de contexto con los filtros."""
    emp = meta.get("empresa") or {}
    der = max(3, min(6, ancho // 2 - 1))       # columnas del bloque derecho
    izq_fin = ancho - der                      # último col. del bloque izquierdo
    for fila, alto in ((1, 24), (2, 13), (3, 13), (4, 8), (5, 17), (6, 17)):
        ws.row_dimensions[fila].height = alto

    logo = meta.get("logo")
    if logo and os.path.exists(logo):
        try:
            from openpyxl.drawing.image import Image as XLImage
            img = XLImage(logo)
            img.width, img.height = 60, 60
            ws.add_image(img, "A1")
        except Exception:  # sin Pillow o imagen ilegible: el membrete sale igual, sin logo
            pass

    _merge(ws, 2, 1, izq_fin, 1, "SMG SERVICIOS MINEROS", font=_f(16, True, AZUL),
           align=Alignment(horizontal="left", vertical="center"))
    linea1 = " · ".join(x for x in [emp.get("razon_social"), f"CUIT {emp['cuit']}" if emp.get("cuit") else ""] if x)
    linea2 = " · ".join(x for x in [
        emp.get("direccion"),
        f"Tel. {emp['telefono']}" if emp.get("telefono") else "",
        emp.get("email") or "",
    ] if x)
    _merge(ws, 2, 2, izq_fin, 2, linea1, font=_f(9, color=GRIS_TXT), align=Alignment(horizontal="left", vertical="center"))
    _merge(ws, 2, 3, izq_fin, 3, linea2, font=_f(9, color=GRIS_TXT), align=Alignment(horizontal="left", vertical="center"))

    _merge(ws, izq_fin + 1, 1, ancho, 1, titulo, font=_f(14, True, AZUL),
           align=Alignment(horizontal="right", vertical="center"))
    emitido = f"Emitido: {meta['emitido']:%d/%m/%Y %H:%M} – Usuario: {meta.get('usuario') or '—'}"
    _merge(ws, izq_fin + 1, 2, ancho, 2, emitido, font=_f(9, color=GRIS_TXT),
           align=Alignment(horizontal="right", vertical="center"))

    ctx = meta.get("contexto") or []
    mitad = (len(ctx) + 1) // 2
    alin = Alignment(horizontal="left", vertical="center", indent=1)
    for fila, pares in ((5, ctx[:mitad]), (6, ctx[mitad:])):
        celda = _merge(ws, 1, fila, ancho, fila, fill=F_CEL, align=alin)
        if pares:
            celda.value = _rich(pares)
    grueso = Border(bottom=Side(style="thick", color=AZUL))
    for c in range(1, ancho + 1):
        ws.cell(row=6, column=c).border = grueso


def _impresion(ws, ultima_col: int, ultima_fila: int, filas_titulo: str):
    ws.sheet_view.showGridLines = False
    ps = ws.page_setup
    ps.orientation = "landscape"
    ps.paperSize = ws.PAPERSIZE_A4
    ps.fitToWidth = 1
    ps.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.25
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.page_margins.header = ws.page_margins.footer = 0.25
    ws.print_options.horizontalCentered = True
    ws.print_title_rows = filas_titulo
    ws.print_area = f"A1:{get_column_letter(ultima_col)}{ultima_fila}"
    for pie in (ws.oddFooter, ws.evenFooter):
        pie.left.text = "SMG Servicios Mineros – Documento generado por el sistema"
        pie.left.size = 9
        pie.left.font = FUENTE
        pie.right.text = "Página &P de &N"
        pie.right.size = 9
        pie.right.font = FUENTE


# ---------------------------------------------------------------- hoja Partes
def _hoja_partes(ws, filas, columnas, meta, subtotales: bool):
    n = len(columnas)
    ancho = max(n, 9)
    membrete(ws, ancho, "PARTES DIARIOS DE EQUIPOS", meta)
    defs = [COL_DEF[c] for c in columnas]

    hdr_font = _f(10, True, "FFFFFF")
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, (_, titulo, _) in enumerate(defs, start=1):
        celda = ws.cell(row=FILA_HDR, column=i, value=titulo)
        celda.font, celda.fill, celda.alignment, celda.border = hdr_font, F_HDR, hdr_align, B_FINO
    ws.row_dimensions[FILA_HDR].height = 30

    al = {
        "fecha": Alignment(horizontal="center", vertical="top"),
        "fecha_hora": Alignment(horizontal="center", vertical="top"),
        "centro": Alignment(horizontal="center", vertical="top"),
        "estado": Alignment(horizontal="center", vertical="top"),
        "num": Alignment(horizontal="right", vertical="top"),
        "texto": Alignment(horizontal="left", vertical="top"),
        "largo": Alignment(horizontal="left", vertical="top", wrap_text=True),
    }
    f_dato = _f(10)
    f_sub = _f(10, True, "1E293B")
    f_tot = _f(10, True, "FFFFFF")
    largos = [len(t) + 3 for _, t, _ in defs]  # +3: lugar para el botón del autofiltro
    decimales = {c: False for c in columnas}
    sumas = [i for i, c in enumerate(columnas, start=1) if c in SUMABLES]
    col_label = next((i for i, c in enumerate(columnas, start=1) if c not in SUMABLES), 1)

    fila_x = FILA_HDR + 1
    inicio_dia, dia_actual, alterna = fila_x, None, 0

    def subtotal(desde, hasta, dia):
        nonlocal fila_x
        etiqueta = f"Subtotal {dia:%d/%m}" if isinstance(dia, date) else "Subtotal"
        for c in range(1, n + 1):
            estilos.aplicar(ws.cell(row=fila_x, column=c), "sub", lambda celda: (
                setattr(celda, "fill", F_SUB), setattr(celda, "border", B_FINO), setattr(celda, "font", f_sub),
                setattr(celda, "alignment", Alignment(horizontal="right", vertical="center"))))
        lab = ws.cell(row=fila_x, column=col_label, value=etiqueta)
        lab.alignment = Alignment(horizontal="left", vertical="center")
        for c in sumas:
            letra = get_column_letter(c)
            celda = ws.cell(row=fila_x, column=c, value=f"=SUBTOTAL(9,{letra}{desde}:{letra}{hasta})")
            fmt = FMT_DECIMAL if decimales[columnas[c - 1]] else FMT_ENTERO
            estilos.aplicar(celda, ("sub", fmt), lambda celda, fmt=fmt: setattr(celda, "number_format", fmt))
        fila_x += 1

    estilos = _Estilos()
    for fila in filas:
        dia = _fecha(fila.get("fecha_parte"))
        if subtotales and dia_actual is not None and dia != dia_actual:
            subtotal(inicio_dia, fila_x - 1, dia_actual)
            inicio_dia = fila_x
        dia_actual = dia
        relleno = F_ALT if alterna % 2 else None
        alterna += 1
        for i, (campo, _, tipo) in enumerate(defs, start=1):
            v = valor(fila, campo)
            celda = ws.cell(row=fila_x, column=i, value=v)
            fmt = None
            if tipo == "fecha" and isinstance(v, date):
                fmt = FMT_FECHA
            elif tipo == "fecha_hora" and isinstance(v, datetime):
                fmt = FMT_FECHA_HORA
            elif tipo == "num" and isinstance(v, (int, float)):
                fmt = _fmt_num(v)
                if fmt == FMT_DECIMAL:
                    decimales[campo] = True
            est = v if tipo == "estado" and v in ESTADO_ESTILO else None

            def armar(celda, tipo=tipo, fmt=fmt, est=est, relleno=relleno):
                celda.font, celda.border, celda.alignment = f_dato, B_FINO, al[tipo]
                if relleno is not None:
                    celda.fill = relleno
                if fmt:
                    celda.number_format = fmt
                if est:
                    fill, color = ESTADO_ESTILO[est]
                    celda.fill, celda.font = fill, _f(10, True, color)

            estilos.aplicar(celda, (tipo, fmt, est, relleno is not None), armar)
            largo = 10 if tipo in ("fecha", "fecha_hora") and not isinstance(v, str) else len(str(v)) if v is not None else 0
            if tipo == "fecha_hora" and isinstance(v, datetime):
                largo = 16
            largos[i - 1] = max(largos[i - 1], largo)
        fila_x += 1
    if subtotales and dia_actual is not None:
        subtotal(inicio_dia, fila_x - 1, dia_actual)
    ultima_dato = fila_x - 1

    # Total general (SUBTOTAL ignora los subtotales por día y respeta el autofiltro).
    for c in range(1, n + 1):
        celda = ws.cell(row=fila_x, column=c)
        celda.fill, celda.font, celda.border = F_TOT, f_tot, B_FINO
        celda.alignment = Alignment(horizontal="right", vertical="center")
    lab = ws.cell(row=fila_x, column=col_label, value=f"TOTAL GENERAL · {len(filas)} partes")
    lab.alignment = Alignment(horizontal="left", vertical="center")
    for c in sumas:
        letra = get_column_letter(c)
        celda = ws.cell(row=fila_x, column=c, value=f"=SUBTOTAL(9,{letra}{FILA_HDR + 1}:{letra}{ultima_dato})")
        celda.number_format = FMT_DECIMAL if decimales[columnas[c - 1]] else FMT_ENTERO
    ws.row_dimensions[fila_x].height = 20
    fila_total = fila_x

    # Anchos: según el contenido, con tope 50 para los textos largos y 32 para el resto.
    for i, (campo, titulo, tipo) in enumerate(defs, start=1):
        tope = 50 if tipo == "largo" else 32
        base = 12 if tipo in ("fecha", "fecha_hora") else 8
        ws.column_dimensions[get_column_letter(i)].width = max(base, min(tope, largos[i - 1] + 2))
    if ws.column_dimensions["A"].width < 10:
        ws.column_dimensions["A"].width = 10  # el logo va anclado en A1
    for c in range(n + 1, ancho + 1):
        ws.column_dimensions[get_column_letter(c)].width = 12

    ws.freeze_panes = ws.cell(row=FILA_HDR + 1, column=1)
    ws.auto_filter.ref = f"A{FILA_HDR}:{get_column_letter(n)}{ultima_dato}"
    _impresion(ws, ancho, fila_total, f"1:{FILA_HDR}")


# ---------------------------------------------------------------- hoja Resumen
def _hoja_resumen(ws, filas, meta):
    ANCHO = 14
    for c in range(1, ANCHO + 1):
        ws.column_dimensions[get_column_letter(c)].width = 10
    membrete(ws, ANCHO, "PARTES DIARIOS DE EQUIPOS", meta)
    vehiculos = meta.get("vehiculos") or {}

    partes = len(filas)
    hs = sum(_n(f.get("total_hs")) for f in filas)
    km = sum(_n(f.get("km_total")) for f in filas)
    gasoil = sum(_n(f.get("gasoil")) for f in filas)
    controlados = sum(1 for f in filas if str(f.get("control") or "").strip())
    equipos = {str(f.get("ci") or "").strip().upper() for f in filas if str(f.get("ci") or "").strip()}
    operadores = {str(f.get("legajo_empleado") or "").strip() for f in filas if str(f.get("legajo_empleado") or "").strip()}
    pct = controlados / partes if partes else 0

    # Tarjetas: 7 de 2 columnas; el borde blanco grueso las separa como mosaicos.
    blanco = Side(style="thick", color="FFFFFF")
    acento = Side(style="medium", color=AZUL_HDR)
    tarjetas = [
        ("PARTES", partes, FMT_ENTERO, f"{len({f.get('fecha_parte') for f in filas})} días con partes"),
        ("HORAS TRABAJADAS", hs, FMT_ENTERO if float(hs).is_integer() else FMT_DECIMAL,
         f"{hs / partes:.1f} hs por parte".replace(".", ",") if partes else ""),
        ("KM RECORRIDOS", km, FMT_ENTERO, ""),
        ("GASOIL (L)", gasoil, FMT_ENTERO if float(gasoil).is_integer() else FMT_DECIMAL, ""),
        ("% CONTROLADOS", pct, "0%", f"{controlados} de {partes} partes"),
        ("EQUIPOS", len(equipos), FMT_ENTERO, "Con partes en el período"),
        ("OPERADORES", len(operadores), FMT_ENTERO, "Con partes en el período"),
    ]
    for fila, alto in ((8, 16), (9, 30), (10, 15)):
        ws.row_dimensions[fila].height = alto
    for i, (et, v, fmt, sub) in enumerate(tarjetas):
        c1, c2 = 1 + i * 2, 2 + i * 2
        _merge(ws, c1, 8, c2, 8, et, fill=F_CEL, font=_f(8, True, GRIS_TXT), align=Alignment(horizontal="center", vertical="bottom"))
        cel = _merge(ws, c1, 9, c2, 9, v, fill=F_CEL, font=_f(20, True, AZUL), align=Alignment(horizontal="center", vertical="center"))
        cel.number_format = fmt
        _merge(ws, c1, 10, c2, 10, sub, fill=F_CEL, font=_f(8, color=GRIS_TXT), align=Alignment(horizontal="center", vertical="top"))
        for f in (8, 9, 10):
            for c in (c1, c2):
                ws.cell(row=f, column=c).border = Border(
                    left=blanco if c == c1 else None, right=blanco if c == c2 else None,
                    top=acento if f == 8 else None, bottom=blanco if f == 10 else None)

    def titulo(fila, texto):
        celda = ws.cell(row=fila, column=1, value=texto)
        celda.font = _f(12, True, AZUL)
        ws.row_dimensions[fila].height = 20

    def tabla(fila, cols, datos, total=None):
        """cols = [(encabezado, c1, c2, tipo, formato)]; datos = filas de valores en ese orden."""
        for et, c1, c2, tipo, _ in cols:
            _merge(ws, c1, fila, c2, fila, et, fill=F_HDR, border=B_FINO, font=_f(10, True, "FFFFFF"),
                   align=Alignment(horizontal="center", vertical="center", wrap_text=True))
        ws.row_dimensions[fila].height = 22
        for k, valores in enumerate(datos):
            f = fila + 1 + k
            relleno = F_ALT if k % 2 else PatternFill(fill_type=None)
            for (et, c1, c2, tipo, fmt), v in zip(cols, valores):
                al = Alignment(horizontal="right" if tipo == "num" else "center" if tipo == "centro" else "left", vertical="center")
                cel = _merge(ws, c1, f, c2, f, v, fill=relleno, border=B_FINO, font=_f(10), align=al)
                if fmt:
                    cel.number_format = fmt
        f = fila + 1 + len(datos)
        if total:
            for (et, c1, c2, tipo, fmt), v in zip(cols, total):
                al = Alignment(horizontal="right" if tipo == "num" else "left", vertical="center")
                cel = _merge(ws, c1, f, c2, f, v, fill=F_TOT, border=B_FINO, font=_f(10, True, "FFFFFF"), align=al)
                if fmt:
                    cel.number_format = fmt
            f += 1
        return f

    # Resumen por turno
    fila = 12
    titulo(fila, "Resumen por turno")
    turnos = {}
    for f in filas:
        t = turno_normalizado(f.get("turno")) or "Sin turno"
        t = t if t in ("Día", "Noche") else "Otro / sin turno"
        a = turnos.setdefault(t, [0, 0.0])
        a[0] += 1
        a[1] += _n(f.get("total_hs"))
    orden_t = [t for t in ("Día", "Noche", "Otro / sin turno") if t in turnos]
    fmt_hs = FMT_ENTERO if all(_n(f.get("total_hs")).is_integer() for f in filas) else FMT_DECIMAL
    cols_t = [("Turno", 1, 2, "texto", None), ("Partes", 3, 4, "num", FMT_ENTERO),
              ("Horas", 5, 6, "num", fmt_hs), ("% de partes", 7, 8, "num", "0%")]
    datos_t = [[t, turnos[t][0], turnos[t][1], (turnos[t][0] / partes) if partes else 0] for t in orden_t]
    fila = tabla(fila + 1, cols_t, datos_t, ["Total", partes, hs, 1 if partes else 0])

    # Resumen por equipo
    fila += 1
    titulo(fila, "Resumen por equipo")
    por_eq = {}
    for f in filas:
        eq = str(f.get("ci") or "").strip().upper() or "(sin equipo)"
        a = por_eq.setdefault(eq, [0, 0.0, 0.0, 0.0, 0])
        a[0] += 1
        a[1] += _n(f.get("total_hs"))
        a[2] += _n(f.get("km_total"))
        a[3] += _n(f.get("gasoil"))
        a[4] += 0 if str(f.get("control") or "").strip() else 1
    orden_eq = sorted(por_eq, key=_clave_natural)
    cols_e = [("Equipo", 1, 1, "centro", None), ("Descripción", 2, 6, "texto", None), ("Partes", 7, 8, "num", FMT_ENTERO),
              ("Horas", 9, 10, "num", fmt_hs), ("Km", 11, 12, "num", FMT_ENTERO), ("Gasoil (l)", 13, 13, "num", FMT_ENTERO),
              ("Pendientes", 14, 14, "num", FMT_ENTERO)]
    datos_e = [[eq, vehiculos.get(eq) or "", *por_eq[eq]] for eq in orden_eq]
    inicio_eq = fila + 2
    fila = tabla(fila + 1, cols_e, datos_e,
                 ["Total", "", partes, hs, km, gasoil, partes - controlados])
    fin_eq = inicio_eq + len(datos_e) - 1

    # Gráfico de barras: horas por equipo (colores corporativos).
    ultima = fila - 1
    if datos_e:
        graf = BarChart()
        graf.type = "col"
        graf.title = "Horas por equipo"
        graf.style = 10
        graf.legend = None
        graf.y_axis.title = "Horas"
        graf.y_axis.numFmt = "#,##0"
        graf.y_axis.majorGridlines = None
        graf.x_axis.delete = False
        graf.y_axis.delete = False
        graf.add_data(Reference(ws, min_col=9, min_row=inicio_eq, max_row=fin_eq), titles_from_data=False)
        graf.set_categories(Reference(ws, min_col=1, min_row=inicio_eq, max_row=fin_eq))
        serie = graf.series[0]
        serie.graphicalProperties.solidFill = AZUL_HDR
        serie.graphicalProperties.line.solidFill = AZUL
        graf.gapWidth = 60
        graf.width = 34
        graf.height = 9
        fila += 1
        ws.add_chart(graf, f"A{fila}")
        ultima = fila + 18
    _impresion(ws, ANCHO, ultima, "1:6")


# ---------------------------------------------------------------- archivo
def nombre_archivo(obra: str, desde: date, hasta: date) -> str:
    obra = str(obra or "").strip()
    codigo = obra.split(" - ")[0].strip() if obra else "Todas"
    codigo = re.sub(r"[^0-9A-Za-z_-]+", "_", _fold(codigo)).strip("_") or "Obra"
    return f"SMG_Partes_{codigo}_{desde:%Y-%m-%d}_{hasta:%Y-%m-%d}.xlsx"


def _empresa_en_propiedades(contenido: bytes, empresa: str) -> bytes:
    """openpyxl no expone la empresa (docProps/app.xml): se agrega al archivo ya generado."""
    origen = zipfile.ZipFile(io.BytesIO(contenido))
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        for item in origen.infolist():
            datos = origen.read(item.filename)
            if item.filename == "docProps/app.xml" and b"<Company>" not in datos:
                esc = empresa.replace("&", "&amp;").replace("<", "&lt;").encode("utf-8")
                datos = datos.replace(b"</Properties>", b"<Company>" + esc + b"</Company></Properties>")
            z.writestr(item, datos)
    return salida.getvalue()


def generar(filas: list, columnas: list, meta: dict, subtotales: bool = False) -> bytes:
    """filas: dicts de horas_maquinas (ya filtrados). meta: empresa, usuario, emitido, contexto [(et, valor)],
    logo (ruta), vehiculos {ci: descripción}, titulo_archivo."""
    filas = ordenar(filas)
    wb = Workbook()
    resumen = wb.active
    resumen.title = "Resumen"
    _hoja_resumen(resumen, filas, meta)
    _hoja_partes(wb.create_sheet("Partes"), filas, columnas, meta, subtotales)

    wb.properties.title = meta.get("titulo_archivo") or "Partes diarios de equipos"
    wb.properties.subject = "Partes diarios de equipos"
    wb.properties.creator = "SMG Servicios Mineros"
    wb.properties.lastModifiedBy = str(meta.get("usuario") or "SMG Servicios Mineros")
    wb.active = 0
    for ws in wb.worksheets:
        ws.sheet_view.tabSelected = ws is resumen
    resumen.sheet_view.selection[0].activeCell = "A1"
    resumen.sheet_view.selection[0].sqref = "A1"

    out = io.BytesIO()
    wb.save(out)
    return _empresa_en_propiedades(out.getvalue(), "SMG Servicios Mineros")
