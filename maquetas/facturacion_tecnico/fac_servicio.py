"""Servicio del módulo Facturación y Cobranzas (preparación técnica, NO integrado a main.py).

Todos los importes se calculan con Decimal y redondeo HALF_UP a 2 decimales. Lo que mande el
navegador como IVA, total o equivalente se ignora: se recalcula acá. Las funciones reciben una
conexión SQLite abierta y no hacen commit; la API las envolverá en una transacción.
"""
import hashlib
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
CERO = Decimal("0")
ALICUOTAS = {"27": Decimal("0.27"), "21": Decimal("0.21"), "10.5": Decimal("0.105")}
NO_GRAVADAS = {"0": "exento", "NG": "no_gravado"}


class FacError(ValueError):
    def __init__(self, codigo, mensaje):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo


def dec(valor):
    return CERO if valor is None or valor == "" else Decimal(str(valor))


def r2(valor):
    return dec(valor).quantize(CENT, rounding=ROUND_HALF_UP)


def _num(valor):
    """Decimal -> str para guardar en columnas NUMERIC sin pasar por float."""
    return str(r2(valor))


# ---------------------------------------------------------------- configuración y auditoría

def leer_config(conn, clave):
    fila = conn.execute("SELECT valor_json FROM fac_config WHERE clave = ?", (clave,)).fetchone()
    return json.loads(fila[0]) if fila else None


def guardar_config(conn, clave, valor, usuario, ahora, es_responsable):
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo cambia la configuración")
    anterior = leer_config(conn, clave)
    conn.execute(
        "INSERT INTO fac_config (clave, valor_json, actualizado_por, actualizado_en) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor_json = excluded.valor_json, "
        "actualizado_por = excluded.actualizado_por, actualizado_en = excluded.actualizado_en",
        (clave, json.dumps(valor), usuario, ahora),
    )
    evento(conn, "CONFIG", None, "CONFIG_CAMBIADA", usuario, ahora, {"clave": clave, "antes": anterior, "despues": valor})


def evento(conn, entidad, entidad_id, nombre, usuario, ahora, detalle=None):
    conn.execute(
        "INSERT INTO fac_eventos (entidad, entidad_id, evento, usuario, fecha, detalle_json) VALUES (?, ?, ?, ?, ?, ?)",
        (entidad, entidad_id, nombre, usuario, ahora, json.dumps(detalle, default=str) if detalle is not None else None),
    )


def validar_adjuntos(conn, entidad_cfg, adjuntos):
    """Los adjuntos son opcionales salvo que la configuración los exija para esa entidad."""
    requeridos = leer_config(conn, "adjuntos_requeridos") or {}
    if requeridos.get(entidad_cfg) and not adjuntos:
        raise FacError("ADJUNTO_REQUERIDO", f"la configuración exige un adjunto para {entidad_cfg}")
    almacenamiento = leer_config(conn, "adjuntos_almacenamiento") or "ENLACE"
    for adj in adjuntos:
        if adj.get("ruta") and almacenamiento != "DISCO":
            raise FacError("ADJUNTO_ALMACENAMIENTO", "la subida de archivos está deshabilitada; use un enlace externo")
        if not adj.get("url_externa") and not adj.get("ruta"):
            raise FacError("ADJUNTO_INVALIDO", "el adjunto necesita un enlace o un archivo")


def _guardar_adjuntos(conn, entidad, entidad_id, adjuntos, usuario, ahora):
    for adj in adjuntos:
        conn.execute(
            "INSERT INTO fac_adjuntos (entidad, entidad_id, tipo, nombre, ruta, sha256, url_externa, creado_por, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entidad, entidad_id, adj.get("tipo", "COMPROBANTE"), adj.get("nombre") or adj.get("url_externa"),
             adj.get("ruta"), adj.get("sha256"), adj.get("url_externa"), usuario, ahora),
        )


def adjuntar(conn, entidad, entidad_id, adjunto, usuario, ahora):
    """Agregar un adjunto después del alta (siempre permitido, también en registros ya vigentes)."""
    validar_adjuntos(conn, None, [adjunto])
    _guardar_adjuntos(conn, entidad, entidad_id, [adjunto], usuario, ahora)
    evento(conn, entidad, entidad_id, "ADJUNTO_AGREGADO", usuario, ahora, adjunto)


# ---------------------------------------------------------------- cálculos puros

def calcular_importes(lineas, tributos=(), total_pdf=None, tolerancia=Decimal("0.05")):
    """IVA por alícuota sobre el neto agrupado, tributos y control opcional contra el total del PDF."""
    netos = {}
    for linea in lineas:
        neto = r2(linea["neto"])
        if neto <= 0:
            raise FacError("LINEA_INVALIDA", "el neto de cada línea debe ser positivo")
        alic = str(linea["alicuota_iva"])
        if alic not in ALICUOTAS and alic not in NO_GRAVADAS:
            raise FacError("ALICUOTA_INVALIDA", f"alícuota de IVA desconocida: {alic}")
        netos[alic] = netos.get(alic, CERO) + neto
    iva = {a: r2(netos[a] * ALICUOTAS[a]) for a in ALICUOTAS if a in netos}
    trib = []
    for t in tributos:
        if t.get("tipo") == "REDONDEO":
            raise FacError("REDONDEO_MANUAL", "el redondeo lo calcula el sistema a partir del total del PDF")
        importe = r2(dec(t["base"]) * dec(t["alicuota"]) / 100) if t.get("base") and t.get("alicuota") else r2(t["importe"])
        if importe <= 0:
            raise FacError("TRIBUTO_INVALIDO", "el importe del tributo debe ser positivo")
        trib.append({**t, "importe": importe})
    res = {
        "neto_gravado": sum((netos[a] for a in ALICUOTAS if a in netos), CERO),
        "exento": netos.get("0", CERO),
        "no_gravado": netos.get("NG", CERO),
        "iva_por_alicuota": iva,
        "iva_total": sum(iva.values(), CERO),
        "tributos": trib,
    }
    res["tributos_total"] = sum((t["importe"] for t in trib), CERO)
    res["total"] = res["neto_gravado"] + res["exento"] + res["no_gravado"] + res["iva_total"] + res["tributos_total"]
    res["redondeo"] = CERO
    if total_pdf not in (None, ""):
        dif = r2(total_pdf) - res["total"]
        if abs(dif) > tolerancia:
            raise FacError("TOTAL_NO_COINCIDE", f"el total calculado {res['total']} difiere del PDF de Tango en {dif}")
        if dif:
            res["redondeo"] = dif
            res["tributos"].append({"tipo": "REDONDEO", "importe": dif})
            res["tributos_total"] += dif
            res["total"] += dif
    return res


