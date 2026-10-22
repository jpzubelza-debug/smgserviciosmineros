"""Prueba de punta a punta de la pantalla de Certificados (navegador real, base copiada).

Uso: python tests/e2e_certificados.py <carpeta_capturas>
Levanta la aplicación sobre una COPIA de dashboard.db (solo lectura sobre la original), crea usuarios y un
seguimiento de prueba, y recorre el flujo con Microsoft Edge (Playwright) verificando resultados y errores de JS.
"""
import hashlib
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
CAPTURAS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, "capturas"))
os.makedirs(CAPTURAS, exist_ok=True)
DB_REAL = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
TMP = tempfile.mkdtemp(prefix="smg_e2e_cert_")
DB = os.path.join(TMP, "dashboard.db")
origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
destino = sqlite3.connect(DB)
origen.backup(destino)
destino.close()
origen.close()
os.environ.update({"DATA_DIR": TMP, "APP_LOGIN_USER": "contingencia_e2e", "APP_LOGIN_PASSWORD": "clave-contingencia-e2e"})

import main  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB)
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PUERTO = sock.getsockname()[1]
sock.close()
servidor = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PUERTO, log_level="warning", lifespan="off"))
threading.Thread(target=servidor.run, daemon=True).start()
while not servidor.started:
    time.sleep(0.05)
URL = f"http://127.0.0.1:{PUERTO}"
CLAVE = "Clave-Prueba-1"
TODAS = ["consultar", "certificar", "emitir_certificado", "anular_certificado", "cambiar_modalidad", "autorizar_exceso",
         "configurar_reajuste", "cargar_indices", "incorporar_historico"]


def db():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


with db() as conn:
    for correo, acciones in (("ui.admin@test.local", TODAS), ("ui.sinpanel@test.local", None)):
        conn.execute("DELETE FROM usuarios WHERE correo = ?", (correo,))
        conn.execute(
            "INSERT INTO usuarios (nombre_apellido, correo, password_hash, estado, tipo_usuario, modulos_json, paneles_json, acciones_json, "
            "bloqueado, intentos_fallidos) VALUES (?, ?, ?, 'ACTIVO', 'OPERADOR', ?, ?, ?, 0, 0)",
            (correo.split("@")[0], correo, hashlib.sha256(CLAVE.encode()).hexdigest(), json.dumps(["operaciones"]),
             json.dumps({"operaciones": ["dashboard", "seguimiento_cliente"]} if acciones else {"operaciones": ["dashboard"]}),
             json.dumps({"operaciones": {"seguimiento_cliente": acciones}} if acciones else {})),
        )
    cliente = conn.execute("SELECT codigo FROM clientes ORDER BY codigo LIMIT 1").fetchone()
    SID = conn.execute(
        "INSERT INTO seguimiento_cc (centro_costo_id, estado_seguimiento, cotizacion, cliente_codigo, cantidad_certificados, fecha_ultima_actualizacion) "
        "VALUES (1, 'En curso', 'COT-E2E-001', ?, 6, CURRENT_TIMESTAMP)", (cliente["codigo"] if cliente else None,)).lastrowid
    oc1 = conn.execute("INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, revision, moneda, monto_oc) "
                       "VALUES (?, 1, 'OC-4501', 'Rev. 1', 'ARS', 500000)", (SID,)).lastrowid
    for n, (desc, unidad, cantidad, precio) in enumerate([("Movimientos de suelo & excavación", "m3", 1000, 50), ("Hormigón armado", "m3", 800, 150),
                                                           ("Estructura metálica", "tn", 50, 2000), ("Instalaciones eléctricas", "gl", 1, 80000),
                                                           ("Terminaciones", "m2", 2000, 75)], start=1):
        conn.execute("INSERT INTO items_oc_cliente (orden_compra_cliente_id, numero, descripcion, unidad, cantidad, moneda, precio, total) "
                     "VALUES (?, ?, ?, ?, ?, 'ARS', ?, ?)", (oc1, n, desc, unidad, cantidad, precio, cantidad * precio))
    oc2 = conn.execute("INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, revision, moneda, monto_oc) "
                       "VALUES (?, 2, 'OC-4502', 'Rev. 0', 'ARS', 60000)", (SID,)).lastrowid
    conn.execute("INSERT INTO items_oc_cliente (orden_compra_cliente_id, numero, descripcion, unidad, cantidad, moneda, precio, total) "
                 "VALUES (?, 1, 'Servicio de grúa', 'gl', 1, 'ARS', 60000, 60000)", (oc2,))

