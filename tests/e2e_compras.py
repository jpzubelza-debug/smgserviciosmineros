"""Prueba de punta a punta de Compras (Edge real vía Playwright, base copiada).

Uso: .venv-1\\Scripts\\python.exe tests/e2e_compras.py <carpeta_capturas>
Copia dashboard.db a una carpeta temporal, levanta el servidor sobre la copia y recorre todos los flujos:
emitir solicitud, aprobar / rechazar / gestionar (en el detalle y masivo), emitir / editar / anular OC con vínculo
a ítems de solicitud, PDF, Excel, ABM de proveedores con validación de CUIT, panel, buscador, permisos y responsive.
Informa errores de JS. SMG_MAIN_DIR permite probar una copia de main.py (con SMG_BASE_DIR apuntando a las pantallas).
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

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
CAPTURAS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "capturas"))
os.makedirs(CAPTURAS, exist_ok=True)

DB_REAL = os.getenv("SMG_TEST_DB_ORIGEN") or os.path.join(RAIZ, "dashboard.db")
MTIME_REAL = os.path.getmtime(DB_REAL)
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_compras_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")
_o = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
_d = sqlite3.connect(DB_COPIA)
_o.backup(_d)
_d.close()
_o.close()
os.environ["DATA_DIR"] = TMP_DIR
os.environ["APP_LOGIN_USER"] = "contingencia_test"
os.environ["APP_LOGIN_PASSWORD"] = "clave-contingencia-test"
os.environ.setdefault("SMG_BASE_DIR", RAIZ)
sys.path.insert(0, os.getenv("SMG_MAIN_DIR") or RAIZ)

import main  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB_COPIA), "La prueba no apunta a la copia"
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
PUERTO = _s.getsockname()[1]
_s.close()
_SRV = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PUERTO, log_level="warning", lifespan="off"))
threading.Thread(target=_SRV.run, daemon=True).start()
for _ in range(100):
    if _SRV.started:
        break
    time.sleep(0.05)
URL = f"http://127.0.0.1:{PUERTO}"

# Usuario con permisos acotados: solo pide y consulta sus solicitudes
SOLICITANTE = ("solicitante.compras@test.local", "clave-solicitante-1")
with sqlite3.connect(DB_COPIA) as conn:
    conn.execute(
        """INSERT INTO usuarios (nombre_apellido, dni, legajo, correo, password_hash, estado, tipo_usuario, modulos_json,
        paneles_json, acciones_json, bloqueado, intentos_fallidos, password_temporal) VALUES
        ('Solicitante Compras', '99999997', '', ?, ?, 'ACTIVO', 'USUARIO', '["compras"]', ?, ?, 0, 0, 0)""",
        (SOLICITANTE[0], hashlib.sha256(SOLICITANTE[1].encode()).hexdigest(),
         json.dumps({"compras": ["solicitud_compra", "solicitudes_emitidas"]}),
         json.dumps({"compras": {"solicitud_compra": ["crear_solicitud"], "solicitudes_emitidas": ["consultar"]}})))

RESULTADOS, ERRORES = [], []


def ok(nombre, condicion, detalle=""):
    RESULTADOS.append(f"{'OK   ' if condicion else 'FALLA'} {nombre}{f' · {detalle}' if detalle else ''}")


def vigilar(pagina, quien):
    pagina.on("pageerror", lambda e: ERRORES.append(f"{quien}: {e}"))
    pagina.on("console", lambda m: ERRORES.append(f"{quien} consola: {m.text}") if m.type == "error" and " 40" not in m.text else None)


def captura(pagina, nombre, completa=False):
    pagina.screenshot(path=os.path.join(CAPTURAS, f"co_{nombre}.png"), full_page=completa)


def cuit_valido(base10):
    suma = sum(int(d) * m for d, m in zip(base10, (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)))
    v = 11 - suma % 11
    v = 0 if v == 11 else 9 if v == 10 else v
    return f"{base10[:2]}-{base10[2:]}-{v}"


def sesion(navegador, usuario, clave, ancho=1440, alto=900):
    ctx = navegador.new_context(viewport={"width": ancho, "height": alto}, locale="es-AR", accept_downloads=True)
    r = ctx.request.post(f"{URL}/login", form={"username": usuario, "password": clave})
    assert r.ok, r.text()
    p = ctx.new_page()
    vigilar(p, usuario)
    return p


def elegir(p, selector, texto, opcion=None):
    """Escribe en un combobox y elige la opción que contiene `opcion` (o la primera)."""
    p.click(selector)
    p.fill(selector, texto)
    p.wait_for_timeout(150)
    lista = p.locator(".combo-list:not([hidden]) .combo-opt[data-i]")
    (lista.filter(has_text=opcion).first if opcion else lista.first).click()


def ir(p, hash_):
    p.evaluate(f"location.hash = {json.dumps(hash_)}")
    p.wait_for_timeout(700)


def toast(p):
    p.wait_for_selector(".toast", timeout=8000)
    t = p.locator(".toast").last.text_content()
    p.evaluate("document.querySelectorAll('.toast').forEach((t) => t.remove())")
    return t


with sync_playwright() as pw:
    nav = pw.chromium.launch(channel="msedge", headless=True)
    p = sesion(nav, "contingencia_test", "clave-contingencia-test")

    # ---------------------------------------------------------------- shell
    p.goto(f"{URL}/compras")
    p.wait_for_timeout(1200)
    ok("shell: carga app.css y shell.js", p.evaluate("!!document.querySelector('link[href=\"/ui/app.css\"]') && !!window.SMG"))
    ok("shell: sin hojas de estilo propias", p.evaluate("document.querySelectorAll('style').length === 0"))
    ok("shell: entra al panel por defecto", p.evaluate("location.hash") == "#panel")
    ok("shell: breadcrumb Compras / Panel", p.text_content("#crumbs").replace("\n", "") == "ComprasPanel", p.text_content("#crumbs"))
    ok("shell: sidebar con los 6 ítems de Compras", p.locator("#sidebar [data-panel]:not([hidden])").count() == 6)
    ok("shell: ítem activo según la URL", p.get_attribute('#sidebar [data-panel="dashboard"]', "aria-current") == "page")
    fuentes = p.evaluate("getComputedStyle(document.body).fontFamily")
    ok("shell: tipografía Inter", "Inter" in fuentes, fuentes)
    ok("shell: sin imagen de fondo", p.evaluate("getComputedStyle(document.body).backgroundImage") == "none")
    mayus = p.evaluate("[...document.querySelectorAll('label, .field, .kpi-label, th, .sb-section, legend, .card-title')].filter((e) => getComputedStyle(e).textTransform === 'uppercase').length")
    ok("shell: ningún label en mayúsculas", mayus == 0, str(mayus))
    sin_tbl = p.evaluate("[...document.querySelectorAll('table')].filter((t) => !t.classList.contains('tbl')).length")
    ok("shell: todas las tablas con .tbl", sin_tbl == 0, str(sin_tbl))
    p.click("#sb-modulo")
    p.click('#menu-modulos [data-modulo="logistica"]')
    ok("shell: el selector de módulo muestra los ítems de Logística", p.locator('#sidebar [data-panel="vehiculos"]').count() == 1)
    p.click("#sb-modulo")
    p.click('#menu-modulos [data-modulo="compras"]')
    captura(p, "01_panel")

    # ---------------------------------------------------------------- panel
    ok("panel: 8 KPIs", p.locator("#pd-kpis .kpi").count() == 8)
    ok("panel: montos en formato es-AR", "$" in p.text_content("#pd-kpis") and "," in p.text_content("#pd-kpis"))
    p.click('#pd-items-vista [data-vista="departamento"]')
    p.wait_for_timeout(800)
    ok("panel: vista por departamento", p.is_visible("#pd-depto-tabla") and p.locator("#pd-depto-tabla tbody tr").count() > 0)
    captura(p, "02_panel_departamento", True)
    p.click('#pd-items-vista [data-vista="estado"]')
    p.locator("#pd-items-estados [data-items-estado]").first.click()
    p.wait_for_selector("#d-items:not([hidden])")
    p.wait_for_timeout(500)
    ok("panel: detalle de ítems en drawer", p.locator("#di-tabla tbody tr").count() > 0)
    p.keyboard.press("Escape")
    with p.expect_download() as dl:
        p.click("#pd-exportar")
        p.click('#menu-pd-exportar [data-exp="excel"]')
    ok("panel: exporta Excel", dl.value.suggested_filename.endswith(".xlsx"), dl.value.suggested_filename)
    p.locator("#pd-plazos tbody tr").last.click()
    p.wait_for_timeout(800)
    ok("panel: fila de plazo abre Solicitudes filtrado", "plazo=fuera" in p.evaluate("location.hash") and p.evaluate("document.querySelector('#sl-f-plazo').value") == "fuera", p.evaluate("location.hash"))

    # ---------------------------------------------------------------- nueva solicitud
    ir(p, "#nueva-solicitud")
    ok("solicitud: Emitir deshabilitado sin ítems", p.is_disabled("#ns-emitir"))
    numero = p.text_content("#ns-numero").strip()
    ok("solicitud: N.º SC en solo lectura", numero.startswith("SC-"), numero)
    ok("solicitud: fecha dd/mm/aaaa", p.text_content("#ns-fecha").count("/") == 2, p.text_content("#ns-fecha"))
    elegir(p, "#ns-solicitante", "zubelza")
    elegir(p, "#ns-destino", "")
    for art, cant in (("Filtro de aceite e2e", "2"), ("Correa de distribución e2e", "1,5")):
        p.fill("#ns-articulo", art)
        p.keyboard.press("Escape")
        p.fill("#ns-motivo", "Prueba e2e")
        p.fill("#ns-cantidad", cant)
        p.fill("#ns-unidad", "unidad")
        p.keyboard.press("Escape")
        elegir(p, "#ns-cc", "")
        p.click("#ns-agregar")
    ok("solicitud: 2 ítems cargados", p.locator("#ns-items tr[data-i]").count() == 2)
    p.locator("#ns-items [data-menu-fila]").first.click()
    p.click('#menu-ns-fila [data-act="editar"]')
    p.fill("#ns-cantidad", "3")
    p.click("#ns-agregar")
    ok("solicitud: editar ítem desde ⋯", "3" in p.locator("#ns-items tr[data-i]").first.text_content())
    p.locator("#ns-prioridad [data-prioridad]").last.click()
    ok("solicitud: resumen con ítems y prioridad", "2 de 20" in p.text_content("#ns-resumen"))
    captura(p, "03_nueva_solicitud", True)
    with p.expect_download() as dl:
        p.click("#ns-emitir")
    t = toast(p)
    ok("solicitud: emite y descarga PDF", dl.value.suggested_filename.endswith(".pdf") and numero in t, f"{dl.value.suggested_filename} · {t}")
    ok("solicitud: formulario limpio y N.º siguiente", p.locator("#ns-items tr[data-i]").count() == 0 and p.text_content("#ns-numero").strip() != numero)

    # ---------------------------------------------------------------- solicitudes: detalle, aprobar, rechazar, gestionar
    ir(p, f"#solicitudes/{numero}")
    p.wait_for_selector("#d-solicitud:not([hidden])")
    p.wait_for_timeout(600)
    ok("solicitudes: URL propia abre el detalle", p.text_content("#ds-titulo") == numero)
    ok("solicitudes: breadcrumb con el N.º", numero in p.text_content("#crumbs"))
    captura(p, "04_solicitud_detalle")
    p.locator("#ds-items [data-item-sel]").nth(0).check()
    p.click("#ds-aprobar")
    t = toast(p)
    ok("solicitudes: aprobar ítem", "aprobado" in t, t)
    p.locator("#ds-items [data-item-sel]").nth(1).check()
    p.click("#ds-rechazar")
    p.fill("#pedir-area", "No corresponde (e2e)")
    p.click("#pedir-ok")
    t = toast(p)
    ok("solicitudes: rechazar con motivo", "rechazado" in t, t)
    p.locator("#ds-items [data-item-sel]").nth(0).check()
    p.click("#ds-gestionar")
    p.fill("#dg-obs", "Compra directa (e2e)")
    p.click("#dg-ok")
    t = toast(p)
    ok("solicitudes: marcar gestionado", "gestionado" in t.lower(), t)
    ok("solicitudes: actividad con el historial", "Gestionados" in p.text_content("#ds-actividad") and "Rechazados" in p.text_content("#ds-actividad"))
    p.click("#d-solicitud [data-cerrar-drawer]")
    p.wait_for_timeout(300)
    ok("solicitudes: cerrar el detalle vuelve al listado", p.evaluate("location.hash").startswith("#solicitudes") and "/" not in p.evaluate("location.hash"))
    if p.is_visible("#sl-limpiar"):
        p.click("#sl-limpiar")
    with p.expect_download() as dl:
        p.locator(f'#sl-tabla tr[data-id="{numero}"] [data-menu-fila]').click()
        p.click('#menu-sl-fila [data-act="pdf"]')
    ok("solicitudes: PDF desde el menú ⋯", dl.value.suggested_filename.endswith(".pdf"))
    fechas = p.locator("#sl-tabla tbody tr td:nth-child(3)").all_text_contents()
    ok("solicitudes: fechas dd/mm/aaaa", all(f.count("/") == 2 for f in fechas), fechas[:3])

    # acción masiva: una solicitud nueva aprobada desde el listado
    ir(p, "#nueva-solicitud")
    numero2 = p.text_content("#ns-numero").strip()
    elegir(p, "#ns-solicitante", "zubelza")
    elegir(p, "#ns-destino", "")
    p.fill("#ns-articulo", "Rodamiento 6204 e2e")
    p.keyboard.press("Escape")
    p.fill("#ns-cantidad", "4")
    p.fill("#ns-unidad", "unidad")
    p.keyboard.press("Escape")
    p.keyboard.press("Enter")
    p.locator("#ns-prioridad [data-prioridad]").first.click()
    with p.expect_download():
        p.click("#ns-emitir")
    toast(p)
    ir(p, "#solicitudes")
    if p.is_visible("#sl-limpiar"):
        p.click("#sl-limpiar")
    p.locator(f'#sl-tabla tr[data-id="{numero2}"] [data-sel]').check()
    ok("solicitudes: barra de acciones masivas", p.is_visible("#sl-bulk") and "1 solicitud" in p.text_content("#sl-bulk-n"))
    captura(p, "05_solicitudes_masivas")
    p.click('#sl-bulk [data-masiva="aprobar"]')
    p.click("#confirm-ok")
    t = toast(p)
    ok("solicitudes: aprobación masiva", "aprobada" in t, t)
    estado2 = p.locator(f'#sl-tabla tr[data-id="{numero2}"]').text_content()
    ok("solicitudes: estado Aprobada en el listado", "Aprobada" in estado2, estado2)

    # ---------------------------------------------------------------- nueva OC con vínculo
    ir(p, "#nueva-oc")
    ok("OC: Emitir deshabilitado sin proveedor", p.is_disabled("#oc-emitir"))
    elegir(p, "#oc-proveedor", "AB CONSTRUCCIONES")
    ok("OC: datos del proveedor en solo lectura", p.is_visible("#oc-prov-datos") and "CUIT" in p.text_content("#oc-prov-datos"))
    fila = p.locator("#oc-lineas tr").first
    fila.locator('[data-c="codigo"]').fill("ROD-6204")
    fila.locator("[data-vincular]").click()
    p.wait_for_selector("#d-vincular:not([hidden])")
    p.fill("#dv-q", numero2)
    p.wait_for_timeout(200)
    p.locator("#dv-tabla [data-vinc-check]:not([disabled])").first.check()
    p.click("#d-vincular [data-cerrar-drawer] >> nth=-1")
    ok("OC: ítem vinculado (chip «1 vinculado»)", "1 vinculado" in fila.locator("[data-vincular]").text_content())
    ok("OC: descripción completada desde el ítem", "Rodamiento" in fila.locator('[data-c="descripcion"]').input_value())
    fila.locator('[data-c="precio"]').fill("1234,5")
    fila.locator('[data-c="precio"]').press("Tab")
    ok("OC: precio con formato es-AR", fila.locator('[data-c="precio"]').input_value() == "1.234,50", fila.locator('[data-c="precio"]').input_value())
    ok("OC: neto calculado", "$ 4.938,00" in fila.text_content().replace("\xa0", " "), fila.text_content())
    ok("OC: total en el resumen", "4.938,00" in p.text_content("#oc-total"))
    captura(p, "06_nueva_oc", True)
    with p.expect_download() as dl:
        p.click("#oc-emitir")
    t = toast(p)
    oc = dl.value.suggested_filename.split("-")[0] + "-" + dl.value.suggested_filename.split("-")[1]
    ok("OC: emite y descarga PDF", dl.value.suggested_filename.startswith("OC-") and "emitida" in t, f"{dl.value.suggested_filename} · {t}")

    # ---------------------------------------------------------------- órdenes: detalle, editar, anular
    ir(p, f"#ordenes/{oc}")
    p.wait_for_timeout(800)
    ok("órdenes: URL propia abre el detalle", p.text_content("#od-numero") == oc, p.text_content("#od-numero"))
    ok("órdenes: solicitud vinculada", numero2 in p.text_content("#od-solicitudes"))
    p.click("#od-editar")
    p.wait_for_selector("#d-oc-editar:not([hidden])")
    p.locator('#de-tabla [data-c="precio"]').first.fill("1300")
    p.click("#de-guardar")
    t = toast(p)
    ok("órdenes: editar exige justificación", "motivo" in t.lower(), t)
    p.fill("#de-motivo", "Ajuste de precio (e2e)")
    p.click("#de-guardar")
    t = toast(p)
    ok("órdenes: edición registrada", "historial" in t, t)
    p.wait_for_timeout(500)
    ok("órdenes: chip Editada", "Editada" in p.text_content("#od-estado"))
    ok("órdenes: historial con la justificación", "Ajuste de precio (e2e)" in p.text_content("#od-historial"))
    captura(p, "07_oc_detalle", True)
    with p.expect_download() as dl:
        p.click("#od-pdf")
    ok("órdenes: PDF desde el detalle", dl.value.suggested_filename.endswith(".pdf"))
    p.click("#od-anular")
    ok("órdenes: anular exige justificación", p.is_disabled("#pedir-ok"))
    p.fill("#pedir-area", "Proveedor sin stock (e2e)")
    p.click("#pedir-ok")
    t = toast(p)
    ok("órdenes: anulada", "anulada" in t, t)
    p.wait_for_timeout(500)
    ok("órdenes: chip Anulada y sin Editar", "Anulada" in p.text_content("#od-estado") and not p.is_visible("#od-editar"))
    ir(p, "#ordenes")
    ok("órdenes: listado sin botón PDF por fila", p.locator("#or-tabla tbody tr").first.locator("text=Descargar PDF").count() == 0)
    p.select_option("#or-f-estado", "Anulada")
    ok("órdenes: filtro por estado", p.locator("#or-tabla tbody tr[data-id]").count() >= 1 and oc in p.text_content("#or-tabla tbody"))
    captura(p, "08_ordenes")

    # ---------------------------------------------------------------- proveedores
    ir(p, "#proveedores")
    ok("proveedores: panel propio con el total", "registrados" in p.text_content("#pv-sub"), p.text_content("#pv-sub"))
    ok("proveedores: paginado", "/" in p.text_content("#pv-pag"))
    p.click("#pv-nuevo")
    p.wait_for_selector("#d-proveedor:not([hidden])")
    p.fill('#dp-form [data-k="nombre"]', "PROVEEDOR E2E SRL")
    p.fill('#dp-form [data-k="cuit"]', "30-12345678-0")
    ok("proveedores: CUIT con dígito verificador inválido", p.is_visible("#dp-cuit-error"), p.text_content("#dp-cuit-error"))
    existente = p.evaluate("SMG && document.querySelector('#pv-tabla tbody tr td:nth-child(2)').textContent.trim()")
    p.fill('#dp-form [data-k="cuit"]', existente)
    ok("proveedores: CUIT duplicado", "Ya está cargado" in p.text_content("#dp-cuit-error"), p.text_content("#dp-cuit-error"))
    nuevo_cuit = cuit_valido("3071999888")
    p.fill('#dp-form [data-k="cuit"]', nuevo_cuit.replace("-", ""))
    p.fill('#dp-form [data-k="localidad"]', "Palpalá")
    p.click("#dp-guardar")
    t = toast(p)
    ok("proveedores: alta", "creado" in t, t)
    with sqlite3.connect(DB_COPIA) as c:
        fila_bd = c.execute('SELECT "CUIT" FROM proveedores WHERE "NOM_PROVEE" = ?', ("PROVEEDOR E2E SRL",)).fetchone()
    ok("proveedores: CUIT guardado con formato", fila_bd and fila_bd[0] == nuevo_cuit, str(fila_bd))
    r = p.request.post(f"{URL}/compras/proveedores", data={"nombre": "OTRO E2E", "cuit": nuevo_cuit})
    ok("proveedores: el backend rechaza el CUIT duplicado", r.status == 409, str(r.status))
    r = p.request.post(f"{URL}/compras/proveedores", data={"nombre": "OTRO E2E", "cuit": "30-12345678-0"})
    ok("proveedores: el backend rechaza el CUIT inválido", r.status == 400, str(r.status))
    p.fill("#pv-q", "PROVEEDOR E2E")
    p.wait_for_timeout(200)
    p.locator("#pv-tabla tbody tr[data-id]").first.click()
    p.wait_for_selector("#d-proveedor:not([hidden])")
    p.fill('#dp-form [data-k="telefono_1"]', "0388 4000000")
    p.click("#dp-guardar")
    ok("proveedores: edición", "actualizado" in toast(p))
    p.locator("#pv-tabla tbody tr[data-id] [data-menu-fila]").first.click()
    p.click('#menu-pv-fila [data-act="eliminar"]')
    p.click("#confirm-ok")
    ok("proveedores: baja con confirmación", "eliminado" in toast(p))
    captura(p, "09_proveedores")

    # ---------------------------------------------------------------- buscador global
    p.fill("#buscar-global", numero)
    p.wait_for_selector("#menu-buscar:not([hidden]) a")
    p.keyboard.press("Enter")
    p.wait_for_selector("#d-solicitud:not([hidden])")
    ok("buscador: N.º SC abre la solicitud", p.text_content("#ds-titulo") == numero)
    p.keyboard.press("Escape")

    # ---------------------------------------------------------------- navegador: atrás
    ir(p, "#proveedores")
    ir(p, "#ordenes")
    p.go_back()
    p.wait_for_timeout(600)
    ok("navegación: atrás vuelve al panel anterior", p.evaluate("location.hash") == "#proveedores" and p.is_visible('[data-vista="proveedores"]'))

    # ---------------------------------------------------------------- responsive y zoom (ancho efectivo del viewport)
    for ancho, nombre in ((2150, "zoom67"), (1920, "zoom75"), (1440, "zoom100"), (1152, "zoom125"), (1024, "tablet"), (390, "movil")):
        q = sesion(nav, "contingencia_test", "clave-contingencia-test", ancho, 900)
        for vista in ("panel", "solicitudes", "nueva-oc"):
            q.goto(f"{URL}/compras#{vista}")
            q.wait_for_timeout(1200)
            desborde = q.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            ok(f"responsive {nombre} ({ancho}px) {vista}: sin scroll horizontal de la página", desborde <= 0, str(desborde))
            captura(q, f"10_{nombre}_{vista}")
        q.context.close()

    # ---------------------------------------------------------------- permisos
    q = sesion(nav, *SOLICITANTE)
    q.goto(f"{URL}/compras")
    q.wait_for_timeout(1200)
    visibles = q.eval_on_selector_all("#sidebar [data-panel]:not([hidden])", "els => els.map((e) => e.dataset.panel)")
    ok("permisos: solo ve Nueva solicitud y Solicitudes", sorted(visibles) == ["solicitud_compra", "solicitudes_emitidas"], str(visibles))
    ok("permisos: entra a Nueva solicitud", q.evaluate("location.hash") == "#nueva-solicitud", q.evaluate("location.hash"))
    q.goto(f"{URL}/compras#ordenes")
    q.wait_for_timeout(800)
    ok("permisos: sin acceso a Órdenes de compra", q.evaluate("location.hash") != "#ordenes")
    q.goto(f"{URL}/compras#solicitudes")
    q.wait_for_timeout(1000)
    ok("permisos: sin selección masiva", q.locator("#sl-tabla [data-sel]").count() == 0)
    q.context.close()

    # ---------------------------------------------------------------- Logística sigue igual con el shell nuevo
    p.goto(f"{URL}/vehiculos_form")
    p.wait_for_timeout(1500)
    ok("logística: Vehículos con breadcrumb Logística", "Logística" in p.text_content("#crumbs"), p.text_content("#crumbs"))
    ok("logística: ítem activo Vehículos", p.get_attribute('#sidebar [data-panel="vehiculos"]', "aria-current") == "page")
    captura(p, "11_vehiculos")
    nav.close()

print("\n".join(RESULTADOS))
print(f"\n{sum(r.startswith('OK') for r in RESULTADOS)} OK · {sum(r.startswith('FALLA') for r in RESULTADOS)} FALLAS")
print("\nErrores de JS:\n" + ("\n".join(ERRORES) if ERRORES else "ninguno"))
