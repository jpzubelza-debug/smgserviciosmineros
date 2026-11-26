"""Prueba de punta a punta de Almacén (Edge real vía Playwright, base copiada).

Uso: .venv-1\\Scripts\\python.exe tests/e2e_almacen.py <carpeta_capturas>
Copia dashboard.db a una carpeta temporal, levanta el servidor sobre la copia y recorre los flujos:
catálogos (alta, edición, desactivar, borrado bloqueado, unificar), alta de producto con N.º de serie, ingreso con series,
validaciones de series y stock, salida, autorización y rechazo de RE, documentos con detalle y PDF, movimientos,
inventario con filtros y exportación PDF/Excel, punto de reposición masivo, calidad de datos, buscador, permisos y responsive.
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
from unittest import mock

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.getenv("SMG_BASE_DIR") or os.path.dirname(AQUI)
CAPTURAS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "capturas"))
os.makedirs(CAPTURAS, exist_ok=True)

DB_REAL = os.getenv("SMG_TEST_DB_ORIGEN") or os.path.join(RAIZ, "dashboard.db")
MTIME_REAL = os.path.getmtime(DB_REAL)
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_almacen_")
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
mock.patch.object(main, "_enviar_notificacion_remito", lambda *a, **kw: None).start()
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

# Usuario con permisos acotados: solo inventario y movimientos
CONSULTA = ("consulta.almacen@test.local", "clave-consulta-1")
with sqlite3.connect(DB_COPIA) as conn:
    conn.execute(
        """INSERT INTO usuarios (nombre_apellido, dni, legajo, correo, password_hash, estado, tipo_usuario, modulos_json,
        paneles_json, acciones_json, bloqueado, intentos_fallidos, password_temporal) VALUES
        ('Consulta Almacen', '99999996', '', ?, ?, 'ACTIVO', 'USUARIO', '["almacen"]', ?, '{}', 0, 0, 0)""",
        (CONSULTA[0], hashlib.sha256(CONSULTA[1].encode()).hexdigest(), json.dumps({"almacen": ["inventario", "movimientos"]})))

RESULTADOS, ERRORES = [], []


def ok(nombre, condicion, detalle=""):
    RESULTADOS.append(f"{'OK   ' if condicion else 'FALLA'} {nombre}{f' · {detalle}' if detalle else ''}")


def vigilar(pagina, quien):
    pagina.on("pageerror", lambda e: ERRORES.append(f"{quien}: {e}"))
    pagina.on("console", lambda m: ERRORES.append(f"{quien} consola: {m.text}") if m.type == "error" and " 40" not in m.text else None)


def captura(pagina, nombre, completa=False):
    pagina.screenshot(path=os.path.join(CAPTURAS, f"al_{nombre}.png"), full_page=completa)


def db(sql, args=()):
    with sqlite3.connect(DB_COPIA) as c:
        return c.execute(sql, args).fetchall()


def toast_texto(p, previos=None):
    """Texto del toast nuevo (los anteriores pueden seguir en pantalla)."""
    p.wait_for_timeout(250)
    p.wait_for_selector(".toast:not(.out)", timeout=8000)
    textos = p.locator(".toast").all_inner_texts()
    return textos[-1] if textos else ""


def elegir_combo(p, selector, texto, opcion=None):
    p.fill(selector, texto)
    p.wait_for_timeout(150)
    lista = p.locator(".combo-list:not([hidden]) .combo-opt[data-i]")
    try:
        (lista.filter(has_text=opcion).first if opcion else lista.first).click(timeout=5000)
    except Exception:
        captura(p, "falla_combo")
        print(chr(10).join(RESULTADOS))
        print("ERRORES JS:", ERRORES)
        raise


def entrar(ctx, usuario, clave):
    r = ctx.request.post(f"{URL}/login", form={"username": usuario, "password": clave})
    assert r.ok, r.status


with sync_playwright() as pw:
    nav = pw.chromium.launch(channel="msedge", headless=True)
    ctx = nav.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    entrar(ctx, "contingencia_test", "clave-contingencia-test")
    p = ctx.new_page()
    vigilar(p, "admin")

    # ---------------------------------------------------------------- shell y navegación
    p.goto(f"{URL}/almacen")
    p.wait_for_selector("#sidebar .sb-item[data-panel='dashboard']")
    ok("Redirige a #panel", p.evaluate("location.hash") == "#panel")
    ok("Migas Almacén / Panel", "Almacén" in p.inner_text("#crumbs") and "Panel" in p.inner_text("#crumbs"))
    ok("Selector de módulo muestra Almacén", "Almacén" in p.inner_text("#sb-modulo"))
    ok("Sin CSS propio ni fondo", p.evaluate("!document.querySelector('style, .bg-photo, .layout, .metric-grid, .dash-hero, .productos-table, .ios-items-table')"))
    ok("Todas las tablas con .tbl", p.evaluate("[...document.querySelectorAll('table')].every(t => t.classList.contains('tbl'))"))
    p.click("#sb-modulo")
    ok("Menú de módulos con Almacén", p.locator("#menu-modulos [data-modulo='almacen']").is_visible())
    p.keyboard.press("Escape")

    # ---------------------------------------------------------------- catálogos
    p.goto(f"{URL}/almacen#catalogos/categorias")
    p.wait_for_selector("#ct-tabla tbody tr[data-id]")
    p.click("#ct-nuevo")
    p.fill("#dk-nombre", "Comunicaciones E2E")
    p.click("#dk-guardar")
    ok("Alta de categoría", "creado" in toast_texto(p))
    p.click("#ct-nuevo")
    p.fill("#dk-nombre", "comunicaciones  e2e")
    ok("Duplicado detectado (sin tildes/espacios)", p.locator("#dk-error").is_visible())
    p.click("#d-catalogo [data-cerrar-drawer]")
    # marca con error de tipeo y su par
    p.goto(f"{URL}/almacen#catalogos/marcas")
    p.wait_for_selector("#ct-tabla tbody tr[data-id]")
    for nombre in ["Motorola E2E", "Motorolla E2E"]:
        p.click("#ct-nuevo")
        p.fill("#dk-nombre", nombre)
        p.click("#dk-guardar")
        p.wait_for_timeout(400)
    fila = p.locator("#ct-tabla tbody tr", has_text="Motorolla E2E")
    ok("Registro parecido marcado con alerta", fila.locator(".warn-ico").count() == 1)
    fila.locator("[data-menu-fila]").click()
    p.locator("#menu-acciones .menu-item", has_text="Editar").click()
    p.fill("#dk-nombre", "Motorolla E2E bis")
    p.click("#dk-guardar")
    ok("Edición de nombre", "guardados" in toast_texto(p))
    p.wait_for_timeout(300)
    p.locator("#ct-tabla tbody tr", has_text="Motorolla E2E bis").locator("[data-menu-fila]").click()
    p.locator("#menu-acciones .menu-item", has_text="Desactivar").click()
    p.wait_for_timeout(500)
    ok("Desactivar marca", db("SELECT activo FROM marcas WHERE nombre='Motorolla E2E bis'")[0][0] == 0)
    captura(p, "catalogos")

    # ---------------------------------------------------------------- alta de productos
    p.goto(f"{URL}/almacen#productos")
    p.wait_for_selector("#pr-nuevo")
    p.click("#pr-nuevo")
    p.wait_for_selector("#d-producto:not([hidden])")
    ok("Alta sin «Stock de ingreso»", p.locator("text=Stock de ingreso").count() == 0 and p.locator("#dp-aviso-stock").is_visible())
    p.fill("#dp-codigo", "E2E-RAD-1")
    p.fill("#dp-desc", "RADIO PORTATIL E2E")
    elegir_combo(p, "#dp-categoria", "Comunicaciones E2E")
    elegir_combo(p, "#dp-marca", "Motorola E2E")
    p.fill("#dp-repo", "2")
    p.check("#dp-serie", force=True)
    p.click("#dp-guardar")
    ok("Alta de producto serializado", "creado" in toast_texto(p))
    fila = db("SELECT tipo_control, punto_reposicion, categoria_id FROM productos WHERE codigo='E2E-RAD-1'")
    ok("Guarda N.º de serie y punto de reposición", fila and fila[0][0] == "SERIALIZADO" and fila[0][1] == 2, str(fila))
    p.click("#pr-nuevo")
    p.wait_for_selector("#d-producto:not([hidden])")
    p.wait_for_timeout(300)
    p.fill("#dp-codigo", "E2E-GUA-1")
    p.fill("#dp-desc", "GUANTE E2E")
    # «+ crear» inline desde el combobox
    p.fill("#dp-familia", "Familia E2E")
    p.locator(".combo-list:not([hidden]) [data-crear]").click()
    p.wait_for_selector("#pedir:not([hidden])")
    p.click("#pedir-ok")
    p.wait_for_timeout(500)
    ok("Crear familia inline", p.input_value("#dp-familia") == "Familia E2E")
    p.click("#dp-guardar")
    toast_texto(p)
    p.click("#pr-nuevo")
    p.wait_for_selector("#d-producto:not([hidden])")
    p.wait_for_timeout(300)
    p.fill("#dp-codigo", "e2e-gua-1")
    ok("Código duplicado avisado", p.locator("#dp-codigo-error").is_visible())
    p.click("#d-producto [data-cerrar-drawer]")
    p.fill("#pr-q", "E2E")
    p.wait_for_timeout(200)
    ok("Listado de productos filtra", p.locator("#pr-tabla tbody tr[data-id]").count() == 2)
    ok("Filas de 40px", p.evaluate("Math.round(document.querySelector('#pr-tabla tbody tr[data-id]').getBoundingClientRect().height)") <= 41)

    # ---------------------------------------------------------------- ingreso con series
    p.goto(f"{URL}/almacen#ingresos")
    p.wait_for_selector("#is-producto")
    ok("Generar remito deshabilitado sin ítems", p.is_disabled("#is-generar"))
    p.fill("#is-proveedor", "Proveedor E2E")
    p.select_option("#is-ubicacion", index=1)
    elegir_combo(p, "#is-producto", "E2E-RAD-1")
    p.fill("#is-cantidad", "2")
    p.fill("#is-serie", "SN-A")
    p.keyboard.press("Enter")
    p.click("#is-agregar")
    ok("Valida cantidad = series", "1" in p.inner_text("#is-error") and p.locator("#is-error").is_visible())
    p.fill("#is-serie", "SN-B")
    p.keyboard.press("Enter")
    p.click("#is-agregar")
    ok("Agrega línea con 2 series", p.locator("#is-items tr").count() == 1 and "SN-A, SN-B" in p.inner_text("#is-items"))
    elegir_combo(p, "#is-producto", "E2E-GUA-1")
    p.fill("#is-cantidad", "10")
    p.click("#is-agregar")
    ok("Resumen con 2 ítems y 12 unidades", "12" in p.inner_text("#is-resumen") and p.inner_text("#is-cant") == "2")
    captura(p, "ingreso")
    p.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")
    p.click("#is-generar")
    t = toast_texto(p)
    ok("Ingreso generado", "Ingreso RI-" in t, t)
    stock = dict(db("SELECT p.codigo, s.stock_actual FROM stock s JOIN productos p ON p.id = s.producto_id WHERE p.codigo LIKE 'E2E-%'"))
    ok("Stock sumado", stock.get("E2E-RAD-1") == 2 and stock.get("E2E-GUA-1") == 10, str(stock))
    ok("Ubicación legible en el remito", db("SELECT instalacion FROM remitos_ingreso_detalle ORDER BY id DESC LIMIT 1")[0][0].count(" - ") == 1)

    # backend: serializado sin series se rechaza
    pid = db("SELECT id FROM productos WHERE codigo='E2E-RAD-1'")[0][0]
    r = ctx.request.post(f"{URL}/almacen/remitos_ingreso", data=json.dumps({"items": [{"producto_id": pid, "cantidad": 1, "series": []}]}), headers={"Content-Type": "application/json"})
    ok("Backend rechaza serializado sin N.º de serie", "serie" in (r.json().get("error") or ""))

    # ---------------------------------------------------------------- salida
    p.click("#is-tipo [data-tipo='RE']")
    ok("Salida muestra destinatario y envío", p.locator("#is-destinatario").is_visible() and p.locator("#is-envio").is_visible())
    elegir_combo(p, "#is-producto", "E2E-GUA-1")
    p.fill("#is-cantidad", "50")
    p.click("#is-agregar")
    ok("Advierte stock insuficiente", "insuficiente" in p.inner_text("#is-error"))
    p.fill("#is-cantidad", "3")
    p.click("#is-agregar")
    elegir_combo(p, "#is-producto", "E2E-RAD-1")
    p.fill("#is-cantidad", "1")
    p.fill("#is-serie", "SN-A")
    p.keyboard.press("Enter")
    p.click("#is-agregar")
    ok("Generar deshabilitado sin destinatario", p.is_disabled("#is-generar"))
    p.fill("#is-destinatario", "Destinatario E2E")
    p.press("#is-destinatario", "Tab")
    p.wait_for_timeout(200)
    p.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")
    p.click("#is-generar")
    t = toast_texto(p)
    ok("Salida registrada pendiente", "pendiente" in t, t)
    p.wait_for_timeout(800)
    ok("Contador de pendientes en la sidebar", p.locator("#sidebar [data-panel='pendientes'] .sb-badge").count() == 1)

    # ---------------------------------------------------------------- pendientes: autorizar
    p.goto(f"{URL}/almacen#pendientes")
    p.wait_for_selector("#pe-tabla tbody tr[data-id]")
    p.locator("#pe-tabla tbody tr[data-id]").first.locator("[data-menu-fila]").click()
    p.locator("#menu-acciones .menu-item", has_text="Autorizar").click()
    p.click("#pedir-ok")
    ok("RE autorizado", "autorizado" in toast_texto(p))
    stock = dict(db("SELECT p.codigo, s.stock_actual FROM stock s JOIN productos p ON p.id = s.producto_id WHERE p.codigo LIKE 'E2E-%'"))
    ok("Stock descontado al autorizar", stock.get("E2E-GUA-1") == 7 and stock.get("E2E-RAD-1") == 1, str(stock))

    # rechazo
    p.goto(f"{URL}/almacen#ingresos")
    p.click("#is-tipo [data-tipo='RE']")
    p.fill("#is-destinatario", "Otro E2E")
    elegir_combo(p, "#is-producto", "E2E-GUA-1")
    p.fill("#is-cantidad", "1")
    p.click("#is-agregar")
    p.click("#is-generar")
    toast_texto(p)
    numero_re = db("SELECT numero FROM remitos_entrega ORDER BY id DESC LIMIT 1")[0][0]
    p.goto(f"{URL}/almacen#documentos/{numero_re}")
    p.wait_for_selector("#d-documento:not([hidden]) #dd-rechazar")
    p.wait_for_selector("#dd-items td.mono")
    ok("Detalle del documento con ítems", "GUANTE E2E" in p.inner_text("#dd-items"))
    p.click("#dd-rechazar")
    p.fill("#pedir-area", "Prueba de rechazo")
    p.click("#pedir-ok")
    ok("RE rechazado", "rechazado" in toast_texto(p))
    ok("Estado NO_AUTORIZADO", db("SELECT estado_autorizacion FROM remitos_entrega WHERE numero=?", (numero_re,))[0][0] == "NO_AUTORIZADO")

    # ---------------------------------------------------------------- documentos y PDF
    p.goto(f"{URL}/almacen#documentos")
    p.wait_for_selector("#dc-tabla tbody tr[data-id]")
    ok("Documentos con fecha dd/mm/aaaa", p.evaluate("/^\\d{2}\\/\\d{2}\\/\\d{4}$/.test(document.querySelector('#dc-tabla tbody tr td:nth-child(3)').textContent.trim())"))
    p.locator("#dc-tabla tbody tr[data-id]").first.locator("[data-menu-fila]").click()
    with p.expect_download() as d:
        p.locator("#menu-acciones .menu-item", has_text="Descargar PDF").click()
    ok("PDF del documento", d.value.suggested_filename.endswith(".pdf"), d.value.suggested_filename)
    p.select_option("#dc-f-tipo", "RI")
    ok("Filtro por tipo", p.locator("#dc-tabla tbody tr[data-id]").count() >= 1 and "RE-" not in p.inner_text("#dc-tabla tbody"))
    captura(p, "documentos")

    # ---------------------------------------------------------------- movimientos y exportación
    p.goto(f"{URL}/almacen#movimientos")
    p.wait_for_selector("#mv-tabla tbody tr[data-id]")
    ok("Movimientos con chip de tipo", p.locator("#mv-tabla tbody .chip").count() > 0)
    p.click("#mv-tipo [data-v='SALIDA']")
    ok("Filtro Salidas", "Entrada" not in p.inner_text("#mv-tabla tbody"))
    p.click("[data-exportar='movimientos']")
    with p.expect_download() as d:
        p.locator("#menu-acciones .menu-item", has_text="Excel").click()
    ok("Excel de movimientos", d.value.suggested_filename.endswith(".xlsx"), d.value.suggested_filename)
    ok("Sin tablas de altura fija", p.evaluate("getComputedStyle(document.querySelector('#mv-wrap')).maxHeight") == "none")

    # ---------------------------------------------------------------- inventario, ficha y exportación
    p.goto(f"{URL}/almacen#inventario?q=E2E")
    p.wait_for_selector("#iv-tabla tbody tr[data-id]")
    ok("Inventario incluye productos sin movimientos", len(ctx.request.get(f"{URL}/almacen/v2/stock").json()) == db("SELECT COUNT(*) FROM productos")[0][0])
    p.locator("#iv-tabla tbody tr[data-id]", has_text="E2E-RAD-1").click()
    p.wait_for_selector("#d-ficha:not([hidden])")
    p.wait_for_timeout(800)
    ok("Ficha con N.º de serie en stock", "SN-B" in p.inner_text("#df-cuerpo"), p.inner_text("#df-series") if p.locator("#df-series").count() else "")
    captura(p, "ficha")
    p.keyboard.press("Escape")
    # ancho de columna manual: arrastrar el borde del encabezado «Familia»
    ancho_col = "document.querySelector('#iv-tabla th:nth-child(3)').getBoundingClientRect().width"
    orden_antes = p.evaluate("document.querySelector('#iv-tabla th[aria-sort]:not([aria-sort=none])').textContent")
    asa = p.locator("#iv-tabla th:nth-child(3) .col-resize")
    caja = asa.bounding_box()
    w0 = p.evaluate(ancho_col)
    p.mouse.move(caja["x"] + 4, caja["y"] + caja["height"] / 2)
    p.mouse.down()
    p.mouse.move(caja["x"] + 124, caja["y"] + caja["height"] / 2, steps=6)
    p.mouse.up()
    w1 = p.evaluate(ancho_col)
    ok("Columna ensanchada a mano", w1 - w0 >= 100, f"{w0:.0f} → {w1:.0f}")
    ok("Arrastrar no reordena", p.evaluate("document.querySelector('#iv-tabla th[aria-sort]:not([aria-sort=none])').textContent") == orden_antes)
    p.reload()
    p.wait_for_selector("#iv-tabla tbody tr[data-id]")
    ok("El ancho se recuerda al recargar", abs(p.evaluate(ancho_col) - w1) <= 2, f"{p.evaluate(ancho_col):.0f}")
    p.locator("#iv-tabla th:nth-child(3) .col-resize").dblclick()
    ok("Doble clic restablece el ancho", abs(p.evaluate(ancho_col) - w0) <= 2, f"{p.evaluate(ancho_col):.0f}")
    p.locator("#iv-tabla th:nth-child(3) .col-resize").focus()
    p.keyboard.press("ArrowLeft")
    ok("Achicar con el teclado", p.evaluate(ancho_col) < w0 - 10, f"{p.evaluate(ancho_col):.0f}")
    p.locator("#iv-tabla th:nth-child(3) .col-resize").dblclick()
    p.click("#iv-stock [data-v='repo']")
    ok("Bajo reposición muestra el serializado (1 ≤ 2)", "E2E-RAD-1" in p.inner_text("#iv-tabla tbody"))
    p.click("[data-exportar='inventario']")
    with p.expect_download() as d:
        p.locator("#menu-acciones .menu-item", has_text="PDF").click()
    ok("PDF de inventario", d.value.suggested_filename.endswith(".pdf"), d.value.suggested_filename)
    p.click("[data-exportar='inventario']")
    with p.expect_download() as d:
        p.locator("#menu-acciones .menu-item", has_text="Excel").click()
    ok("Excel de inventario", d.value.suggested_filename.endswith(".xlsx"), d.value.suggested_filename)

    # ---------------------------------------------------------------- punto de reposición masivo
    p.goto(f"{URL}/almacen#productos?q=E2E-GUA")
    p.wait_for_selector("#pr-repo")
    p.click("#pr-repo")
    p.select_option("#pr-f-control", "")
    p.fill("#pr-q", "E2E-GUA")
    p.wait_for_timeout(200)
    p.fill("#pr-tabla [data-repo]", "8")
    p.click("#pr-bulk-guardar")
    ok("Edición masiva de reposición", "actualizado" in toast_texto(p))
    ok("Reposición guardada", db("SELECT punto_reposicion FROM productos WHERE codigo='E2E-GUA-1'")[0][0] == 8)

    # ---------------------------------------------------------------- borrado bloqueado y unificar
    p.goto(f"{URL}/almacen#catalogos/marcas")
    p.wait_for_selector("#ct-tabla tbody tr[data-id]")
    p.locator("#ct-tabla tbody tr", has_text="Motorola E2E").first.locator("[data-menu-fila]").click()
    p.locator("#menu-acciones .menu-item", has_text="Eliminar").click()
    ok("Eliminar bloqueado con productos asociados", "No se puede eliminar" in p.inner_text("#confirm-title"))
    p.click("#confirm-cancel")
    r = ctx.request.delete(f"{URL}/almacen/v2/catalogos/marcas/{db('SELECT id FROM marcas WHERE nombre=?', ('Motorola E2E',))[0][0]}")
    ok("Backend bloquea el borrado", "asociado" in (r.json().get("error") or ""))
    p.locator("#ct-tabla tbody tr", has_text="Motorola E2E").first.locator("[data-menu-fila]").click()
    p.locator("#menu-acciones .menu-item", has_text="Unificar").click()
    p.select_option("#du-destino", label=[o for o in p.locator("#du-destino option").all_inner_texts() if o.startswith("Motorolla E2E bis")][0])
    ok("Vista previa de unificación", "1 producto(s)" in p.inner_text("#du-preview"))
    p.click("#du-ok")
    p.click("#confirm-ok")
    ok("Unificación hecha", "Unificados" in toast_texto(p))
    ok("Producto pasó a la marca destino", db("SELECT m.nombre FROM productos p JOIN marcas m ON m.id = p.id_marca WHERE p.codigo='E2E-RAD-1'")[0][0] == "Motorolla E2E bis")

    # ---------------------------------------------------------------- calidad de datos y buscador
    p.goto(f"{URL}/almacen#productos")
    p.wait_for_selector("#pr-tabla tbody tr[data-id]")
    p.wait_for_timeout(500)
    chip = p.locator("[data-vista='productos'] [data-dq]")
    if chip.is_visible():
        chip.click()
        p.wait_for_selector("#d-dq:not([hidden])")
        ok("Panel de calidad de datos", "punto de reposición" in p.inner_text("#dq-cuerpo"))
        captura(p, "calidad")
        p.keyboard.press("Escape")
    else:
        ok("Chip de inconsistencias", False, "no visible")
    p.fill("#buscar-global", numero_re)
    p.press("#buscar-global", "Enter")
    p.wait_for_timeout(600)
    ok("Buscador global abre documentos", p.evaluate("location.hash").startswith("#documentos") and numero_re in p.inner_text("#dc-tabla tbody"),
       f"{p.evaluate('location.hash')} · {p.inner_text('#dc-rango')}")

    # ---------------------------------------------------------------- panel
    p.goto(f"{URL}/almacen#panel")
    p.wait_for_selector("#pn-kpis a.kpi")
    ok("5 KPIs con enlace", p.locator("#pn-kpis a.kpi").count() == 5)
    ok("KPIs en una fila (1440)", len(set(p.evaluate("[...document.querySelectorAll('#pn-kpis .kpi')].map(k => Math.round(k.getBoundingClientRect().top))"))) == 1)
    p.click("#pn-top-n [data-n='5']")
    p.wait_for_timeout(600)
    ok("Top 5", p.locator("#pn-top .meter-row").count() <= 5)
    captura(p, "panel", completa=True)

    # ---------------------------------------------------------------- un primario por vista, sin uppercase, responsive
    for v in ["panel", "inventario", "productos", "movimientos", "documentos", "pendientes", "ingresos", "catalogos"]:
        p.goto(f"{URL}/almacen#{v}")
        p.wait_for_timeout(700)
        n = p.evaluate("[...document.querySelectorAll('#contenido > [data-vista]:not([hidden]) .btn-primary')].filter(b => b.offsetParent).length")
        ok(f"Un primario como máximo en #{v}", n <= 1, str(n))
        mayus = p.evaluate("[...document.querySelectorAll('#contenido label, #contenido th')].filter(e => getComputedStyle(e).textTransform === 'uppercase').length")
        ok(f"Sin labels uppercase en #{v}", mayus == 0)
        for ancho in (1280, 768, 390):
            p.set_viewport_size({"width": ancho, "height": 900})
            p.wait_for_timeout(150)
            desborde = p.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            ok(f"Sin scroll horizontal de página #{v} a {ancho}px", desborde <= 0, str(desborde))
        p.set_viewport_size({"width": 1440, "height": 900})

    # ---------------------------------------------------------------- permisos
    ctx2 = nav.new_context(viewport={"width": 1280, "height": 800})
    entrar(ctx2, *CONSULTA)
    q = ctx2.new_page()
    vigilar(q, "consulta")
    q.goto(f"{URL}/almacen#catalogos")
    q.wait_for_timeout(1500)
    ok("Usuario sin dashboard entra a la página", "/almacen" in q.url, q.url)
    ok("Vista sin permiso redirige a una permitida", q.evaluate("location.hash") in ("#inventario", "#movimientos"), q.evaluate("location.hash"))
    visibles = q.evaluate("[...document.querySelectorAll('#sidebar [data-panel]')].filter(a => !a.hidden).map(a => a.dataset.panel)")
    ok("Sidebar solo con sus paneles", sorted(visibles) == ["inventario", "movimientos"], str(visibles))
    ctx2.close()
    nav.close()

ok("La base real no se tocó", os.path.getmtime(DB_REAL) == MTIME_REAL)
print("\n".join(RESULTADOS))
print(f"\n{sum(r.startswith('OK') for r in RESULTADOS)} OK · {sum(r.startswith('FALLA') for r in RESULTADOS)} FALLAS")
print("ERRORES JS:", ERRORES or "ninguno")
_SRV.should_exit = True