RESULTADOS, ERRORES, RESPUESTAS = [], [], []


def ok(nombre, condicion, detalle=""):
    RESULTADOS.append(f"{'OK   ' if condicion else 'FALLA'} {nombre}{f' · {detalle}' if detalle else ''}")


def captura(pagina, nombre, completa=True):
    pagina.screenshot(path=os.path.join(CAPTURAS, f"e2e_{nombre}.png"), full_page=completa)


def ingresar(navegador, usuario, clave=CLAVE):
    contexto = navegador.new_context(viewport={"width": 1500, "height": 1000}, locale="es-AR")
    pagina = contexto.new_page()
    pagina.on("pageerror", lambda e: ERRORES.append(f"{usuario}: {e}"))
    # Respuestas con error: los 400 de /calcular son validaciones esperables mientras se carga; el resto se informa.
    pagina.on("response", lambda r: RESPUESTAS.append(f"{r.status} {r.request.method} {r.url.replace(URL, '')}") if r.status >= 400 else None)
    # Mismo POST que el formulario de acceso de login.html; la cookie de sesión queda en este contexto.
    respuesta = contexto.request.post(f"{URL}/login", form={"username": usuario, "password": clave}, max_redirects=0)
    assert respuesta.headers.get("location") == "/", f"Login fallido para {usuario}"
    pagina.goto(f"{URL}/operaciones")
    pagina.click("#btn-sidebar-seguimiento-cliente")
    pagina.wait_for_function(f"[...document.querySelectorAll('#seg-registro option')].some(o => o.value === '{SID}')")
    pagina.select_option("#seg-registro", str(SID))
    pagina.wait_for_function("segSeguimientoId() !== null && !segState.ocupado")
    return pagina


def esperar_calculo(pagina):
    pagina.wait_for_timeout(700)
    pagina.wait_for_function("certState.ed && (certState.ed.calculo || certState.ed.error)")


def cargar_indice(pagina, codigo, periodo, valor, motivo=""):
    pagina.click(f"button[data-ix-c='{codigo}'][data-ix-p='{periodo}']")
    pagina.fill("#cert-ix-val", str(valor))
    if motivo:
        pagina.fill("#cert-ix-mot", motivo)
    pagina.click("#cert-ix-cuerpo .cert-btn.primary")
    pagina.wait_for_function(f"certState.rj.indices.some(i => i.codigo === '{codigo}' && i.periodo === '{periodo}' && Number(i.valor) === {valor})")


