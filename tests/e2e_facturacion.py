"""Prueba de punta a punta de Facturación y Cobranzas (navegador real, base copiada).

Uso: python tests/e2e_facturacion.py [carpeta_capturas]
Levanta la aplicación sobre una COPIA de dashboard.db (la original se abre en solo lectura) y recorre con
Microsoft Edge (Playwright) los formularios: corte, pedido (borrador, edición y emisión), factura con percepción,
nota de crédito aplicada y cobranzas en pesos y en dólares. Verifica los saldos en pantalla, en la API y en la base.
La fecha de corte 2025-12-31 es SOLO un dato de prueba: la real está pendiente de Contabilidad.
"""
import hashlib
import io
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")  # consola de Windows (cp1252)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
CAPTURAS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "smg_e2e_facturacion_capturas"))
os.makedirs(CAPTURAS, exist_ok=True)
DB_ORIGEN = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
SHA_ORIGEN = hashlib.sha256(open(DB_ORIGEN, "rb").read()).hexdigest()
TMP = tempfile.mkdtemp(prefix="smg_e2e_fac_")
DB = os.path.join(TMP, "dashboard.db")
origen = sqlite3.connect(f"file:{DB_ORIGEN}?mode=ro", uri=True)
destino = sqlite3.connect(DB)
origen.backup(destino)
destino.close()
origen.close()
os.environ.update({"DATA_DIR": TMP, "APP_LOGIN_USER": "contingencia_e2e", "APP_LOGIN_PASSWORD": "clave-contingencia-e2e"})

import main  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from pypdf import PdfReader  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB), "La prueba no apunta a la copia"
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PUERTO = sock.getsockname()[1]
sock.close()
servidor = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PUERTO, log_level="warning", lifespan="off"))
threading.Thread(target=servidor.run, daemon=True).start()
while not servidor.started:
    time.sleep(0.05)
URL = f"http://127.0.0.1:{PUERTO}"
API = "/api/facturacion"
CLAVE = "Clave-Prueba-1"


def db():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


with db() as conn:
    for correo, tipo in (("ui.resp@test.local", "OPERADOR"), ("ui.consulta@test.local", "CONSULTOR")):
        conn.execute("DELETE FROM usuarios WHERE correo = ?", (correo,))
        conn.execute(
            "INSERT INTO usuarios (nombre_apellido, correo, password_hash, estado, tipo_usuario, modulos_json, paneles_json, acciones_json, "
            "bloqueado, intentos_fallidos) VALUES (?, ?, ?, 'ACTIVO', ?, ?, '{}', '{}', 0, 0)",
            (correo.split("@")[0], correo, hashlib.sha256(CLAVE.encode()).hexdigest(), tipo, json.dumps(["facturacion"])))
    uid = conn.execute("SELECT id FROM usuarios WHERE correo = 'ui.resp@test.local'").fetchone()[0]
    conn.execute("INSERT INTO responsables_modulo (modulo, usuario_id) VALUES ('facturacion', ?)", (uid,))
    # Cliente con certificados emitidos (antecedente y deuda inicial) y otro sin antecedentes para el flujo diario.
    CERT = conn.execute("SELECT c.id, s.cliente_codigo, c.total_a_facturar, c.moneda FROM certificados_cliente c JOIN seguimiento_cc s "
                        "ON s.id = c.seguimiento_cc_id WHERE c.estado = 'EMITIDO' ORDER BY c.id LIMIT 1").fetchone()
    CLI = conn.execute("SELECT codigo, razon_social FROM clientes WHERE COALESCE(cuit, '') <> '' AND COALESCE(razon_social, '') <> '' "
                       "AND codigo <> ? ORDER BY codigo LIMIT 1", (CERT["cliente_codigo"] if CERT else "",)).fetchone()
    CLI, RAZON = CLI["codigo"], CLI["razon_social"]
    # Tercer cliente, sin carga inicial: opera sin validación ni fecha de corte (seguimiento desde las operaciones nuevas).
    OTRO = conn.execute("SELECT codigo FROM clientes WHERE codigo NOT IN (?, ?) AND COALESCE(razon_social, '') <> '' ORDER BY codigo LIMIT 1",
                        (CLI, CERT["cliente_codigo"] if CERT else "")).fetchone()["codigo"]
    # Registros del módulo que ya trae la base de origen (la real tiene la v1 con un corte en borrador, un pedido y eventos):
    # se conservan y no cuentan como creados por la prueba.
    TABLAS_FAC = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'fac_%'")]
    PREVIOS = {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in ("fac_pedidos", "fac_eventos", "fac_cortes") if t in TABLAS_FAC}
    PREVIOS = {t: [tuple(f) for f in filas] for t, filas in PREVIOS.items()}
    ULTIMO_EVENTO = max((f[0] for f in PREVIOS.get("fac_eventos", [])), default=0)