def equivalente(importe_recibo, saldo_documento, tipo_cambio):
    """Equivalente en la moneda del documento de un cobro en otra moneda (p. ej. ARS -> USD).

    Si los pesos cubren el saldo con una tolerancia de un centavo de la moneda del documento,
    el equivalente es exactamente el saldo (cancela la factura sin dejar centavos colgados).
    """
    importe_recibo, saldo_documento, tc = r2(importe_recibo), r2(saldo_documento), dec(tipo_cambio)
    if tc <= 0:
        raise FacError("TC_INVALIDO", "el tipo de cambio debe ser positivo")
    if abs(importe_recibo - r2(saldo_documento * tc)) <= r2(CENT * tc):
        return saldo_documento
    return r2(importe_recibo / tc)


def condicion_a_dias(condicion):
    """'30 FF' -> 30; 'Al día' / 'Inmediato' -> 0; otro -> None (se carga a mano)."""
    texto = (condicion or "").strip().upper()
    if texto.split(" ")[0].isdigit():
        return int(texto.split(" ")[0])
    return 0 if texto in ("AL DÍA", "AL DIA", "INMEDIATO", "CONTADO") else None


# ---------------------------------------------------------------- corte

def corte_actual(conn):
    fila = conn.execute("SELECT id, fecha_corte, estado FROM fac_cortes").fetchone()
    return {"id": fila[0], "fecha_corte": fila[1], "estado": fila[2]} if fila else None


def crear_corte(conn, usuario, ahora, fecha_corte=None, observaciones=None):
    """La fecha puede quedar vacía hasta que Contabilidad la confirme."""
    if corte_actual(conn):
        raise FacError("CORTE_EXISTE", "ya hay un corte creado")
    cur = conn.execute(
        "INSERT INTO fac_cortes (fecha_corte, observaciones, creado_por, creado_en) VALUES (?, ?, ?, ?)",
        (fecha_corte, observaciones, usuario, ahora),
    )
    evento(conn, "CORTE", cur.lastrowid, "CORTE_CREADO", usuario, ahora, {"fecha_corte": fecha_corte})
    return cur.lastrowid


def fijar_fecha_corte(conn, fecha_corte, usuario, ahora):
    corte = corte_actual(conn)
    if not corte or corte["estado"] != "BORRADOR":
        raise FacError("CORTE_CERRADO", "solo se cambia la fecha de un corte en borrador")
    fuera = conn.execute(
        "SELECT count(*) FROM fac_documentos WHERE origen = 'SALDO_INICIAL' AND estado = 'VIGENTE' AND fecha_emision > ?",
        (fecha_corte,),
    ).fetchone()[0]
    if fuera:
        raise FacError("CORTE_FECHA", f"{fuera} saldo(s) inicial(es) tienen fecha posterior a {fecha_corte}")
    conn.execute("UPDATE fac_cortes SET fecha_corte = ? WHERE id = ?", (fecha_corte, corte["id"]))
    evento(conn, "CORTE", corte["id"], "FECHA_CORTE", usuario, ahora, {"antes": corte["fecha_corte"], "despues": fecha_corte})


def _exigir_operacion_normal(conn, fecha):
    """Comprobantes y cobranzas normales: solo con corte confirmado y fecha posterior al corte."""
    corte = corte_actual(conn)
    if corte and corte["estado"] == "CONFIRMADO":
        if fecha <= corte["fecha_corte"]:
            raise FacError("ANTERIOR_AL_CORTE", f"la fecha {fecha} es del corte o anterior ({corte['fecha_corte']}); "
                                                "solo puede entrar como saldo inicial")
    elif leer_config(conn, "requiere_corte_confirmado"):
        raise FacError("SIN_CORTE", "los comprobantes y cobranzas se habilitan al confirmar el corte de saldos iniciales")


def _exigir_saldo_inicial(conn, corte_id, fecha):
    corte = corte_actual(conn)
    if not corte or corte["id"] != corte_id:
        raise FacError("CORTE_INEXISTENTE", "el saldo inicial debe pertenecer al corte vigente")
    if corte["fecha_corte"] and fecha > corte["fecha_corte"]:
        raise FacError("POSTERIOR_AL_CORTE", f"un saldo inicial no puede tener fecha posterior al corte ({corte['fecha_corte']})")
    return corte


# ---------------------------------------------------------------- saldos

def _sum(conn, sql, params):
    return sum((r2(v) for (v,) in conn.execute(sql, params).fetchall()), CERO)


def documento(conn, doc_id):
    conn_row = conn.execute(
        "SELECT id, tipo, cliente_codigo, moneda, total, estado, origen, fecha_emision, fecha_vencimiento, letra, punto_venta, numero "
        "FROM fac_documentos WHERE id = ?", (doc_id,)
    ).fetchone()
    if not conn_row:
        raise FacError("NO_EXISTE", f"comprobante {doc_id} inexistente")
    claves = ("id", "tipo", "cliente_codigo", "moneda", "total", "estado", "origen", "fecha_emision", "fecha_vencimiento",
              "letra", "punto_venta", "numero")
    d = dict(zip(claves, conn_row))
    d["total"] = r2(d["total"])
    return d


