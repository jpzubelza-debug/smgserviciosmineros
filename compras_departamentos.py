"""Ítems de solicitudes de compra agrupados por departamento (destino de compra).

El destino de una solicitud se guarda como texto en ``proyectos.nombre`` (vía
``solicitud_compra.id_proyecto``) y históricamente se cargó libre: "27",
"TALLER ALTO LA TORRE" y "27 TALLER ALTO LA TORRE" son el mismo centro de costos.
``resolver_departamento`` lo normaliza contra la tabla maestra ``centros_costos``:

- texto que empieza con un código numérico -> se agrupa por ese código;
- texto sin código cuyo nombre coincide exacto (sin acentos ni mayúsculas) con un
  centro de costos -> se agrupa con ese código;
- lo demás ("Exar", "Pirquitas") se muestra tal cual, sin forzar agrupación;
- vacío -> "(Sin destino)".
"""

import re
import unicodedata

ESTADOS_ITEM = ["Gestionado", "Rechazado", "Pendiente de gestionar", "Pendiente"]
CLAVE_SIN_DESTINO = "sin"
ETIQUETA_SIN_DESTINO = "(Sin destino)"

# Mismo criterio que la tarjeta "Ítems por estado" del dashboard (main.compras_dashboard).
SQL_ESTADO_ITEM = """CASE
    WHEN vinculo.id IS NOT NULL OR UPPER(COALESCE(d.estado_aprobacion, '')) = 'GESTIONADO' THEN 'Gestionado'
    WHEN UPPER(COALESCE(d.estado_aprobacion, 'PENDIENTE')) = 'APROBADA' THEN 'Pendiente de gestionar'
    WHEN UPPER(COALESCE(d.estado_aprobacion, 'PENDIENTE')) = 'RECHAZADA' THEN 'Rechazado'
    ELSE 'Pendiente'
END"""

# El código debe ir separado del nombre ("27 TALLER", "27 - TALLER", "27"); "4X4" no es código.
_RE_CODIGO = re.compile(r"^\s*(\d+)(?:\s*[-–—.:]\s*|\s+|$)(.*)$", re.S)


def normalizar_texto(texto):
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto).strip().upper()


def cargar_centros_costos(conn):
    """Devuelve ({id: nombre}, {nombre_normalizado: id}) de la tabla maestra.

    Nombres repetidos en la maestra no se usan para resolver (serían ambiguos).
    """
    por_id = {}
    por_nombre = {}
    repetidos = set()
    for fila in conn.execute("SELECT id, nombre FROM centros_costos").fetchall():
        cc_id, nombre = int(fila[0]), str(fila[1] or "").strip()
        por_id[cc_id] = nombre
        clave = normalizar_texto(nombre)
        if not clave:
            continue
        if clave in por_nombre and por_nombre[clave] != cc_id:
            repetidos.add(clave)
        por_nombre[clave] = cc_id
    for clave in repetidos:
        por_nombre.pop(clave, None)
    return por_id, por_nombre


def resolver_departamento(texto, por_id, por_nombre):
    """Devuelve (clave, etiqueta, codigo_o_None, en_maestra)."""
    original = re.sub(r"\s+", " ", str(texto or "")).strip()
    if not original:
        return CLAVE_SIN_DESTINO, ETIQUETA_SIN_DESTINO, None, False
    coincidencia = _RE_CODIGO.match(original)
    if coincidencia:
        codigo = int(coincidencia.group(1))
        resto = coincidencia.group(2).strip()
        if codigo in por_id:
            return f"cc:{codigo}", f"{codigo} {por_id[codigo]}".strip(), codigo, True
        return f"cc:{codigo}", f"{codigo} {resto}".strip(), codigo, False
    codigo = por_nombre.get(normalizar_texto(original))
    if codigo is not None:
        return f"cc:{codigo}", f"{codigo} {por_id[codigo]}".strip(), codigo, True
    return "txt:" + normalizar_texto(original), original, None, False


def destino_canonico(conn, texto):
    """Texto "código nombre" del centro de costos maestro, o None si no se resuelve."""
    por_id, por_nombre = cargar_centros_costos(conn)
    _, etiqueta, codigo, en_maestra = resolver_departamento(texto, por_id, por_nombre)
    return etiqueta if en_maestra and codigo is not None else None


def filtros_items(desde=None, hasta=None, centro_costo_id=None):
    """Mismos filtros que usa la tarjeta "Ítems por estado"."""
    where, params = [], []
    if desde:
        where.append("substr(sc.fecha_solicitud, 1, 10) >= ?")
        params.append(desde)
    if hasta:
        where.append("substr(sc.fecha_solicitud, 1, 10) <= ?")
        params.append(hasta)
    if centro_costo_id is not None:
        where.append("d.id_centro_costo = ?")
        params.append(centro_costo_id)
    return where, params