RESULTADOS, ERRORES, RESPUESTAS = [], [], []


def ok(nombre, condicion, detalle=""):
    RESULTADOS.append(f"{'OK   ' if condicion else 'FALLA'} {nombre}{f' · {detalle}' if detalle else ''}")
    return condicion


def captura(pagina, nombre):
    pagina.screenshot(path=os.path.join(CAPTURAS, f"e2e_fac_{nombre}.png"), full_page=True)


def ingresar(navegador, usuario):
    contexto = navegador.new_context(viewport={"width": 1600, "height": 1000}, locale="es-AR")
    pagina = contexto.new_page()
    pagina.on("pageerror", lambda e: ERRORES.append(f"{usuario}: {e}"))
    pagina.on("response", lambda r: RESPUESTAS.append(f"{r.status} {r.request.method} {r.url.replace(URL, '')}") if r.status >= 400 else None)
    pagina.on("dialog", lambda d: d.accept())  # confirmaciones del flujo (corte, avisos): se aceptan como lo haría el usuario
    respuesta = contexto.request.post(f"{URL}/login", form={"username": usuario, "password": CLAVE}, max_redirects=0)
    assert respuesta.headers.get("location") == "/", f"Login fallido para {usuario}"
    pagina.goto(f"{URL}/facturacion")
    pagina.wait_for_function("typeof S !== 'undefined' && S.sesion && S.cat.clientes.length > 0")
    return pagina


def api(pagina, ruta):
    r = pagina.request.get(URL + API + ruta)
    assert r.ok, f"{ruta}: {r.status} {r.text()}"
    return r.json()


def ir(pagina, vista):
    pagina.click(f".menu-btn[data-vista='{vista}']")
    pagina.wait_for_function(f"S.vista === '{vista}'")
    pagina.wait_for_timeout(300)


def esperar_cierre(pagina, modal):
    pagina.wait_for_function(f"!document.getElementById('{modal}').classList.contains('open')", timeout=10000)


def fmt(pagina, valor, moneda):
    return pagina.evaluate("([v, mon]) => m(v, mon).replace(/<[^>]+>/g, '')", [valor, moneda])


def texto(pagina, selector):
    return pagina.locator(selector).inner_text()