def cancelado_antes_corte(conn, doc_id):
    return _sum(conn, "SELECT importe FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,))


def cancelaciones_estimadas(conn, doc_id):
    return _sum(conn, "SELECT importe FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE' "
                      "AND criterio = 'ESTIMADO_ANTIGUEDAD'", (doc_id,))


def saldo_documento(conn, doc_id):
    d = documento(conn, doc_id)
    if d["estado"] != "VIGENTE":
        return CERO
    if d["tipo"] == "NC":
        return d["total"] - _sum(conn, "SELECT importe FROM fac_aplicaciones_nc WHERE nc_id = ? AND estado = 'VIGENTE'", (doc_id,))
    return (d["total"] - cancelado_antes_corte(conn, doc_id)
            - _sum(conn, "SELECT importe FROM fac_aplicaciones_nc WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,))
            - _sum(conn, "SELECT importe_documento FROM fac_imputaciones WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,)))


def a_cuenta_recibo(conn, recibo_id):
    fila = conn.execute("SELECT total, estado FROM fac_recibos WHERE id = ?", (recibo_id,)).fetchone()
    if not fila or fila[1] != "VIGENTE":
        return CERO
    return r2(fila[0]) - _sum(conn, "SELECT importe_recibo FROM fac_imputaciones WHERE recibo_id = ? AND estado = 'VIGENTE'",
                              (recibo_id,))


def saldo_cliente(conn, cliente, moneda):
    """Siempre por moneda: nunca se suman saldos de monedas distintas."""
    total = CERO
    for (doc_id, tipo) in conn.execute(
        "SELECT id, tipo FROM fac_documentos WHERE cliente_codigo = ? AND moneda = ? AND estado = 'VIGENTE'", (cliente, moneda)
    ).fetchall():
        s = saldo_documento(conn, doc_id)
        total += -s if tipo == "NC" else s
    for (rec_id,) in conn.execute(
        "SELECT id FROM fac_recibos WHERE cliente_codigo = ? AND moneda = ? AND estado = 'VIGENTE'", (cliente, moneda)
    ).fetchall():
        total -= a_cuenta_recibo(conn, rec_id)
    return total


def estado_documento(conn, doc_id, hoy):
    d = documento(conn, doc_id)
    if d["estado"] == "ANULADO":
        return "ANULADO"
    saldo = saldo_documento(conn, doc_id)
    if d["tipo"] == "NC":
        return "SIN APLICAR" if saldo == d["total"] else ("APLICADA" if saldo == 0 else "APLICADA PARCIAL")
    if saldo == 0:
        cobros = conn.execute("SELECT count(*) FROM fac_imputaciones WHERE documento_id = ? AND estado = 'VIGENTE'",
                              (doc_id,)).fetchone()[0]
        return "CANCELADA" if cobros or cancelado_antes_corte(conn, doc_id) else "CANCELADA POR NC"
    if d["fecha_vencimiento"] and hoy > d["fecha_vencimiento"]:
        return "VENCIDA"
    return "PENDIENTE" if saldo == d["total"] - cancelado_antes_corte(conn, doc_id) else "PARCIAL"


def antiguedad(conn, moneda, hoy):
    tramos = {"sin vencer": CERO, "1-30": CERO, "31-60": CERO, "61-90": CERO, "+90": CERO}
    for (doc_id, vto) in conn.execute(
        "SELECT id, fecha_vencimiento FROM fac_documentos WHERE moneda = ? AND estado = 'VIGENTE' AND tipo <> 'NC'", (moneda,)
    ).fetchall():
        s = saldo_documento(conn, doc_id)
        if not s:
            continue
        dias = (date.fromisoformat(hoy) - date.fromisoformat(vto)).days
        clave = "sin vencer" if dias <= 0 else "1-30" if dias <= 30 else "31-60" if dias <= 60 else "61-90" if dias <= 90 else "+90"
        tramos[clave] += s
    return tramos


# ---------------------------------------------------------------- pedidos

def guardar_pedido_borrador(conn, datos, usuario, ahora, pedido_id=None):
    if not conn.execute("SELECT 1 FROM clientes WHERE codigo = ?", (datos["cliente_codigo"],)).fetchone():
        raise FacError("CLIENTE", "cliente inexistente")
    lineas = []
    for i, l in enumerate(datos["lineas"], 1):
        sub = r2(dec(l["cantidad"]) * dec(l["precio_unitario"]))
        if l.get("certificado_id"):
            cert = conn.execute("SELECT estado, moneda FROM certificados_cliente WHERE id = ?", (l["certificado_id"],)).fetchone()
            if not cert or cert[0] != "EMITIDO":
                raise FacError("CERTIFICADO", "solo se vinculan certificados EMITIDOS")
            if cert[1] != datos["moneda"]:
                raise FacError("MONEDA", "el certificado está en otra moneda que el pedido")
        lineas.append({**l, "orden": i, "subtotal": sub})
    imp = calcular_importes([{"neto": l["subtotal"], "alicuota_iva": l["alicuota_iva"]} for l in lineas])
    campos = dict(
        tipo_solicitado=datos["tipo_solicitado"], fecha_emision=datos["fecha_emision"], cliente_codigo=datos["cliente_codigo"],
        moneda=datos["moneda"], condicion_pago=datos.get("condicion_pago"), cotizacion=datos.get("cotizacion"),
        dias_vencimiento=datos.get("dias_vencimiento", condicion_a_dias(datos.get("condicion_pago"))),
        orden_compra_cliente_id=datos.get("orden_compra_cliente_id"), oc_texto=datos.get("oc_texto"),
        centro_costo_id=datos.get("centro_costo_id"), seguimiento_cc_id=datos.get("seguimiento_cc_id"),
        tipo_cambio_informar=datos.get("tipo_cambio_informar") or ("TC de la fecha efectiva de pago" if datos["moneda"] != "ARS" else None),
        observaciones=datos.get("observaciones"), subtotal=_num(imp["neto_gravado"] + imp["exento"] + imp["no_gravado"]),
        iva_total=_num(imp["iva_total"]), total=_num(imp["total"]),
    )
    if pedido_id is None:
        cols = list(campos) + ["creado_por", "creado_en"]
        cur = conn.execute(f"INSERT INTO fac_pedidos ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [*campos.values(), usuario, ahora])
        pedido_id = cur.lastrowid
    else:
        conn.execute(f"UPDATE fac_pedidos SET {', '.join(c + ' = ?' for c in campos)}, actualizado_por = ?, actualizado_en = ? "
                     "WHERE id = ? AND estado = 'BORRADOR'", [*campos.values(), usuario, ahora, pedido_id])
        conn.execute("DELETE FROM fac_pedido_lineas WHERE pedido_id = ?", (pedido_id,))
    for l in lineas:
        conn.execute(
            "INSERT INTO fac_pedido_lineas (pedido_id, orden, cantidad, detalle, precio_unitario, alicuota_iva, subtotal, certificado_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pedido_id, l["orden"], str(dec(l["cantidad"])), l["detalle"], str(dec(l["precio_unitario"])), l["alicuota_iva"],
             _num(l["subtotal"]), l.get("certificado_id")),
        )
    evento(conn, "PEDIDO", pedido_id, "BORRADOR_GUARDADO", usuario, ahora, {"total": imp["total"]})
    return pedido_id


def emitir_pedido(conn, pedido_id, usuario, ahora, generar_pdf=None, adjuntos=()):
    """Asigna AAAAMMDD-n, congela el snapshot y el SHA-256 del PDF. generar_pdf(snapshot) -> bytes."""
    fila = conn.execute("SELECT estado, fecha_emision FROM fac_pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not fila or fila[0] != "BORRADOR":
        raise FacError("PEDIDO_ESTADO", "solo se emite un pedido en borrador")
    validar_adjuntos(conn, "PEDIDO", adjuntos)
    prefijo = fila[1].replace("-", "")
    usados = [int(n.split("-")[1]) for (n,) in conn.execute(
        "SELECT numero_solicitud FROM fac_pedidos WHERE numero_solicitud LIKE ?", (prefijo + "-%",))]
    numero = f"{prefijo}-{max(usados, default=0) + 1}"
    cab = conn.execute("SELECT * FROM fac_pedidos WHERE id = ?", (pedido_id,))
    snapshot = dict(zip([c[0] for c in cab.description], cab.fetchone()))
    lin = conn.execute("SELECT * FROM fac_pedido_lineas WHERE pedido_id = ? ORDER BY orden", (pedido_id,))
    snapshot["lineas"] = [dict(zip([c[0] for c in lin.description], r)) for r in lin.fetchall()]
    snapshot["numero_solicitud"] = numero
    snapshot_json = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    contenido = generar_pdf(snapshot) if generar_pdf else snapshot_json.encode()
    conn.execute(
        "UPDATE fac_pedidos SET estado = 'EMITIDO', numero_solicitud = ?, snapshot_json = ?, pdf_sha256 = ?, "
        "emitido_por = ?, emitido_en = ? WHERE id = ?",
        (numero, snapshot_json, hashlib.sha256(contenido).hexdigest(), usuario, ahora, pedido_id),
    )
    _guardar_adjuntos(conn, "PEDIDO", pedido_id, adjuntos, usuario, ahora)
    evento(conn, "PEDIDO", pedido_id, "EMITIDO", usuario, ahora, {"numero": numero})
    return numero


def facturado_pedido(conn, pedido_id):
    return _sum(conn, "SELECT v.neto_asignado FROM fac_documento_vinculos v JOIN fac_documentos d ON d.id = v.documento_id "
                      "WHERE v.pedido_id = ? AND d.estado = 'VIGENTE'", (pedido_id,))


# ---------------------------------------------------------------- comprobantes

def registrar_documento(conn, datos, usuario, ahora, adjuntos=()):
    tipo, moneda, origen = datos["tipo"], datos["moneda"], datos.get("origen", "NORMAL")
    if not conn.execute("SELECT 1 FROM clientes WHERE codigo = ?", (datos["cliente_codigo"],)).fetchone():
        raise FacError("CLIENTE", "cliente inexistente")
    if origen == "NORMAL":
        _exigir_operacion_normal(conn, datos["fecha_emision"])
    else:
        _exigir_saldo_inicial(conn, datos.get("corte_id"), datos["fecha_emision"])
    validar_adjuntos(conn, tipo, adjuntos)
    if conn.execute("SELECT 1 FROM fac_documentos WHERE tipo = ? AND letra = ? AND punto_venta = ? AND numero = ? "
                    "AND estado = 'VIGENTE'", (tipo, datos["letra"], datos["punto_venta"], datos["numero"])).fetchone():
        raise FacError("DUPLICADO", "ya existe un comprobante vigente con ese tipo, letra, punto de venta y número")
    tol = dec(leer_config(conn, "tolerancia_redondeo") or "0.05")
    imp = calcular_importes(datos["lineas"], datos.get("tributos", ()), datos.get("total_pdf"), tol)
    neto = imp["neto_gravado"] + imp["exento"] + imp["no_gravado"]
    vinculos = datos.get("vinculos", ())
    for clave in ("pedido_id", "certificado_id"):
        asignado = sum((r2(v["neto_asignado"]) for v in vinculos if v.get(clave)), CERO)
        if asignado > neto:
            raise FacError("VINCULO_EXCEDE", f"los vínculos a {clave[:-3]}s suman {asignado} y el neto es {neto}")
    for v in vinculos:
        if v.get("pedido_id"):
            p = conn.execute("SELECT moneda, cliente_codigo, estado FROM fac_pedidos WHERE id = ?", (v["pedido_id"],)).fetchone()
            if not p or p[2] != "EMITIDO":
                raise FacError("PEDIDO", "solo se vinculan pedidos emitidos")
            if p[0] != moneda or p[1] != datos["cliente_codigo"]:
                raise FacError("MONEDA", "el pedido vinculado tiene otra moneda o cliente")
        else:
            c = conn.execute("SELECT moneda, estado FROM certificados_cliente WHERE id = ?", (v["certificado_id"],)).fetchone()
            if not c or c[1] != "EMITIDO" or c[0] != moneda:
                raise FacError("MONEDA", "el certificado vinculado no está emitido o tiene otra moneda")
    cli = conn.execute("SELECT razon_social, cuit FROM clientes WHERE codigo = ?", (datos["cliente_codigo"],)).fetchone()
    cur = conn.execute(
        "INSERT INTO fac_documentos (tipo, letra, punto_venta, numero, origen, corte_id, motivo_alta_post_corte, cliente_codigo, "
        "cliente_razon_social, cliente_cuit, fecha_emision, fecha_vencimiento, moneda, tipo_cambio_emision, condicion_pago, detalle, "
        "neto_gravado, exento, no_gravado, iva_total, tributos_total, total, total_pdf_tango, motivo_codigo, motivo_detalle, "
        "documento_origen_id, orden_compra_cliente_id, centro_costo_id, reemplaza_a_id, nota, creado_por, creado_en) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tipo, datos["letra"], datos["punto_venta"], datos["numero"], origen, datos.get("corte_id"),
         datos.get("motivo_alta_post_corte"), datos["cliente_codigo"], cli[0], cli[1], datos["fecha_emision"],
         datos.get("fecha_vencimiento"), moneda, datos.get("tipo_cambio_emision"), datos.get("condicion_pago"), datos.get("detalle"),
         _num(imp["neto_gravado"]), _num(imp["exento"]), _num(imp["no_gravado"]), _num(imp["iva_total"]),
         _num(imp["tributos_total"]), _num(imp["total"]), datos.get("total_pdf"), datos.get("motivo_codigo"),
         datos.get("motivo_detalle"), datos.get("documento_origen_id"), datos.get("orden_compra_cliente_id"),
         datos.get("centro_costo_id"), datos.get("reemplaza_a_id"), datos.get("nota"), usuario, ahora),
    )
    doc_id = cur.lastrowid
    for l in datos["lineas"]:
        conn.execute("INSERT INTO fac_documento_lineas (documento_id, descripcion, alicuota_iva, neto) VALUES (?, ?, ?, ?)",
                     (doc_id, l.get("descripcion"), str(l["alicuota_iva"]), _num(l["neto"])))
    for t in imp["tributos"]:
        conn.execute("INSERT INTO fac_documento_tributos (documento_id, tipo, jurisdiccion, base, alicuota, importe) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (doc_id, t["tipo"], t.get("jurisdiccion"), t.get("base"), t.get("alicuota"),
                                                   _num(t["importe"])))
    for v in vinculos:
        conn.execute("INSERT INTO fac_documento_vinculos (documento_id, pedido_id, certificado_id, neto_asignado) VALUES (?, ?, ?, ?)",
                     (doc_id, v.get("pedido_id"), v.get("certificado_id"), _num(v["neto_asignado"])))
    if origen == "SALDO_INICIAL" and datos.get("cancelado_antes_corte"):
        c = datos["cancelado_antes_corte"]
        _insertar_cancelacion(conn, doc_id, c["importe"], c.get("criterio", "SEGUN_TANGO"), c.get("nota"), usuario, ahora)
    _guardar_adjuntos(conn, "DOCUMENTO", doc_id, adjuntos, usuario, ahora)
    evento(conn, "DOCUMENTO", doc_id, "REGISTRADO", usuario, ahora,
           {"total": imp["total"], "redondeo": imp["redondeo"], "origen": origen, "vinculos": list(vinculos)})
    return doc_id


def aplicar_nc(conn, nc_id, documento_id, importe, usuario, ahora):
    nc, dest, importe = documento(conn, nc_id), documento(conn, documento_id), r2(importe)
    if nc["tipo"] != "NC" or dest["tipo"] == "NC":
        raise FacError("APLICACION", "una NC se aplica a una FC o ND")
    if nc["estado"] != "VIGENTE" or dest["estado"] != "VIGENTE":
        raise FacError("APLICACION", "la NC y el destino deben estar vigentes")
    if nc["moneda"] != dest["moneda"]:
        raise FacError("MONEDA", "una NC solo se aplica a comprobantes de su misma moneda")
    if nc["cliente_codigo"] != dest["cliente_codigo"]:
        raise FacError("CLIENTE", "la NC y el comprobante son de clientes distintos")
    if importe <= 0 or importe > saldo_documento(conn, nc_id):
        raise FacError("EXCEDE_NC", "el importe supera lo disponible de la NC")
    if importe > saldo_documento(conn, documento_id):
        raise FacError("EXCEDE_SALDO", "el importe supera el saldo del comprobante")
    cur = conn.execute("INSERT INTO fac_aplicaciones_nc (nc_id, documento_id, importe, creado_por, creado_en) VALUES (?, ?, ?, ?, ?)",
                       (nc_id, documento_id, _num(importe), usuario, ahora))
    evento(conn, "DOCUMENTO", documento_id, "NC_APLICADA", usuario, ahora, {"nc_id": nc_id, "importe": importe})
    return cur.lastrowid


# ---------------------------------------------------------------- cobranzas

def sugerir_asignacion(conn, cliente, moneda_recibo, importe, tipo_cambio=None):
    """Sugerencia por vencimiento (más antiguo primero). Solo sugiere; el usuario la confirma o edita."""
    resto, sugerencia = r2(importe), []
    for (doc_id, moneda) in conn.execute(
        "SELECT id, moneda FROM fac_documentos WHERE cliente_codigo = ? AND estado = 'VIGENTE' AND tipo <> 'NC' "
        "ORDER BY fecha_vencimiento, fecha_emision, id", (cliente,)
    ).fetchall():
        if resto <= 0:
            break
        if moneda != moneda_recibo and not tipo_cambio:
            continue
        saldo = saldo_documento(conn, doc_id)
        if saldo <= 0:
            continue
        necesario = saldo if moneda == moneda_recibo else r2(saldo * dec(tipo_cambio))
        asignar = min(resto, necesario)
        sugerencia.append({"documento_id": doc_id, "importe_recibo": asignar,
                           "tipo_cambio": tipo_cambio if moneda != moneda_recibo else None})
        resto -= asignar
    return sugerencia


def registrar_recibo(conn, datos, usuario, ahora, adjuntos=()):
    origen = datos.get("origen", "NORMAL")
    if not conn.execute("SELECT 1 FROM clientes WHERE codigo = ?", (datos["cliente_codigo"],)).fetchone():
        raise FacError("CLIENTE", "cliente inexistente")
    if origen == "NORMAL":
        _exigir_operacion_normal(conn, datos["fecha_pago"])
    else:
        _exigir_saldo_inicial(conn, datos.get("corte_id"), datos["fecha_pago"])
        if datos.get("imputaciones"):
            raise FacError("ANTICIPO", "un anticipo al corte entra a cuenta, sin asignar")
    validar_adjuntos(conn, "RECIBO", adjuntos)
    if conn.execute("SELECT 1 FROM fac_recibos WHERE punto_venta = ? AND numero = ? AND estado = 'VIGENTE'",
                    (datos["punto_venta"], datos["numero"])).fetchone():
        raise FacError("DUPLICADO", "ya existe un recibo vigente con ese número")
    total = sum((r2(v["importe"]) for v in datos["valores"]), CERO)
    imputaciones = datos.get("imputaciones", ())
    if sum((r2(i["importe_recibo"]) for i in imputaciones), CERO) > total:
        raise FacError("EXCEDE_RECIBO", "lo asignado supera el total del recibo")
    cur = conn.execute(
        "INSERT INTO fac_recibos (origen, corte_id, punto_venta, numero, cliente_codigo, fecha_pago, moneda, total, observaciones, "
        "creado_por, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (origen, datos.get("corte_id"), datos["punto_venta"], datos["numero"], datos["cliente_codigo"], datos["fecha_pago"],
         datos["moneda"], _num(total), datos.get("observaciones"), usuario, ahora),
    )
    rec_id = cur.lastrowid
    for v in datos["valores"]:
        conn.execute("INSERT INTO fac_recibo_valores (recibo_id, medio, importe, banco_emisor, referencia, fecha_acreditacion, "
                     "numero_certificado_retencion) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (rec_id, v["medio"], _num(v["importe"]), v.get("banco_emisor"), v.get("referencia"),
                      v.get("fecha_acreditacion"), v.get("numero_certificado_retencion")))
    detalle = []
    for imp in imputaciones:
        d = documento(conn, imp["documento_id"])
        if d["tipo"] == "NC" or d["estado"] != "VIGENTE" or d["cliente_codigo"] != datos["cliente_codigo"]:
            raise FacError("IMPUTACION", "se imputa solo a FC/ND vigentes del mismo cliente")
        saldo, importe_recibo = saldo_documento(conn, d["id"]), r2(imp["importe_recibo"])
        if d["moneda"] == datos["moneda"]:
            tc = fecha_tc = None
            importe_doc = importe_recibo
        else:
            if not imp.get("tipo_cambio"):
                raise FacError("TC_REQUERIDO", "el comprobante está en otra moneda: cargue el TC de la fecha de pago")
            tc, fecha_tc = dec(imp["tipo_cambio"]), imp.get("fecha_tipo_cambio") or datos["fecha_pago"]
            importe_doc = equivalente(importe_recibo, saldo, tc)
        if importe_doc > saldo:
            raise FacError("EXCEDE_SALDO", f"el equivalente {importe_doc} supera el saldo {saldo} del comprobante")
        conn.execute(
            "INSERT INTO fac_imputaciones (recibo_id, documento_id, importe_recibo, tipo_cambio, fecha_tipo_cambio, importe_documento, "
            "saldo_documento_antes, saldo_documento_despues, creado_por, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rec_id, d["id"], _num(importe_recibo), str(tc) if tc else None, fecha_tc, _num(importe_doc), _num(saldo),
             _num(saldo - importe_doc), usuario, ahora),
        )
        detalle.append({"documento_id": d["id"], "importe_recibo": importe_recibo, "tc": tc, "importe_documento": importe_doc,
                        "saldo_despues": saldo - importe_doc})
    _guardar_adjuntos(conn, "RECIBO", rec_id, adjuntos, usuario, ahora)
    evento(conn, "RECIBO", rec_id, "REGISTRADO", usuario, ahora, {"total": total, "imputaciones": detalle})
    return rec_id