def items_por_departamento(conn, desde=None, hasta=None, centro_costo_id=None):
    where, params = filtros_items(desde, hasta, centro_costo_id)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    filas = conn.execute(
        f"""
        SELECT COALESCE(pr.nombre, '') AS destino, {SQL_ESTADO_ITEM} AS estado, COUNT(*) AS total
        FROM solicitud_compra_detalle d
        INNER JOIN solicitud_compra sc ON sc.id = d.id_solicitud
        LEFT JOIN proyectos pr ON pr.id = sc.id_proyecto
        LEFT JOIN OrdenCompraDetalleSolicitud vinculo
               ON vinculo.solicitud_compra_detalle_id = d.id
        {where_sql}
        GROUP BY COALESCE(pr.nombre, ''), estado
        """,
        params,
    ).fetchall()

    por_id, por_nombre = cargar_centros_costos(conn)
    grupos = {}
    for fila in filas:
        clave, etiqueta, _, en_maestra = resolver_departamento(fila[0], por_id, por_nombre)
        grupo = grupos.setdefault(clave, {
            "clave": clave,
            "departamento": etiqueta,
            "_fija": en_maestra,
            "total": 0,
            "estados": {estado: 0 for estado in ESTADOS_ITEM},
        })
        # Código fuera de la maestra: mostrar el texto más completo que se haya cargado.
        if not grupo["_fija"] and len(etiqueta) > len(grupo["departamento"]):
            grupo["departamento"] = etiqueta
        cantidad = int(fila[2] or 0)
        grupo["estados"][fila[1]] = grupo["estados"].get(fila[1], 0) + cantidad
        grupo["total"] += cantidad

    departamentos = sorted(grupos.values(), key=lambda g: (-g["total"], g["departamento"]))
    for grupo in departamentos:
        grupo.pop("_fija", None)
    total = {"total": 0, "estados": {estado: 0 for estado in ESTADOS_ITEM}}
    for grupo in departamentos:
        total["total"] += grupo["total"]
        for estado, cantidad in grupo["estados"].items():
            total["estados"][estado] = total["estados"].get(estado, 0) + cantidad
    return {"estados": ESTADOS_ITEM, "departamentos": departamentos, "total": total}


def condicion_departamento(conn, clave):
    """(sql, params) para filtrar ítems cuya solicitud pertenece al departamento ``clave``.

    Requiere ``LEFT JOIN proyectos pr ON pr.id = sc.id_proyecto`` en la consulta.
    """
    if clave == CLAVE_SIN_DESTINO:
        return "TRIM(COALESCE(pr.nombre, '')) = ''", []
    por_id, por_nombre = cargar_centros_costos(conn)
    ids = [
        int(fila[0])
        for fila in conn.execute("SELECT id, nombre FROM proyectos").fetchall()
        if resolver_departamento(fila[1], por_id, por_nombre)[0] == clave
    ]
    if not ids:
        return "0 = 1", []
    return f"sc.id_proyecto IN ({', '.join('?' for _ in ids)})", ids


def porcentaje(cantidad, total):
    return (cantidad / total) if total else 0.0


def escribir_hoja_excel(wb, datos):
    """Agrega la hoja "Ítems por departamento" con cantidades y porcentajes."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    ws = wb.create_sheet("Ítems por departamento")
    encabezados = ["Departamento", "Total ítems"]
    for estado in datos["estados"]:
        encabezados += [estado, f"% {estado}"]
    ws.append(encabezados)

    borde = Border(*(Side(style="thin", color="D1D5DB"),) * 4)
    for celda in ws[1]:
        celda.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        celda.fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    filas = [(g["departamento"], g) for g in datos["departamentos"]]
    filas.append(("Total general", datos["total"]))
    for nombre, grupo in filas:
        valores = [nombre, grupo["total"]]
        for estado in datos["estados"]:
            cantidad = grupo["estados"].get(estado, 0)
            valores += [cantidad, porcentaje(cantidad, grupo["total"])]
        ws.append(valores)
        fila = ws.max_row
        for col, celda in enumerate(ws[fila], start=1):
            celda.border = borde
            if col > 1:
                celda.alignment = Alignment(horizontal="right")
                celda.number_format = "0.0%" if col > 2 and col % 2 == 0 else "#,##0"
            if nombre == "Total general":
                celda.font = Font(bold=True)

    ws.column_dimensions["A"].width = min(max((len(n) for n, _ in filas), default=12) + 3, 60)
    for col in range(2, len(encabezados) + 1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = 16
    ws.freeze_panes = "B2"
    return ws