with sync_playwright() as p:
    navegador = p.chromium.launch(channel="msedge", headless=True)
    pg = ingresar(navegador, "ui.resp@test.local")
    ok("responsable: sesión con permiso de carga y responsable", pg.evaluate("puedeCargar() && esResp()"))

    # ---------- 1. Puesta en marcha (fecha de inicio de PRUEBA) y carga inicial por cliente ----------
    ir(pg, "saldos")
    if PREVIOS.get("fac_cortes"):  # la base real ya tiene el corte en borrador sin fecha: no se ofrece crear otro
        pg.wait_for_selector("#corte-fecha")
        ok("corte existente: sin botón para iniciar otro", pg.locator("button:has-text('Iniciar puesta en marcha')").count() == 0)
    else:
        pg.click("button:has-text('Iniciar puesta en marcha (fecha pendiente)')")
        pg.wait_for_selector("#corte-fecha")
    corte0 = api(pg, "/corte")["corte"]
    ok("puesta en marcha en borrador sin fecha", corte0["fecha_corte"] is None and corte0["estado"] == "BORRADOR")

    # ---------- 0. Seguimiento desde las operaciones nuevas, sin fecha de corte: pedido → factura → cobranza ----------
    r = pg.request.post(f"{URL}{API}/pedidos", data={"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-21", "cliente_codigo": OTRO, "moneda": "USD",
                                                     "condicion_pago": "30 FF", "lineas": [{"cantidad": 1, "detalle": "Servicio sin corte (E2E)",
                                                                                            "precio_unitario": "500", "alicuota_iva": "21"}]})
    PED0 = r.json()["id"]
    ok("sin fecha de corte: pedido emitido", pg.request.post(f"{URL}{API}/pedidos/{PED0}/emitir", data={}).ok)
    pg.evaluate(f"abrirDoc({{pedido_id: {PED0}}})")
    pg.wait_for_function("document.getElementById('m-doc').classList.contains('open') && VL.length === 1")
    pg.fill("#d-pv", "6")
    pg.fill("#d-nro", "800001")
    pg.fill("#d-fe", "2026-10-21")
    pg.fill("#d-vto", "2026-11-20")
    pg.click("#d-ok")
    esperar_cierre(pg, "m-doc")
    fc0 = next(d for d in api(pg, f"/documentos?cliente={OTRO}") if d["numero"] == 800001)
    ped0 = api(pg, f"/pedidos/{PED0}")
    ok("sin fecha de corte: factura registrada desde el pedido (USD 605), pedido facturado",
       fc0["total"] == 605 and fc0["origen"] == "NORMAL" and ped0["estado_facturacion"] == "FACTURADO", f"{fc0['total']} {ped0['estado_facturacion']}")
    pg.evaluate(f"abrirCobro({{cliente: '{OTRO}'}})")
    pg.wait_for_function("document.getElementById('m-cob').classList.contains('open') && PEND_COB.length > 0")
    pg.fill("#c-nro", "800001")
    pg.fill("#c-fecha", "2026-10-25")
    pg.select_option("#c-mon", "USD")
    pg.fill("#c-val tr:nth-child(1) td:nth-child(2) input", "605")
    pg.click("#m-cob button:has-text('Sugerir por vencimiento')")
    pg.wait_for_function("[...document.querySelectorAll('#c-imp input.imp')].some(i => i.value)")
    pg.click("#c-ok")
    esperar_cierre(pg, "m-cob")
    fc0 = next(d for d in api(pg, f"/documentos?cliente={OTRO}") if d["numero"] == 800001)
    ok("sin fecha de corte: cobranza registrada y factura cancelada", fc0["saldo"] == 0 and fc0["estado_saldo"] == "CANCELADA", f"{fc0['saldo']} {fc0['estado_saldo']}")
    corte1 = api(pg, "/corte")["corte"]
    ok("operar no cambia el corte ni crea estados iniciales", (corte1["fecha_corte"], corte1["estado"]) == (None, "BORRADOR")
       and api(pg, f"/puesta-en-marcha/{OTRO}")["estado_inicial"] is None)
    pg.fill("#corte-fecha", "2025-12-31")
    pg.click("button:has-text('Guardar fecha')")
    pg.wait_for_function("S.sesion.corte && S.sesion.corte.fecha_corte === '2025-12-31'")
    pg.wait_for_selector("#pm-clientes tr.click")

    def ficha(codigo):
        pg.fill("#pm-q", codigo)
        pg.click(f"#pm-clientes tr.click:has-text('{codigo}')")
        pg.wait_for_function(f"document.getElementById('m-ini').classList.contains('open') && INI_CLI === '{codigo}'")
        pg.wait_for_timeout(300)

    # Con la fecha de la carga inicial definida, el cliente sin carga sigue operando sin validación.
    r = pg.request.post(f"{URL}{API}/documentos", data={"tipo": "FC", "letra": "A", "punto_venta": 6, "numero": 800003, "cliente_codigo": OTRO,
                                                        "fecha_emision": "2026-02-01", "fecha_vencimiento": "2026-02-01", "moneda": "USD",
                                                        "lineas": [{"descripcion": "x", "alicuota_iva": "21", "neto": "1"}]})
    ok("cliente sin carga inicial: sigue operando con la fecha de carga definida", r.status == 200, r.text())

    # Cliente en cero: se valida sin antecedentes.
    ficha(CLI)
    ok("ficha: cliente sin carga inicial", "SIN CARGA INICIAL" in texto(pg, "#ini-tit"))
    pg.click("#ini-pie button:has-text('Validar sin antecedentes')")
    pg.wait_for_function("document.getElementById('ini-tit').innerText.includes('HABILITADO')")
    ok("cliente en cero: habilitado sin antecedentes, saldo 0",
       api(pg, f"/puesta-en-marcha/{CLI}")["estado_inicial"]["situacion"] == "SIN_ANTECEDENTES"
       and all(api(pg, f"/cuenta-corriente?cliente={CLI}&moneda={mo}")["saldo"] == 0 for mo in ("ARS", "USD")))
    pg.click("#m-ini .close")

    if CERT:
        PIRQ, CERT_ID = CERT["cliente_codigo"], CERT["id"]
        # Factura cobrada antes del inicio: antecedente del certificado (no crea deuda ni factura).
        ficha(PIRQ)
        pg.click(f"#ini-body a:has-text('Ya facturado antes del inicio')")
        pg.wait_for_function("document.getElementById('m-ant').classList.contains('open')")
        pg.select_option("#a-cert", str(CERT_ID))
        pg.fill("#a-fecha", "2025-11-30")
        pg.select_option("#a-letra", "A")
        pg.fill("#a-pv", "6")
        pg.fill("#a-nro", "300")
        pg.fill("#a-nota", "Cobrada según Tango (prueba)")
        captura(pg, "00_antecedente")
        pg.click("#m-ant button:has-text('Registrar antecedente')")
        esperar_cierre(pg, "m-ant")
        cert = next(x for x in api(pg, f"/puesta-en-marcha/{PIRQ}")["certificados"] if x["id"] == CERT_ID)
        ok("factura cobrada: certificado sin pendiente, sin comprobantes ni deuda",
           cert["pendiente"] == 0 and cert["antecedentes"] == CERT["total_a_facturar"] and api(pg, f"/documentos?cliente={PIRQ}") == [],
           f"pendiente {cert['pendiente']} antecedentes {cert['antecedentes']}")
        # Factura pendiente con pago parcial previo en pesos (moneda y TC originales).
        pg.wait_for_function("document.getElementById('ini-pie').innerText.includes('Comprobante abierto')")
        pg.click("#ini-pie button:has-text('+ Comprobante abierto')")
        pg.wait_for_function("document.getElementById('m-doc').classList.contains('open') && DOC_MODO === 'SALDO_INICIAL'")
        pg.select_option("#d-mon", "USD")
        pg.fill("#d-pv", "6")
        pg.fill("#d-nro", "301")
        pg.fill("#d-fe", "2025-11-15")
        pg.fill("#d-vto", "2025-12-15")
        pg.fill("#d-lineas tr:nth-child(1) td:nth-child(3) input", "1000")
        pg.click("#d-si-pagos-w button:has-text('+ Pago previo')")
        pg.fill("#d-si-pagos tr:nth-child(1) td:nth-child(1) input", "2025-12-10")
        pg.select_option("#d-si-pagos tr:nth-child(1) td:nth-child(2) select", "ARS")
        pg.fill("#d-si-pagos tr:nth-child(1) td:nth-child(3) input", "500000")
        pg.fill("#d-si-pagos tr:nth-child(1) td:nth-child(4) input", "1000")
        pg.fill("#d-si-pagos tr:nth-child(1) td:nth-child(5) input", "1")
        pg.fill("#d-si-pagos tr:nth-child(1) td:nth-child(6) input", "77")
        ok("recibo informado sin total: aviso en pantalla", "falta su total" in texto(pg, "#d-si-res"), texto(pg, "#d-si-res"))
        pg.fill("#d-si-pagos tr:nth-child(1) td:nth-child(7) input", "400000")
        ok("pagos que superan el total del recibo: aviso en pantalla", "y el recibo es de" in texto(pg, "#d-si-res"), texto(pg, "#d-si-res"))
        pg.fill("#d-si-pagos tr:nth-child(1) td:nth-child(7) input", "600000")
        ok("pago previo: aplicado 500 USD y saldo al inicio 710", fmt(pg, 500, "USD") in texto(pg, "#pp-ap-0")
           and fmt(pg, 710, "USD") in texto(pg, "#d-si-res") and "recibo es de" not in texto(pg, "#d-si-res"), texto(pg, "#d-si-res"))
        captura(pg, "00_pago_previo")
        pg.click("#d-ok")
        esperar_cierre(pg, "m-doc")
        fi = next(d for d in api(pg, f"/documentos?cliente={PIRQ}") if d["numero"] == 301)
        det = api(pg, f"/documentos/{fi['id']}")
        pago = det["cancelaciones_corte"][0]
        ok("factura pendiente: total 1210, pagado antes 500, saldo 710; pago en ARS a TC 1000 con recibo 77",
           (det["total"], det["cancelado_antes_corte"], det["saldo"]) == (1210, 500, 710)
           and (pago["moneda_pago"], pago["importe_pago"], pago["tipo_cambio"], pago["recibo_numero"], pago["recibo_total"], pago["recibo_moneda"])
           == ("ARS", 500000, 1000, 77, 600000, "ARS"), str(pago))
        # El mismo recibo en otra factura: lo atribuido no supera su total en Tango (600.000 − 500.000 = 100.000 disponibles).
        otra = {"tipo": "FC", "letra": "A", "punto_venta": 6, "numero": 302, "cliente_codigo": PIRQ, "fecha_emision": "2025-11-20",
                "fecha_vencimiento": "2025-12-20", "moneda": "ARS", "origen": "SALDO_INICIAL", "corte_id": api(pg, "/corte")["corte"]["id"],
                "lineas": [{"descripcion": "x", "alicuota_iva": "21", "neto": "1000000"}],
                "pagos_previos": [{"fecha_pago": "2025-12-10", "moneda_pago": "ARS", "importe_pago": "100000.01", "recibo_punto_venta": 1,
                                   "recibo_numero": 77, "recibo_total": "600000", "recibo_moneda": "ARS"}]}
        r = pg.request.post(f"{URL}{API}/documentos", data=otra)
        ok("recibo compartido: superar su total se rechaza", r.status == 400 and r.json().get("codigo") == "RECIBO_EXCEDE", r.text())
        # Saldo según Tango y validación con antecedentes.
        pg.click("#ini-pie button:has-text('+ Saldo según Tango')")
        pg.wait_for_function("document.getElementById('m-conc').classList.contains('open')")
        pg.select_option("#k-mon", "USD")
        pg.fill("#k-saldo", "710")
        pg.click("#m-conc button:has-text('Guardar')")
        esperar_cierre(pg, "m-conc")
        pg.wait_for_function("document.getElementById('ini-body').innerText.includes('CONCILIADO')")
        # Con carga inicial sin validar, sus operaciones nuevas esperan la validación.
        ok("con carga sin validar: ficha marcada", "CARGA SIN VALIDAR" in texto(pg, "#ini-tit"))
        r = pg.request.post(f"{URL}{API}/documentos", data={"tipo": "FC", "letra": "A", "punto_venta": 6, "numero": 800002, "cliente_codigo": PIRQ,
                                                            "fecha_emision": "2026-02-01", "fecha_vencimiento": "2026-02-01", "moneda": "USD",
                                                            "lineas": [{"descripcion": "x", "alicuota_iva": "21", "neto": "1"}]})
        ok("con carga sin validar: comprobante nuevo rechazado", r.status == 400 and r.json().get("codigo") == "CLIENTE_SIN_VALIDAR", r.text())
        pg.click("#ini-pie button:has-text('Validar con antecedentes')")
        pg.wait_for_function("document.getElementById('ini-tit').innerText.includes('HABILITADO')")
        captura(pg, "00_ficha_validada")
        ok("con antecedentes: habilitado con saldo inicial USD 710", api(pg, f"/cuenta-corriente?cliente={PIRQ}&moneda=USD")["saldo"] == 710)
        ok("ficha validada: sin botones de carga inicial", pg.locator("#ini-pie button:has-text('+ Comprobante abierto')").count() == 0)
        # Duplicados: el recibo 77 ya es pago previo y la factura 301 ya está cargada.
        r = pg.request.post(f"{URL}{API}/recibos", data={"punto_venta": 1, "numero": 77, "cliente_codigo": PIRQ, "fecha_pago": "2026-01-10",
                                                         "moneda": "ARS", "valores": [{"medio": "TRANSFERENCIA", "importe": "1"}], "imputaciones": []})
        ok("duplicado: el recibo del pago previo no entra como cobranza nueva", r.status == 409 and r.json().get("codigo") == "DUPLICADO", r.text())
        pg.click("#m-ini .close")

    # Cierre de la puesta en marcha (todos los clientes con carga inicial están validados).
    pg.click("button:has-text('Cerrar puesta en marcha')")
    pg.wait_for_function("S.sesion.corte && S.sesion.corte.estado === 'CONFIRMADO'")
    ok("puesta en marcha cerrada desde la pantalla", api(pg, "/corte")["corte"]["estado"] == "CONFIRMADO")
    captura(pg, "00_puesta_en_marcha")

    # ---------- 2. Pedido: borrador, edición y emisión ----------
    ir(pg, "pedidos")
    pg.click("#v-pedidos button:has-text('+ Nuevo pedido')")
    pg.wait_for_selector("#ped-editor:not(.hidden)")
    pg.select_option("#pe-cli", CLI)
    pg.select_option("#pe-mon", "USD")
    pg.fill("#pe-cond", "30 FF")
    pg.fill("#pe-oc", "OC-E2E-77")
    pg.fill("#pe-lineas tr:nth-child(1) textarea", "Servicio de prueba E2E\nPeríodo octubre 2026")
    pg.fill("#pe-lineas tr:nth-child(1) td:nth-child(5) input", "900")
    ok("vista previa: total con IVA 21 %", fmt(pg, 1089, "USD") in texto(pg, "#pe-tot"), texto(pg, "#pe-tot").replace("\n", " "))
    ok("vista previa: formulario con cliente", RAZON in texto(pg, "#hoja"))
    captura(pg, "01_pedido_editor")
    pg.click("#ped-editor button:has-text('Guardar borrador')")
    pg.wait_for_selector("#ped-editor.hidden", state="attached")
    previos_ped = {f[0] for f in PREVIOS.get("fac_pedidos", [])} | {PED0}
    pedidos = [x for x in api(pg, "/pedidos") if x["id"] not in previos_ped]  # sin el pedido que ya traía la base de origen
    ok("pedido guardado como BORRADOR", len(pedidos) == 1 and pedidos[0]["estado"] == "BORRADOR", str([(x["id"], x["estado"]) for x in pedidos]))
    PED = pedidos[0]["id"]

    pg.click(f"#t-ped tr.click >> nth=0")
    pg.click("#m-ver button:has-text('Editar borrador')")
    pg.wait_for_selector("#ped-editor:not(.hidden)")
    ok("borrador reabierto con sus datos", pg.input_value("#pe-oc") == "OC-E2E-77" and pg.input_value("#pe-lineas tr:nth-child(1) td:nth-child(5) input") == "900")
    pg.fill("#pe-lineas tr:nth-child(1) td:nth-child(5) input", "1000")
    with pg.context.expect_page() as ventana_pdf:
        pg.click("#ped-editor button:has-text('Emitir y generar PDF')")
    ventana_pdf.value.close()
    pg.wait_for_selector("#ped-editor.hidden", state="attached")
    ped = api(pg, f"/pedidos/{PED}")
    ok("pedido emitido con número y huella", ped["estado"] == "EMITIDO" and bool(ped["numero_solicitud"]) and ped["subtotal"] == 1000,
       f"{ped['estado']} Nº {ped['numero_solicitud']} subtotal {ped['subtotal']}")
    pdf1 = pg.request.get(f"{URL}{API}/pedidos/{PED}/pdf")
    pdf2 = pg.request.get(f"{URL}{API}/pedidos/{PED}/pdf")
    with db() as conn:
        sha_guardado = conn.execute("SELECT pdf_sha256 FROM fac_pedidos WHERE id = ?", (PED,)).fetchone()[0]
    ok("PDF del pedido: mismo SHA-256 que el sellado al emitir", hashlib.sha256(pdf1.body()).hexdigest() == sha_guardado == hashlib.sha256(pdf2.body()).hexdigest())
    txt = PdfReader(io.BytesIO(pdf1.body())).pages[0].extract_text()
    open(os.path.join(CAPTURAS, "e2e_fac_pedido.pdf"), "wb").write(pdf1.body())
    esperados = ["SOLICITUD DE EMISION", "FACTURA", "D.C. 1-9-14", ped["numero_solicitud"], RAZON, "OC-E2E-77", "30 FF", "Servicio de prueba E2E", "1.000,00", "210,00", "1.210,00"]
    faltan = [e for e in esperados if e not in txt]
    ok("PDF del pedido: formulario D.C. 1-9-14 con los datos cargados", not faltan, f"faltan {faltan}" if faltan else "")

    # El TC a informar solo se imprime en pedidos en moneda extranjera.
    pg.click("#v-pedidos button:has-text('+ Nuevo pedido')")
    pg.wait_for_selector("#ped-editor:not(.hidden)")
    en_usd = pg.input_value("#pe-tc")
    pg.select_option("#pe-mon", "ARS")
    ok("pedido en ARS: sin fila de tipo de cambio", en_usd == "TC de la fecha efectiva de pago" and pg.input_value("#pe-tc") == "" and "TIPO DE CAMBIO" not in texto(pg, "#hoja"))
    pg.click("#ped-editor button:has-text('Cancelar')")
    pg.wait_for_selector("#ped-editor.hidden", state="attached")

    # ---------- 3. Factura desde el pedido, con percepción ----------
    pg.click(f"#t-ped tr.click >> nth=0")
    pg.click("#m-ver button:has-text('Registrar comprobante')")
    pg.wait_for_function("document.getElementById('m-doc').classList.contains('open') && document.querySelectorAll('#d-vin tr select').length > 0")
    pg.fill("#d-pv", "6")
    pg.fill("#d-nro", "900001")
    ok("factura: vencimiento sugerido por la condición (30 FF)", bool(pg.input_value("#d-vto")), pg.input_value("#d-vto"))
    pg.click("#m-doc button:has-text('+ Tributo')")
    pg.fill("#d-tribs tr:nth-child(1) td:nth-child(2) input", "Jujuy")
    pg.fill("#d-tribs tr:nth-child(1) td:nth-child(3) input", "1000")
    pg.fill("#d-tribs tr:nth-child(1) td:nth-child(4) input", "1")
    pg.fill("#d-pdf", "1220")
    ok("factura: total calculado coincide con el PDF de Tango", "Coincide" in texto(pg, "#d-pdf-msg"), texto(pg, "#d-tot").replace("\n", " "))
    captura(pg, "02_factura")
    pg.click("#d-ok")
    esperar_cierre(pg, "m-doc")
    docs = api(pg, "/documentos")
    FC = next(d["id"] for d in docs if d["numero"] == 900001)
    fc = api(pg, f"/documentos/{FC}")
    ok("factura registrada: neto 1000 + IVA 210 + percepción 10 = 1220", fc["total"] == 1220 and fc["saldo"] == 1220, f"total {fc['total']} saldo {fc['saldo']}")
    ped = api(pg, f"/pedidos/{PED}")
    ok("pedido queda FACTURADO", ped["estado_facturacion"] == "FACTURADO", ped["estado_facturacion"])

    # ---------- 4. Nota de crédito aplicada a la factura ----------
    ir(pg, "documentos")
    pg.click("#v-documentos button:has-text('+ Registrar comprobante')")
    pg.wait_for_function("document.getElementById('m-doc').classList.contains('open')")
    pg.click("#tg-tipo button[data-t='NC']")
    pg.select_option("#d-cli", CLI)
    pg.select_option("#d-mon", "USD")
    pg.wait_for_selector(f"#d-apl input.apl[data-id='{FC}']")
    pg.fill("#d-pv", "5")
    pg.fill("#d-nro", "900002")
    pg.fill("#d-lineas tr:nth-child(1) td:nth-child(1) input", "Ajuste de precio E2E")
    pg.fill("#d-lineas tr:nth-child(1) td:nth-child(3) input", "100")
    pg.select_option("#d-mot", "AJUSTE_PRECIO")
    pg.select_option("#d-origen", str(FC))
    pg.fill(f"#d-apl input.apl[data-id='{FC}']", "121")
    ok("NC: total 121 (100 + IVA 21)", fmt(pg, 121, "USD") in texto(pg, "#d-tot"))
    captura(pg, "03_nota_credito")
    pg.click("#d-ok")
    esperar_cierre(pg, "m-doc")
    NC = next(d["id"] for d in api(pg, "/documentos") if d["numero"] == 900002)
    ok("NC aplicada: saldo de la NC 0, factura 1099", api(pg, f"/documentos/{NC}")["saldo"] == 0 and api(pg, f"/documentos/{FC}")["saldo"] == 1099,
       f"NC {api(pg, f'/documentos/{NC}')['saldo']} FC {api(pg, f'/documentos/{FC}')['saldo']}")

    # ---------- 5. Cobranza parcial en pesos de la factura en USD (TC de la fecha de pago) ----------
    ir(pg, "cobranzas")
    pg.click("#v-cobranzas button:has-text('+ Registrar cobranza')")
    pg.wait_for_function("document.getElementById('m-cob').classList.contains('open')")
    pg.select_option("#c-cli", CLI)
    pg.wait_for_function(f"PEND_COB.some(d => d.id === {FC})")
    i = pg.evaluate(f"PEND_COB.findIndex(d => d.id === {FC})")
    pg.fill("#c-nro", "900003")
    pg.select_option("#c-mon", "ARS")
    pg.fill("#c-tc", "1000")
    pg.fill("#c-val tr:nth-child(1) td:nth-child(2) input", "500000")
    pg.fill(f"#c-imp input.imp[data-i='{i}']", "500000")
    ok("cobro ARS: equivalente 500 USD y saldo restante 599", fmt(pg, 500, "USD") in texto(pg, f"#ci-eq-{i}") and fmt(pg, 599, "USD") in texto(pg, f"#ci-sr-{i}"),
       f"{texto(pg, f'#ci-eq-{i}')} / {texto(pg, f'#ci-sr-{i}')}")
    captura(pg, "04_cobranza_ars")
    pg.click("#c-ok")
    esperar_cierre(pg, "m-cob")
    fc = api(pg, f"/documentos/{FC}")
    ok("factura con cobro parcial: saldo 599 USD", fc["saldo"] == 599, f"saldo {fc['saldo']} · {fc['estado_saldo']}")

    # ---------- 6. Cobranza en USD que cancela el resto y deja 1 USD a cuenta ----------
    pg.click("#v-cobranzas button:has-text('+ Registrar cobranza')")
    pg.wait_for_function("document.getElementById('m-cob').classList.contains('open')")
    pg.select_option("#c-cli", CLI)
    pg.wait_for_function(f"PEND_COB.some(d => d.id === {FC})")
    pg.fill("#c-nro", "900004")
    pg.select_option("#c-mon", "USD")
    pg.fill("#c-val tr:nth-child(1) td:nth-child(2) input", "600")
    pg.click("#m-cob button:has-text('Sugerir por vencimiento')")
    pg.wait_for_function("document.getElementById('c-val-res').innerText.includes('a cuenta')")
    pg.wait_for_timeout(300)
    ok("cobro USD: la sugerencia asigna 599 y deja 1 a cuenta", fmt(pg, 1, "USD") in texto(pg, "#c-val-res"), texto(pg, "#c-val-res"))
    pg.click("#c-ok")
    esperar_cierre(pg, "m-cob")
    fc = api(pg, f"/documentos/{FC}")
    rec = next(r for r in api(pg, "/recibos") if r["numero"] == 900004)
    ok("factura cancelada y 1 USD a cuenta del recibo", fc["saldo"] == 0 and rec["a_cuenta"] == 1, f"saldo {fc['saldo']} {fc['estado_saldo']} · a cuenta {rec['a_cuenta']}")

    # ---------- 7. Cuenta corriente por moneda ----------
    ir(pg, "ctacte")
    pg.select_option("#cc-cli", CLI)
    pg.click("#tg-mon button[data-m='USD']")
    pg.wait_for_function("document.querySelector('#ctacte tfoot') && document.querySelector('#ctacte tfoot').innerText.includes('USD')")
    cc_usd = api(pg, f"/cuenta-corriente?cliente={CLI}&moneda=USD")
    ok("cta. cte. USD: 1220 − 121 − 500 − 600 = −1 (API y saldo calculado)", cc_usd["saldo"] == -1 and cc_usd["saldo_calculado"] == -1, f"{cc_usd['saldo']} / {cc_usd['saldo_calculado']}")
    ok("cta. cte. USD en pantalla", fmt(pg, -1, "USD") in texto(pg, "#ctacte tfoot"), texto(pg, "#ctacte tfoot"))
    captura(pg, "05_ctacte_usd")
    pg.click("#tg-mon button[data-m='ARS']")
    pg.wait_for_timeout(500)
    cc_ars = api(pg, f"/cuenta-corriente?cliente={CLI}&moneda=ARS")
    ok("cta. cte. ARS: el recibo en pesos aplicado a la factura en USD no queda como saldo en pesos", cc_ars["saldo"] == 0 and cc_ars["saldo_calculado"] == 0,
       f"{cc_ars['saldo']} / {cc_ars['saldo_calculado']}")

    # ---------- 8. Base: registros y auditoría ----------
    with db() as conn:
        n = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] - len(PREVIOS.get(t, [])) for t in ("fac_pedidos", "fac_documentos", "fac_recibos", "fac_aplicaciones_nc", "fac_imputaciones")}
        n["fac_documentos"] = conn.execute("SELECT COUNT(*) FROM fac_documentos WHERE origen = 'NORMAL'").fetchone()[0]
        iniciales = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("fac_estado_inicial", "fac_antecedentes_certificado")}
        iniciales["documentos_saldo_inicial"] = conn.execute("SELECT COUNT(*) FROM fac_documentos WHERE origen = 'SALDO_INICIAL'").fetchone()[0]
        eventos = conn.execute("SELECT entidad, evento, usuario FROM fac_eventos WHERE id > ? ORDER BY id", (ULTIMO_EVENTO,)).fetchall()
        intactos = {t: [tuple(f) for f in conn.execute(f"SELECT * FROM {t} WHERE rowid <= ? ORDER BY rowid", (filas[-1][0],)).fetchall()]
                    for t, filas in PREVIOS.items() if filas and t != "fac_cortes"}
        corte_final = conn.execute("SELECT id, creado_por, creado_en FROM fac_cortes").fetchall()
    ok("base: 2 pedidos, 4 comprobantes nuevos, 3 recibos, 1 aplicación, 3 imputaciones",
       n == {"fac_pedidos": 2, "fac_documentos": 4, "fac_recibos": 3, "fac_aplicaciones_nc": 1, "fac_imputaciones": 3}, str(n))
    ok("base: carga inicial = 2 clientes validados, 1 antecedente, 1 factura de saldo inicial (sin recibos por el pago previo)",
       iniciales == ({"fac_estado_inicial": 2, "fac_antecedentes_certificado": 1, "documentos_saldo_inicial": 1} if CERT else
                     {"fac_estado_inicial": 1, "fac_antecedentes_certificado": 0, "documentos_saldo_inicial": 0}), str(iniciales))
    ok("auditoría: cada operación quedó registrada con el usuario", len(eventos) >= 8 and all(e["usuario"] == "ui.resp@test.local" for e in eventos),
       f"{len(eventos)} eventos: {sorted({e['evento'] for e in eventos})}")
    ok("base: el pedido y los eventos que ya tenía la base de origen siguen intactos; el corte es el mismo",
       intactos == {t: f for t, f in PREVIOS.items() if f and t != "fac_cortes"}
       and (not PREVIOS.get("fac_cortes") or [tuple(x) for x in corte_final] == [(c[0], c[4], c[5]) for c in PREVIOS["fac_cortes"]]),
       f"previos: { {t: len(f) for t, f in PREVIOS.items()} }")

    # ---------- 9. Perfil de consulta: ve los datos, sin botones de carga ----------
    cons = ingresar(navegador, "ui.consulta@test.local")
    ir(cons, "documentos")
    cons.wait_for_function("document.querySelectorAll('#t-docs tr.click').length > 0")
    ok("consulta: ve los comprobantes", cons.locator("#t-docs tr.click").count() == (5 if CERT else 4), str(cons.locator("#t-docs tr.click").count()))
    ir(cons, "saldos")
    cons.wait_for_selector("#pm-clientes tr.click")
    ok("consulta: ve la puesta en marcha sin botones de validar",
       cons.locator("#pm-clientes").inner_text().count("HABILITADO") >= 1 and cons.locator("button:has-text('Validar')").count() == 0)
    ok("consulta: sin botón de registrar comprobante", not cons.locator("#v-documentos button:has-text('+ Registrar comprobante')").is_visible())
    navegador.close()

servidor.should_exit = True
ok("la base de origen no cambió", hashlib.sha256(open(DB_ORIGEN, "rb").read()).hexdigest() == SHA_ORIGEN)
for linea in RESULTADOS:
    print(linea)
print("Base temporal:", DB)
print("Capturas:", CAPTURAS)
print("EXCEPCIONES JS:", json.dumps(ERRORES, ensure_ascii=False))
print("Respuestas con error:", json.dumps(RESPUESTAS, ensure_ascii=False))
sys.exit(0 if all(r.startswith("OK") for r in RESULTADOS) and not ERRORES and not RESPUESTAS else 1)