# ---------------------------------------------------------------- anulaciones

def _anular_fila(conn, tabla, fila_id, motivo, usuario, ahora):
    conn.execute(f"UPDATE {tabla} SET estado = 'ANULADO', anulado_por = ?, anulado_en = ?, motivo_anulacion = ? "
                 "WHERE id = ? AND estado = 'VIGENTE'", (usuario, ahora, motivo, fila_id))


def anular_documento(conn, doc_id, motivo, usuario, ahora, es_responsable):
    """Error de carga: el comprobante queda visible y deja de sumar. Sus aplicaciones, cobros y
    cancelaciones al corte se anulan en cascada; los recibos quedan y su importe vuelve a «a cuenta»."""
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo puede anular")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de anulación debe tener al menos 20 caracteres")
    cascada = {"fac_aplicaciones_nc": ("SELECT id FROM fac_aplicaciones_nc WHERE (nc_id = ? OR documento_id = ?) "
                                       "AND estado = 'VIGENTE'", (doc_id, doc_id)),
               "fac_imputaciones": ("SELECT id FROM fac_imputaciones WHERE documento_id = ? AND estado = 'VIGENTE'", (doc_id,)),
               "fac_cancelaciones_corte": ("SELECT id FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE'",
                                           (doc_id,))}
    anuladas = {}
    for tabla, (sql, params) in cascada.items():
        ids = [r[0] for r in conn.execute(sql, params).fetchall()]
        for i in ids:
            _anular_fila(conn, tabla, i, f"Cascada por anulación del comprobante {doc_id}: {motivo}", usuario, ahora)
        anuladas[tabla] = ids
    _anular_fila(conn, "fac_documentos", doc_id, motivo, usuario, ahora)
    evento(conn, "DOCUMENTO", doc_id, "ANULADO", usuario, ahora, {"motivo": motivo, "cascada": anuladas})


