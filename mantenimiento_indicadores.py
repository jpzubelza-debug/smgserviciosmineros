"""Mantenimiento › Etapa 3: indicadores del período (solo lectura).

- Resumen: novedades, OT, horas de mecánicos, costo (mano de obra + repuestos), correctivo / preventivo, tiempos medios.
- Costo por mes y por equipo (OT finalizadas en el período; repuestos de pedidos no autorizados por Almacén no suman).
- Disponibilidad por equipo: días en cada estado según mt_equipo_historial (Operativo, Funcional, Fuera de servicio).
- Preventivo: services hechos en el período y si se hicieron a tiempo (dentro del intervalo del plan).
- Fallas que se repiten: equipos con más novedades y temas más frecuentes (palabras de las descripciones).
- Mecánicos: horas del cronómetro, OT trabajadas y alertas contra la Tarja.
"""
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import mantenimiento as mt

PANEL = "indicadores"
_COSTO_REPUESTOS_SQL = """
    SELECT r.ot_id, SUM(r.cantidad * r.costo_unitario) AS costo
    FROM mt_ot_repuestos r
    LEFT JOIN remitos_entrega_detalle d ON d.id = r.remito_detalle_id
    LEFT JOIN remitos_entrega e ON e.id = d.remito_id
    WHERE UPPER(COALESCE(e.estado_autorizacion, '')) <> 'NO_AUTORIZADO'
    GROUP BY r.ot_id"""
_VACIAS_TEMAS = mt._PALABRAS_VACIAS | set("""
    tiene tienen presenta presentan esta estan para como pero porque cuando donde desde hasta sobre entre mismo misma
    equipo equipos falla fallas problema problemas cambio cambiar revisar revision novedad check list item items
""".split())


def _fecha(valor, defecto):
    if not valor:
        return defecto
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        mt._err("VALIDACION", "Fecha inválida.")


def _dt(texto):
    return datetime.strptime(str(texto)[:19], "%Y-%m-%d %H:%M:%S") if len(str(texto)) >= 19 else datetime.fromisoformat(str(texto)[:10])


def _costos_repuestos(conn):
    try:
        return {f["ot_id"]: f["costo"] or 0 for f in conn.execute(_COSTO_REPUESTOS_SQL)}
    except Exception:  # instalaciones sin Almacén
        return {f[0]: f[1] or 0 for f in conn.execute("SELECT ot_id, SUM(cantidad * costo_unitario) FROM mt_ot_repuestos GROUP BY ot_id")}


def _vehiculos(conn):
    return {f["codigo"].strip().upper(): f for f in conn.execute(
        f"SELECT codigo, marca, modelo, tipo, operativo FROM vehiculos WHERE TRIM(COALESCE(codigo, '')) <> '' AND {mt.VEHICULO_ACTIVO_SQL}")}


def _display(vehiculos, ci):
    v = vehiculos.get(str(ci or "").upper())
    return mt._equipo_display(v) if v else ci