with sync_playwright() as p:
    navegador = p.chromium.launch(channel="msedge", headless=True)
    pagina = ingresar(navegador, "ui.admin@test.local")
    ok("tema claro en Seguimiento", pagina.evaluate("document.body.classList.contains('tema-claro')"))
    ok("barra lateral sin entradas nuevas", pagina.evaluate("[...document.querySelectorAll('.sidebar .menu-label')].map(e => e.textContent.trim()).join('|')")
       == "Dashboard|Carga de Partes Diarios|Centro de Costos|Seguimiento de Cliente|Menú Principal")
    boton = pagina.locator("#btn-certificados-cliente")
    ok("botón Certificados visible y habilitado", boton.is_visible() and boton.is_enabled())
    captura(pagina, "01_seguimiento", completa=False)

    boton.click()
    pagina.wait_for_function("certState.rj && !certState.ocupado")
    ok("pantalla de Certificados abierta", pagina.locator("#panel-certificados-cliente").is_visible() and not pagina.locator("#panel-seguimiento-cliente").is_visible())
    ok("Seguimiento sigue activo en la barra", "Seguimiento" in pagina.evaluate("document.querySelector('.menu-btn.active').textContent"))
    captura(pagina, "02_certificados_vacio")

    # Fórmula: v1 queda pendiente de validación contractual (se valida antes de emitir, más abajo).
    pagina.click("text=Fórmula polinómica")
    pagina.click("button[data-fb-agregar]")
    pagina.click("button[data-fb-agregar]")
    pagina.fill("input[data-fb='vigente_desde']", "2026-09")
    pagina.fill("input[data-fb='mes_base']", "2026-08")
    for i, (codigo, nombre, w, base) in enumerate([("MO", "Mano de obra", "0.40", "1000"), ("GASOIL", "Gasoil", "0.35", "500"), ("ACERO", "Acero", "0.25", "2000")]):
        pagina.fill(f"input[data-fb-comp='{i}'][data-campo='codigo']", codigo)
        pagina.fill(f"input[data-fb-comp='{i}'][data-campo='nombre']", nombre)
        pagina.fill(f"input[data-fb-comp='{i}'][data-campo='ponderacion']", w)
        pagina.fill(f"input[data-fb-comp='{i}'][data-campo='valor_base']", base)
    ok("suma de ponderaciones correcta", pagina.locator("#cert-fb-suma").inner_text().startswith("1,0000"))
    pagina.fill("textarea[data-fb='motivo']", "Versión inicial cargada desde el contrato")
    pagina.click("text=Guardar como versión 1")
    pagina.wait_for_function("certState.rj.formulas.length === 1")
    pagina.wait_for_selector("#cert-m-formula.open")
    pagina.click("[data-cert-cerrar='cert-m-formula']")

    # Índices de septiembre y octubre (MO de octubre provisorio).
    pagina.click("text=Índices mensuales")
    for periodo, valores in (("2026-09", (1030, 510, 2020)), ("2026-10", (1050, 520, 2050))):
        for codigo, valor in zip(("MO", "GASOIL", "ACERO"), valores):
            cargar_indice(pagina, codigo, periodo, valor)
    captura(pagina, "04_indices", completa=False)
    pagina.click("[data-cert-cerrar='cert-m-indices']")

    # Certificado de septiembre: por ítems, con descuento porcentual.
    pagina.click("#btn-cert-nuevo")
    pagina.wait_for_selector("#cert-m-editor.open")
    pagina.fill("#cert-ed-desde", "2026-09-01")
    pagina.fill("#cert-ed-hasta", "2026-09-30")
    pagina.dispatch_event("#cert-ed-hasta", "change")
    pagina.fill("#cert-ed-fecha", "2026-09-30")
    pagina.dispatch_event("#cert-ed-fecha", "change")
    items = pagina.locator("input[data-cert-carga]")
    for i, valor in enumerate(("200", "80", "5", "0.1", "200")):
        items.nth(i).fill(valor)
    pagina.click("#btn-cert-ed-desc")
    pagina.select_option("select[data-cert-desc='0'][data-campo='concepto']", "FONDO_REPARO")
    pagina.select_option("select[data-cert-desc='0'][data-campo='base']", "EJECUCION_REAJUSTADA")
    pagina.fill("input[data-cert-desc='0'][data-campo='porcentaje']", "5")
    esperar_calculo(pagina)
    pagina.wait_for_function("certState.ed.calculo && certState.ed.calculo.descuentos.length === 1")
    calc = pagina.evaluate("certState.ed.calculo.cabecera")
    # C(sep) = 1,0215; neto 55.000 → reajuste 1.182,50; descuento 5 % de 56.182,50 = 2.809,13.
    ok("cálculo del servidor en el editor", (calc["importe_periodo"], calc["reajuste_periodo"], calc["descuentos_netos"], calc["total_a_facturar"])
       == (55000, 1182.5, 2809.13, 53373.37), str(calc))
    ok("foco conservado al recalcular", pagina.evaluate("document.activeElement && document.activeElement.dataset.campo === 'porcentaje'"))
    captura(pagina, "05_editor")
    # Con la fórmula pendiente: el borrador se guarda y se calcula, pero no puede emitirse.
    pagina.click("#btn-cert-ed-emitir")
    pagina.wait_for_selector("#cert-m-emitir.open")
    ok("fórmula pendiente bloquea la emisión", pagina.locator("#btn-cert-em-ok").is_disabled()
       and "pendiente de validación contractual" in pagina.locator("#cert-em-cuerpo").inner_text())
    guardado = pagina.request.get(f"{URL}/operaciones/seguimiento-cliente/{SID}/certificados/{pagina.evaluate('certState.ed.id')}")
    ok("borrador guardado con la fórmula pendiente", guardado.status == 200 and guardado.json()["estado"] == "BORRADOR")
    captura(pagina, "06_emitir_bloqueado", completa=False)
    pagina.click("#cert-m-emitir .cert-modal-f [data-cert-cerrar='cert-m-emitir']")
    pagina.click("#cert-m-editor [data-cert-cerrar='cert-m-editor'] >> nth=0")
    pagina.click("text=Fórmula polinómica")
    pagina.wait_for_selector("#cert-m-formula.open")
    pagina.click("#cert-fo-cuerpo button:has-text('Validar')")
    ok("validar exige confirmación y referencia", pagina.locator("#btn-cert-va-ok").is_disabled())
    pagina.check("#cert-va-ok")
    pagina.fill("#cert-va-ref", "Contrato 66-1764, cláusula 8")
    pagina.click("#btn-cert-va-ok")
    pagina.wait_for_function("certState.rj.formulas[0].validacion_contractual === 'VALIDADA'")
    captura(pagina, "03_formula_validada", completa=False)
    pagina.click("[data-cert-cerrar='cert-m-formula']")
    pagina.click("#cert-lista button:has-text('Emitir')")
    pagina.wait_for_selector("#cert-m-emitir.open")
    ok("validada: emisión habilitada", pagina.locator("#btn-cert-em-ok").is_enabled())
    captura(pagina, "06_emitir", completa=False)
    pagina.click("#btn-cert-em-ok")
    pagina.wait_for_function("certState.lista.some(c => c.estado === 'EMITIDO')")
    ok("certificado emitido", pagina.evaluate("certState.lista[0].estado") == "EMITIDO")

    # Corrección del índice MO de septiembre: retroactivo pendiente de 55.000 × 0,40 × 50/1000 = 1.100.
    pagina.click("text=Índices mensuales")
    cargar_indice(pagina, "MO", "2026-09", 1080, "Paritaria septiembre (acuerdo CCT)")
    pagina.click("[data-cert-cerrar='cert-m-indices']")
    ok("retroactivo pendiente informado", pagina.evaluate("certState.rj.pendientes.reduce((s, l) => s + Number(l.importe), 0)") == 1100)
    captura(pagina, "07_pendiente", completa=False)

    # Certificado de octubre: toma el retroactivo una sola vez.
    pagina.click("#btn-cert-nuevo")
    pagina.wait_for_selector("#cert-m-editor.open")
    pagina.fill("#cert-ed-desde", "2026-10-01")
    pagina.fill("#cert-ed-hasta", "2026-10-31")
    pagina.dispatch_event("#cert-ed-hasta", "change")
    pagina.locator("input[data-cert-carga]").nth(1).fill("100")
    esperar_calculo(pagina)
    pagina.wait_for_function("certState.ed.calculo && certState.ed.calculo.cabecera.importe_periodo === 15000")
    rj = pagina.evaluate("certState.ed.calculo.reajuste")
    ok("retroactivo en el editor", rj["retroactivo"] == 1100 and any(l["tipo"] == "RETROACTIVO" for l in rj["lineas"]), json.dumps(rj["retroactivo"]))
    captura(pagina, "08_editor_retroactivo")
    pagina.click("#btn-cert-ed-guardar")
    pagina.wait_for_function("certState.lista.some(c => c.estado === 'BORRADOR')")
    borrador = pagina.evaluate("certState.lista.find(c => c.estado === 'BORRADOR').id")
    pagina.click(f"#cert-lista button:has-text('Emitir')")
    pagina.wait_for_selector("#cert-m-emitir.open")
    pagina.click("#btn-cert-em-ok")
    pagina.wait_for_function(f"certState.lista.find(c => c.id === {borrador}).estado === 'EMITIDO'")
    ok("pendiente saldado", pagina.evaluate("certState.rj.pendientes.length") == 0)
    captura(pagina, "09_lista")

    # Detalle, contrato y PDF.
    pagina.click("#cert-lista tr:first-child button:has-text('Ver')")
    pagina.wait_for_selector("#cert-m-ver.open")
    ok("detalle con reajustes congelados", "congelados al emitir" in pagina.locator("#cert-ver-cuerpo").inner_text())
    captura(pagina, "10_detalle", completa=False)
    pagina.click("[data-cert-cerrar='cert-m-ver']")
    pagina.click("text=Configuración del contrato")
    pagina.fill("#cert-co-prev", "15000")
    pagina.fill("#cert-co-prev-mot", "Previsión del contrato")
    pagina.click("#cert-co-cuerpo button:has-text('Registrar') >> nth=0")
    pagina.wait_for_function("certState.rj.previsiones.length === 1")
    captura(pagina, "11_contrato", completa=False)
    pagina.click("[data-cert-cerrar='cert-m-contrato']")
    respuesta = pagina.request.get(f"{URL}/operaciones/seguimiento-cliente/{SID}/certificados/{borrador}/pdf")
    ok("PDF del certificado", respuesta.status == 200 and respuesta.body()[:4] == b"%PDF")
    with open(os.path.join(CAPTURAS, "e2e_certificado.pdf"), "wb") as archivo:
        archivo.write(respuesta.body())
    pagina.click("#btn-cert-volver")
    ok("volver al seguimiento", pagina.locator("#panel-seguimiento-cliente").is_visible())
    pagina.click("#btn-sidebar-carga-partes")
    ok("otros paneles conservan el tema actual", not pagina.evaluate("document.body.classList.contains('tema-claro')"))
    captura(pagina, "12_partes_tema_actual", completa=False)

    # Cuenta de contingencia: ve certificados pero no ejecuta acciones nominales.
    contingencia = ingresar(navegador, "contingencia_e2e", "clave-contingencia-e2e")
    contingencia.click("#btn-certificados-cliente")
    contingencia.wait_for_function("certState.rj && !certState.ocupado")
    anular = contingencia.locator("#cert-lista button:has-text('Anular')").first
    ok("contingencia no puede anular", anular.is_disabled() and "usuario nominal" in (anular.get_attribute("title") or ""))

    sinpanel = ingresar(navegador, "ui.sinpanel@test.local")
    sinpanel.wait_for_timeout(500)
    ok("sin panel: botón oculto", not sinpanel.locator("#btn-certificados-cliente").is_visible())
    navegador.close()

servidor.should_exit = True
for linea in RESULTADOS:
    print(linea)
print("EXCEPCIONES JS:", json.dumps(ERRORES, ensure_ascii=False))
inesperadas = [r for r in RESPUESTAS if not (r.startswith("400 POST") and r.endswith("/certificados/calcular"))]
print(f"Respuestas 400 de /calcular (validación mientras se carga): {len(RESPUESTAS) - len(inesperadas)}")
print("Otras respuestas con error:", json.dumps(inesperadas, ensure_ascii=False))
sys.exit(0 if all(r.startswith("OK") for r in RESULTADOS) and not ERRORES and not inesperadas else 1)