def anular_recibo(conn, rec_id, motivo, usuario, ahora, es_responsable):
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable del módulo puede anular")
    if len((motivo or "").strip()) < 20:
        raise FacError("MOTIVO", "el motivo de anulación debe tener al menos 20 caracteres")
    ids = [r[0] for r in conn.execute("SELECT id FROM fac_imputaciones WHERE recibo_id = ? AND estado = 'VIGENTE'", (rec_id,))]
    for i in ids:
        _anular_fila(conn, "fac_imputaciones", i, f"Cascada por anulación del recibo {rec_id}: {motivo}", usuario, ahora)
    _anular_fila(conn, "fac_recibos", rec_id, motivo, usuario, ahora)
    evento(conn, "RECIBO", rec_id, "ANULADO", usuario, ahora, {"motivo": motivo, "imputaciones": ids})


# ---------------------------------------------------------------- saldos iniciales: cancelado antes del corte

def _insertar_cancelacion(conn, doc_id, importe, criterio, nota, usuario, ahora, revision_de_id=None):
    d, importe = documento(conn, doc_id), r2(importe)
    if d["origen"] != "SALDO_INICIAL" or d["tipo"] == "NC":
        raise FacError("CANCELACION", "solo las FC/ND de saldo inicial tienen cancelado antes del corte")
    if importe >= d["total"]:
        raise FacError("CANCELACION", "lo cancelado antes del corte debe ser menor que el total (si no, no está abierto)")
    if importe > saldo_documento(conn, doc_id):
        raise FacError("CANCELACION", "lo cancelado antes del corte supera el saldo disponible del comprobante")
    conn.execute("INSERT INTO fac_cancelaciones_corte (documento_id, importe, criterio, revision_de_id, nota, creado_por, creado_en) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (doc_id, _num(importe), criterio, revision_de_id, nota, usuario, ahora))


def estimar_por_antiguedad(conn, corte_id, cliente, moneda, saldo_tango, usuario, ahora):
    """Cuando Tango no informa qué facturas cubrió cada recibo: reparte la diferencia entre lo facturado
    abierto y el saldo de Tango, cancelando primero lo más antiguo. Queda marcado ESTIMADO_ANTIGUEDAD:
    no es una imputación real y se revisa con revisar_cancelaciones()."""
    corte = _exigir_saldo_inicial(conn, corte_id, "0000-00-00")
    if corte["estado"] != "BORRADOR":
        raise FacError("CORTE_CERRADO", "con el corte confirmado se usa revisar_cancelaciones()")
    docs = conn.execute(
        "SELECT id FROM fac_documentos WHERE corte_id = ? AND origen = 'SALDO_INICIAL' AND cliente_codigo = ? AND moneda = ? "
        "AND tipo <> 'NC' AND estado = 'VIGENTE' ORDER BY fecha_emision, id", (corte_id, cliente, moneda)
    ).fetchall()
    for (doc_id,) in docs:
        if cancelado_antes_corte(conn, doc_id):
            raise FacError("CANCELACION_EXISTE", "ya hay cancelaciones cargadas para este cliente y moneda; revíselas")
    # Saldo SMG sin cancelaciones (las NC y anticipos al corte ya restan) contra el saldo según Tango.
    exceso = saldo_al_corte(conn, corte_id, cliente, moneda)[0] - r2(saldo_tango)
    if exceso < 0:
        raise FacError("FALTAN_COMPROBANTES", f"el saldo de Tango supera lo cargado en {-exceso}: falta cargar comprobantes")
    resto, filas = exceso, []
    for (doc_id,) in docs:
        if resto <= 0:
            break
        d = documento(conn, doc_id)
        importe = min(resto, d["total"])
        if importe >= d["total"]:
            raise FacError("DOCUMENTO_CANCELADO", f"{d['tipo']} {d['punto_venta']}-{d['numero']} quedaría cancelado al corte: "
                                                  "no debería cargarse como abierto")
        _insertar_cancelacion(conn, doc_id, importe, "ESTIMADO_ANTIGUEDAD",
                              "Estimado por antigüedad: Tango no informa la imputación de los recibos", usuario, ahora)
        filas.append({"documento_id": doc_id, "importe": importe})
        resto -= importe
    evento(conn, "CORTE", corte_id, "CANCELADO_ESTIMADO", usuario, ahora,
           {"cliente": cliente, "moneda": moneda, "saldo_tango": r2(saldo_tango), "filas": filas})
    return filas


def revisar_cancelaciones(conn, cliente, moneda, nuevas, criterio, nota, usuario, ahora, es_responsable):
    """Reemplaza las cancelaciones al corte de los comprobantes indicados ({doc_id: importe}; 0 = sin cancelación).
    Con el corte confirmado solo un responsable puede hacerlo y el total por cliente y moneda no cambia
    (se redistribuye entre comprobantes, no se altera el saldo conciliado con Tango)."""
    if criterio not in ("SEGUN_TANGO", "REVISADO"):
        raise FacError("CRITERIO", "una revisión queda como SEGUN_TANGO o REVISADO")
    if len((nota or "").strip()) < 10:
        raise FacError("NOTA", "la revisión requiere una nota de al menos 10 caracteres")
    corte = corte_actual(conn)
    confirmado = corte and corte["estado"] == "CONFIRMADO"
    if confirmado and not es_responsable:
        raise FacError("PERMISO", "con el corte confirmado solo un responsable revisa las cancelaciones")
    antes = despues = CERO
    for doc_id, importe in nuevas.items():
        d = documento(conn, doc_id)
        if d["cliente_codigo"] != cliente or d["moneda"] != moneda:
            raise FacError("CLIENTE", "todos los comprobantes de la revisión deben ser del mismo cliente y moneda")
        antes += cancelado_antes_corte(conn, doc_id)
        despues += r2(importe)
    if confirmado and antes != despues:
        raise FacError("TOTAL_CONCILIADO", f"con el corte confirmado el total cancelado debe seguir siendo {antes} (queda {despues})")
    for doc_id, importe in nuevas.items():
        previas = [r[0] for r in conn.execute("SELECT id FROM fac_cancelaciones_corte WHERE documento_id = ? AND estado = 'VIGENTE'",
                                              (doc_id,))]
        for p in previas:
            _anular_fila(conn, "fac_cancelaciones_corte", p, f"Revisión de lo cancelado antes del corte: {nota}", usuario, ahora)
        if r2(importe) > 0:
            _insertar_cancelacion(conn, doc_id, importe, criterio, nota, usuario, ahora, previas[0] if previas else None)
    evento(conn, "CORTE", corte["id"] if corte else None, "CANCELADO_REVISADO", usuario, ahora,
           {"cliente": cliente, "moneda": moneda, "criterio": criterio, "nuevas": nuevas, "nota": nota})


def cargar_conciliacion(conn, corte_id, cliente, moneda, saldo_tango, nota, usuario, ahora):
    conn.execute(
        "INSERT INTO fac_corte_conciliacion (corte_id, cliente_codigo, moneda, saldo_tango, nota, cargado_por, cargado_en) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(corte_id, cliente_codigo, moneda) DO UPDATE SET "
        "saldo_tango = excluded.saldo_tango, nota = excluded.nota, cargado_por = excluded.cargado_por, cargado_en = excluded.cargado_en",
        (corte_id, cliente, moneda, _num(saldo_tango), nota, usuario, ahora),
    )


def saldo_al_corte(conn, corte_id, cliente, moneda):
    """Saldo SMG al corte (solo registros SALDO_INICIAL) y cuánto de lo cancelado es estimado."""
    smg = estimado = CERO
    for (doc_id, tipo) in conn.execute("SELECT id, tipo FROM fac_documentos WHERE corte_id = ? AND cliente_codigo = ? "
                                       "AND moneda = ? AND estado = 'VIGENTE'", (corte_id, cliente, moneda)).fetchall():
        pendiente = documento(conn, doc_id)["total"] - cancelado_antes_corte(conn, doc_id)
        smg += -pendiente if tipo == "NC" else pendiente
        estimado += cancelaciones_estimadas(conn, doc_id)
    smg -= _sum(conn, "SELECT total FROM fac_recibos WHERE corte_id = ? AND cliente_codigo = ? AND moneda = ? "
                      "AND estado = 'VIGENTE'", (corte_id, cliente, moneda))
    return smg, estimado


def conciliacion(conn, corte_id):
    """Por cliente y moneda: saldo SMG al corte vs. saldo Tango; señala si incluye montos estimados."""
    claves = {tuple(r) for r in conn.execute(
        "SELECT cliente_codigo, moneda FROM fac_documentos WHERE corte_id = ? AND estado = 'VIGENTE' "
        "UNION SELECT cliente_codigo, moneda FROM fac_recibos WHERE corte_id = ? AND estado = 'VIGENTE' "
        "UNION SELECT cliente_codigo, moneda FROM fac_corte_conciliacion WHERE corte_id = ?", (corte_id, corte_id, corte_id))}
    filas = []
    for cliente, moneda in sorted(claves):
        smg, estimado = saldo_al_corte(conn, corte_id, cliente, moneda)
        tango = conn.execute("SELECT saldo_tango, nota FROM fac_corte_conciliacion WHERE corte_id = ? AND cliente_codigo = ? "
                             "AND moneda = ?", (corte_id, cliente, moneda)).fetchone()
        dif = None if tango is None else smg - r2(tango[0])
        filas.append({"cliente": cliente, "moneda": moneda, "saldo_smg": smg, "saldo_tango": None if tango is None else r2(tango[0]),
                      "diferencia": dif, "nota": tango[1] if tango else None, "cancelado_estimado": estimado,
                      "conciliada": dif is not None and (abs(dif) <= CENT or bool((tango[1] or "").strip()))})
    return filas


def confirmar_corte(conn, usuario, ahora, es_responsable):
    if not es_responsable:
        raise FacError("PERMISO", "solo un responsable confirma el corte")
    corte = corte_actual(conn)
    if not corte or corte["estado"] != "BORRADOR":
        raise FacError("CORTE_ESTADO", "no hay un corte en borrador")
    if not corte["fecha_corte"]:
        raise FacError("CORTE_FECHA", "la fecha de corte está pendiente de confirmar con Contabilidad")
    validar_adjuntos(conn, "CORTE", conn.execute("SELECT id FROM fac_adjuntos WHERE entidad = 'CORTE' AND entidad_id = ? "
                                                 "AND estado = 'VIGENTE'", (corte["id"],)).fetchall())
    filas = conciliacion(conn, corte["id"])
    pendientes = [f"{f['cliente']} {f['moneda']}" for f in filas if not f["conciliada"]]
    if pendientes:
        raise FacError("SIN_CONCILIAR", "faltan conciliar o justificar: " + ", ".join(pendientes))
    conn.execute("UPDATE fac_cortes SET estado = 'CONFIRMADO', confirmado_por = ?, confirmado_en = ? WHERE id = ?",
                 (usuario, ahora, corte["id"]))
    evento(conn, "CORTE", corte["id"], "CONFIRMADO", usuario, ahora,
           {"fecha_corte": corte["fecha_corte"], "conciliacion": filas,
            "con_estimados": [f"{f['cliente']} {f['moneda']}" for f in filas if f["cancelado_estimado"]]})