def _disponibilidad(conn, vehiculos, desde, hasta):
    """Días en cada estado dentro del período, con el historial de cambios de estado de cada equipo."""
    inicio = datetime.combine(desde, datetime.min.time())
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time())
    total = (fin - inicio).total_seconds() / 86400
    cambios = defaultdict(list)
    for f in conn.execute("SELECT ci, anterior, nuevo, creado_en FROM mt_equipo_historial WHERE campo = 'ESTADO' ORDER BY creado_en, id"):
        cambios[f["ci"].upper()].append(f)
    actuales = {f["ci"].upper(): f["estado"] for f in conn.execute("SELECT ci, estado FROM mt_equipos")}
    salida = []
    for clave, v in vehiculos.items():
        lista = cambios.get(clave, [])
        previos = [c for c in lista if _dt(c["creado_en"]) < inicio]
        if previos:
            estado = previos[-1]["nuevo"]
        elif lista:
            estado = lista[0]["anterior"] or "OPERATIVO"
        else:
            defecto = "FUERA_DE_SERVICIO" if str(v["operativo"] or "").strip().upper() == "NO" else "OPERATIVO"
            estado = actuales.get(clave) or defecto
        dias = {e: 0.0 for e in mt.ESTADOS_EQUIPO}
        cursor = inicio
        for c in lista:
            momento = _dt(c["creado_en"])
            if momento < inicio:
                continue
            if momento >= fin:
                break
            dias[estado] = dias.get(estado, 0) + (momento - cursor).total_seconds() / 86400
            cursor, estado = momento, c["nuevo"]
        dias[estado] = dias.get(estado, 0) + (fin - cursor).total_seconds() / 86400
        fs = dias["FUERA_DE_SERVICIO"]
        salida.append({"ci": v["codigo"].strip(), "display": mt._equipo_display(v),
                       "dias": {k: round(x, 1) for k, x in dias.items()},
                       "disponibilidad": round(100 * (1 - fs / total), 1) if total else 100.0,
                       "cambios": sum(1 for c in lista if inicio <= _dt(c["creado_en"]) < fin)})
    salida.sort(key=lambda r: (r["disponibilidad"], -r["dias"]["FUNCIONAL"], r["ci"]))
    flota = round(sum(r["disponibilidad"] for r in salida) / len(salida), 1) if salida else None
    return flota, salida


def _preventivo(conn, desde, hasta):
    """Services del período: a tiempo si el uso desde el service anterior no pasó el intervalo del plan."""
    filas = conn.execute(
        """SELECT h.*, a.plan_id, a.ci, p.nombre AS plan, p.intervalo_hs, p.intervalo_km, p.intervalo_dias
           FROM mt_prev_historial h JOIN mt_prev_equipos a ON a.id = h.asignacion_id JOIN mt_prev_planes p ON p.id = a.plan_id
           ORDER BY h.asignacion_id, h.fecha, h.id""").fetchall()
    previo, services = {}, []
    for h in filas:
        anterior = previo.get(h["asignacion_id"])
        previo[h["asignacion_id"]] = h
        if h["origen"] == "ALTA" or not (desde.isoformat() <= h["fecha"][:10] <= hasta.isoformat()) or not anterior:
            continue
        excesos = []
        for clave, intervalo, actual, base in (("hs", h["intervalo_hs"], h["hs"], anterior["hs"]),
                                               ("km", h["intervalo_km"], h["km"], anterior["km"])):
            if intervalo and actual is not None and base is not None and actual - base > intervalo:
                excesos.append(f"{actual - base - intervalo:g} {'h' if clave == 'hs' else 'km'}")
        if h["intervalo_dias"]:
            usados = (date.fromisoformat(h["fecha"][:10]) - date.fromisoformat(anterior["fecha"][:10])).days
            if usados > h["intervalo_dias"]:
                excesos.append(f"{usados - h['intervalo_dias']} días")
        services.append({"ci": h["ci"], "plan": h["plan"], "fecha": h["fecha"][:10], "a_tiempo": not excesos,
                         "exceso": ", ".join(excesos), "origen": h["origen"]})
    return services


def _temas(novedades):
    conteo, forma, equipos = Counter(), {}, defaultdict(set)
    for n in novedades:
        vistas = set()
        for palabra in re.findall(r"[a-záéíóúüñ]+", str(n["descripcion"] or "").lower()):
            clave = mt._sin_acentos(palabra)
            if len(clave) < 4 or clave in _VACIAS_TEMAS or clave in vistas:
                continue
            vistas.add(clave)
            conteo[clave] += 1
            forma.setdefault(clave, Counter())[palabra] += 1
            equipos[clave].add(n["ci"])
    return [{"tema": forma[k].most_common(1)[0][0], "novedades": c, "equipos": len(equipos[k])}
            for k, c in conteo.most_common(12) if c >= 2]


def indicadores(conn, desde=None, hasta=None):
    hasta = _fecha(hasta, mt.hoy())
    desde = _fecha(desde, hasta - timedelta(days=89))
    if desde > hasta:
        mt._err("VALIDACION", "La fecha «desde» es posterior a «hasta».")
    if (hasta - desde).days > 3 * 366:
        mt._err("VALIDACION", "El período máximo es de 3 años.")
    d, h = desde.isoformat(), hasta.isoformat()
    vehiculos = _vehiculos(conn)
    valor_hora = mt._valor_hora(conn)
    rep = _costos_repuestos(conn)

    # --- OT finalizadas en el período: costo y tiempos ---
    ots = conn.execute("""SELECT o.*, n.creada_en AS novedad_creada FROM mt_ordenes o LEFT JOIN mt_novedades n ON n.id = o.novedad_id
                          WHERE o.estado = 'FINALIZADA' AND substr(o.finalizada_en, 1, 10) BETWEEN ? AND ?""", (d, h)).fetchall()
    por_mes = defaultdict(lambda: {"correctivo": 0.0, "preventivo": 0.0})
    por_equipo = defaultdict(lambda: {"ots": 0, "horas": 0.0, "mano_obra": 0.0, "repuestos": 0.0, "correctivo": 0.0, "preventivo": 0.0})
    costo = {"mano_obra": 0.0, "repuestos": 0.0, "correctivo": 0.0, "preventivo": 0.0}
    duraciones, resoluciones, cant_tipo = [], [], Counter()
    for o in ots:
        horas = mt._minutos(conn, o["id"]) / 60
        mo, rp = horas * valor_hora, float(rep.get(o["id"], 0) or 0)
        tipo = "preventivo" if (o["tipo"] or "CORRECTIVA") == "PREVENTIVA" else "correctivo"
        cant_tipo[tipo] += 1
        costo["mano_obra"] += mo
        costo["repuestos"] += rp
        costo[tipo] += mo + rp
        por_mes[o["finalizada_en"][:7]][tipo] += mo + rp
        if o["ci"]:
            e = por_equipo[o["ci"].upper()]
            e["ots"] += 1
            e["horas"] += horas
            e["mano_obra"] += mo
            e["repuestos"] += rp
            e[tipo] += mo + rp
        duraciones.append((_dt(o["finalizada_en"]) - _dt(o["creada_en"])).total_seconds() / 86400)
        if o["novedad_creada"]:
            resoluciones.append((_dt(o["finalizada_en"]) - _dt(o["novedad_creada"])).total_seconds() / 86400)
    for n in conn.execute("""SELECT creada_en, resuelta_en FROM mt_novedades WHERE estado = 'CERRADA' AND ot_id IS NULL
                             AND resuelta_en IS NOT NULL AND substr(resuelta_en, 1, 10) BETWEEN ? AND ?""", (d, h)):
        resoluciones.append((_dt(n["resuelta_en"]) - _dt(n["creada_en"])).total_seconds() / 86400)

    meses = []
    cursor = date(desde.year, desde.month, 1)
    while cursor <= hasta:
        clave = cursor.strftime("%Y-%m")
        meses.append({"mes": clave, **{k: round(v, 2) for k, v in por_mes[clave].items()}})
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    equipos_costo = sorted(({"ci": vehiculos[k]["codigo"].strip() if k in vehiculos else k, "display": _display(vehiculos, k),
                             **{c: round(x, 2) for c, x in v.items()}, "total": round(v["mano_obra"] + v["repuestos"], 2)}
                            for k, v in por_equipo.items()), key=lambda r: -r["total"])[:15]

    # --- novedades y fallas que se repiten ---
    novedades = conn.execute("SELECT id, ci, descripcion, estado FROM mt_novedades WHERE ci IS NOT NULL AND substr(creada_en, 1, 10) BETWEEN ? AND ?",
                             (d, h)).fetchall()
    cuenta_eq = Counter(n["ci"].upper() for n in novedades)
    abiertas_eq = Counter(n["ci"].upper() for n in novedades if n["estado"] != "CERRADA")
    equipos_fallas = [{"ci": vehiculos[k]["codigo"].strip() if k in vehiculos else k, "display": _display(vehiculos, k),
                       "novedades": c, "pendientes": abiertas_eq.get(k, 0)} for k, c in cuenta_eq.most_common(10)]

    # --- mecánicos ---
    tiempos = conn.execute("""SELECT t.legajo, COALESCE(m.nombre, t.legajo) AS nombre, t.ot_id, t.minutos FROM mt_ot_tiempos t
                              LEFT JOIN mt_ot_mecanicos m ON m.ot_id = t.ot_id AND m.legajo = t.legajo
                              WHERE t.fin IS NOT NULL AND substr(t.inicio, 1, 10) BETWEEN ? AND ?""", (d, h)).fetchall()
    mecanicos = {}
    for t in tiempos:
        m = mecanicos.setdefault(t["legajo"], {"legajo": t["legajo"], "nombre": t["nombre"], "horas": 0.0, "ots": set(), "alertas": 0})
        m["horas"] += (t["minutos"] or 0) / 60
        m["ots"].add(t["ot_id"])
    for ot_id in {t["ot_id"] for t in tiempos}:
        for c in mt.control_tarja(conn, ot_id):
            if c["alerta"] and d <= c["fecha"] <= h and c["legajo"] in mecanicos:
                mecanicos[c["legajo"]]["alertas"] += 1
    lista_mec = sorted(({**m, "horas": round(m["horas"], 2), "ots": len(m["ots"])} for m in mecanicos.values()), key=lambda m: -m["horas"])

    flota, disponibilidad = _disponibilidad(conn, vehiculos, desde, min(hasta, mt.hoy()))
    services = _preventivo(conn, desde, hasta)
    prev_estado = __import__("mantenimiento_preventivo").resumen(conn)
    promedio = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    return {
        "desde": d, "hasta": h, "moneda": mt.config(conn).get("moneda") or "",
        "resumen": {
            "novedades": len(novedades),
            "novedades_cerradas_sin_ot": conn.execute(
                "SELECT COUNT(*) FROM mt_novedades WHERE estado = 'CERRADA' AND ot_id IS NULL AND substr(resuelta_en, 1, 10) BETWEEN ? AND ?", (d, h)).fetchone()[0],
            "ot_creadas": conn.execute("SELECT COUNT(*) FROM mt_ordenes WHERE substr(creada_en, 1, 10) BETWEEN ? AND ?", (d, h)).fetchone()[0],
            "ot_finalizadas": len(ots), "ot_correctivas": cant_tipo["correctivo"], "ot_preventivas": cant_tipo["preventivo"],
            "horas_mecanicos": round(sum(m["horas"] for m in lista_mec), 1),
            "costo_mano_obra": round(costo["mano_obra"], 2), "costo_repuestos": round(costo["repuestos"], 2),
            "costo_total": round(costo["mano_obra"] + costo["repuestos"], 2),
            "costo_correctivo": round(costo["correctivo"], 2), "costo_preventivo": round(costo["preventivo"], 2),
            "dias_medio_ot": promedio(duraciones), "dias_medio_resolucion": promedio(resoluciones),
            "disponibilidad_flota": flota,
            "services": len(services), "services_a_tiempo": sum(1 for s in services if s["a_tiempo"]),
            "preventivos_vencidos": prev_estado["vencidos"], "preventivos_proximos": prev_estado["proximos"],
        },
        "costo_mensual": meses, "costo_por_equipo": equipos_costo,
        "disponibilidad": [r for r in disponibilidad if r["disponibilidad"] < 100 or r["dias"]["FUNCIONAL"] > 0][:25],
        "services": services[-50:][::-1], "equipos_fallas": equipos_fallas, "temas": _temas(novedades), "mecanicos": lista_mec,
    }
