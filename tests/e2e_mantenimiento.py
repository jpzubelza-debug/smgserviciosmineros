"""Prueba de punta a punta de Mantenimiento (Edge real vía Playwright, base copiada).

Uso: .venv-1\\Scripts\\python.exe tests/e2e_mantenimiento.py <carpeta_capturas>
Reutiliza la preparación de tests/test_mantenimiento.py (copia de la base y servidor). Recorre la app (operador reporta,
mecánico trabaja la OT) y la web (aprobador resuelve, administrador finaliza, equipos). Informa errores de JS.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(AQUI))
CAPTURAS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(AQUI), "capturas"))
os.makedirs(CAPTURAS, exist_ok=True)

import test_mantenimiento as t  # noqa: E402  (copia la base y levanta el servidor)
from playwright.sync_api import sync_playwright  # noqa: E402

URL = f"http://127.0.0.1:{t.PUERTO}"
RESULTADOS, ERRORES = [], []
t.MantenimientoTests.setUpClass()
CI = "M-33"
MAIL_SUPERVISOR = """Buenos dias estimados, paso a detallar novedades mecánicas de los equipos que se encuentran en sitio:
- Rodillo Compactador M24 Caterpillar:
  El equipo presenta perdidas de aceite por turbo, reten de bancada y por las juntas de bomba de agua y bomba
inyectora.
   Asiento en mal estado de tapiceria,
   Falta tensor de correa alternador,
   Falta compresor de aire acondicionado,
   Presenta cuatro tacos de goma del rodillo rajados,
   Terminales de mangueras hidráulicas en estado de oxidación y corrosión por sal
    Mangueras hidráulicas presentan daño en la aislación: se observa la malla metálica.

- Motoniveladora M26 Caterpillar:
  En el equipo se observa que el cilindro de levante de tornamesa esta filtrando fluido lubricante; es el que se
encuentra del lado derecho.
   Asiento de operador no regula en altura.
   Se agrega cada cuatro o cinco dias dos litros de aceite.
   Cubiertas traseras en un 60%.

- Regador C28:
  El equipo viene manifestando desde hace tiempo problemas de embrague,
  Cubiertas con desgaste notorio. Medidas de las mismas: 1100*20.
  Se siguen presentando inconvenientes eléctricos respecto de luces reglamentarias.
   Torre de dirección presenta juego.

- Regador C16 Iveco Trakker:
 Pulmon de freno con diafragma roto,
 Falta aire acondicionado y calefacción,
 Hoja de elastico quebrada (3* trasera derecha),
 Soldaduras de soporte tanque de agua con fisuras,

- Excavadora M40:
 Cilindro del balde con vástago lastimado, pierde aceite.

- Cargadora Frontal M 34:
 Uñas y cuchillas del balde gastadas y quebradas las punteras,
 Termómetro de temperatura del motor intermitente, la mayor parte del tiempo no registra nada.
 Filtro de aire en mal estado, no se cambió en dos servicios que se realizaron por falta de elemento filtrante en el kit.
 No entra grasa en pernos superiores del balde y en crucetas de cardan.

- Camion C24 Volquete XCMG:
  Falta luces de marcha atras, no tiene bocina de retroceso.
  Puerta dañada en bisagra por efecto del viento
  Cableado trasero sulfatado por efecto de sal,
  Golpe en motor,
  Luces led lado derecho no funcionan,
  Cinturon de seguridad no funciona,
 Cubre tanque desoldado,
 Cubre carter chocado, presenta daño,
 Choque lado derecho abajo a la altura de farol,


Sin más saludo atte.
P/D: Se envía registro fotográfico via whatsApp."""
PEGAR_JS = """([sel, texto]) => { const ta = document.querySelector(sel); const dt = new DataTransfer(); dt.setData('text/plain', texto);
  ta.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }"""


def ok(nombre, condicion, detalle=""):
    RESULTADOS.append(f"{'OK   ' if condicion else 'FALLA'} {nombre}{f' · {detalle}' if detalle else ''}")


def vigilar(pagina, quien):
    pagina.on("pageerror", lambda e: ERRORES.append(f"{quien}: {e}"))
    pagina.on("console", lambda m: ERRORES.append(f"{quien} consola: {m.text}") if m.type == "error" and "40" not in m.text else None)


def captura(pagina, nombre):
    pagina.screenshot(path=os.path.join(CAPTURAS, f"mt_{nombre}.png"), full_page=False)


def app(navegador, legajo):
    ctx = navegador.new_context(viewport={"width": 400, "height": 860}, locale="es-AR", is_mobile=True, has_touch=True)
    r = ctx.request.post(f"{URL}/operador/api/login", data={"legajo": legajo, "pin": t.PIN})
    assert r.ok, r.text()
    p = ctx.new_page()
    vigilar(p, f"app {legajo}")
    p.on("dialog", lambda d: d.accept())
    p.goto(f"{URL}/operador")
    p.wait_for_selector("#app:not([hidden])")
    return p


with sync_playwright() as pw:
    nav = pw.chromium.launch(channel="msedge", headless=True)

    # ---------------------------------------------------------------- operador
    op = app(nav, t.OPER)
    ok("app: pestaña Novedades visible", op.is_visible('.tabbar [data-tab="novedades"]'))
    ok("app: operador sin pestaña Mantenimiento", not op.is_visible('.tabbar [data-tab="mto"]'))
    op.click('.tabbar [data-tab="nuevo"]')
    op.click('[data-picker="ci"]')
    op.fill("#pk-q", CI)
    op.click(f'#pk-list .opt[data-v="{CI}"]')
    op.wait_for_selector("#mt-aviso-eq:not([hidden])")
    ok("parte: aviso del equipo bajo el selector", "novedad" in op.text_content("#mt-aviso-eq").lower(), op.text_content("#mt-aviso-eq")[:80])
    op.click("#mt-ir-nov")
    op.wait_for_selector("#mt-nueva:not([hidden])")
    ok("novedades: llega con el equipo elegido", CI in op.text_content("#mt-obj"))
    IMAGEN = os.path.join(CAPTURAS, "mt_foto_prueba.png")
    op.screenshot(path=IMAGEN)  # cualquier imagen real sirve: la app la comprime a JPG
    op.fill("#mt-items [data-desc]", "Pérdida de aceite en la transmisión (e2e)")
    op.set_input_files("#mt-items [data-foto-in]", IMAGEN)
    ok("novedades: foto lista para enviar", "Foto lista" in op.text_content("#mt-items [data-foto]"))
    op.check("#mt-items [data-urg]")
    op.click("#mt-mas")
    op.fill("#mt-items [data-i='1'] [data-desc]", "Espejo retrovisor roto (e2e2)")
    ok("novedades: varios ítems en un envío", op.text_content("#mt-enviar").strip() == "Enviar 2 novedades", op.text_content("#mt-enviar"))
    op.click("#mt-enviar")
    op.wait_for_selector("#mt-pend [data-conf]")
    captura(op, "01_app_novedades")
    ok("novedades: la nueva aparece como pendiente con «Sigue igual»", "(e2e)" in op.text_content("#mt-pend"))
    op.click("#mt-pend [data-conf]")
    op.wait_for_timeout(600)
    ok("novedades: confirmación registrada", "1 confirmación" in op.text_content("#mt-pend"), op.text_content("#mt-pend")[:120])
    ok("mis novedades: listada", "(e2e)" in op.text_content("#mt-mias"))
    ok("novedades: solo de equipos (sin opción de obra)", op.locator("#mt-seg").count() == 0)
    ok("novedades: los dos ítems quedaron pendientes", "(e2e2)" in op.text_content("#mt-pend"))
    op.fill("#mt-items [data-desc]", "Sigue perdiendo aceite la transmisión")
    op.wait_for_selector("#mt-items .mt-aviso")
    captura(op, "01b_app_parecida")
    ok("novedades: avisa que se parece a una pendiente", "(e2e)" in op.text_content("#mt-items .mt-aviso") and not op.is_disabled("#mt-enviar"))
    op.click("#mt-items [data-misma]")
    op.wait_for_timeout(800)
    ok("novedades: «Es la misma» confirma la existente y quita el ítem",
       op.locator("#mt-items [data-desc]").count() == 1 and op.input_value("#mt-items [data-desc]") == "")
    op.evaluate(PEGAR_JS, ["#mt-items [data-desc]", "Pulmon de freno roto,\nFalta calefacción,\nSoldaduras con fisuras"])
    ok("novedades: pegar renglones arma un ítem por renglón", op.locator("#mt-items [data-desc]").count() == 3)
    op.evaluate(PEGAR_JS, ["#mt-items [data-desc]", MAIL_SUPERVISOR])
    ok("novedades: texto con varios equipos no se pega en la app", op.locator("#mt-items [data-desc]").count() == 3)

    # ---------------------------------------------------------------- web: aprobador
    ctx = nav.new_context(viewport={"width": 1500, "height": 950}, locale="es-AR")
    r = ctx.request.post(f"{URL}/login", form={"username": "contingencia_test", "password": "clave-contingencia-test"}, max_redirects=0)
    assert r.headers.get("location") == "/", "login fallido"
    w = ctx.new_page()
    vigilar(w, "web")
    w.on("dialog", lambda d: d.accept("Motivo e2e"))
    w.goto(f"{URL}/mantenimiento")
    w.wait_for_selector("#vista .page-head")
    w.wait_for_function("document.querySelectorAll('#sidebar [data-panel]:not([hidden])').length > 0")
    tabs = w.eval_on_selector_all("#sidebar [data-panel]:not([hidden])", "els => els.map(e => e.textContent.trim())")
    ok("web: menú lateral", tabs == ["Panel", "Novedades", "Órdenes de trabajo", "Check list", "Equipos", "Preventivo", "Configuración"], str(tabs))
    w.wait_for_selector(".ind-kpis .ind-kpi")
    ok("web: abre en el panel", w.evaluate("location.hash") == "#panel", w.evaluate("location.hash"))
    captura(w, "01_web_panel")
    w.click('#sidebar [data-panel="novedades"]')
    w.wait_for_selector("#tabla table")
    captura(w, "02_web_novedades")
    fila = w.locator("#tabla tbody tr", has_text="(e2e)").first
    ok("web: novedad urgente en la lista", fila.count() == 1 and "Urgente" in fila.text_content())
    fila.click()
    w.wait_for_selector("#r-ok")
    w.fill("[data-buscar-mec]", "PRUEBA MECANICO")
    w.check(f'[data-mecs] input[value="{t.MEC}"]')
    w.select_option("#r-est", "FUNCIONAL")
    captura(w, "03_web_resolver")
    w.click("#r-ok")
    w.wait_for_selector(".drawer h2:has-text('OT-')")
    captura(w, "04_web_ot")
    numero = w.text_content(".drawer h2").split()[0]
    ok("web: la novedad pasó a OT y abre la OT", numero.startswith("OT-"), numero)
    w.click("[data-cerrar]")

    # Reporte del supervisor: se pega el mail y arma un bloque por equipo con un ítem por renglón
    w.click("#btn-nueva-nov")
    w.wait_for_selector("#r-bloques [data-desc]")
    w.evaluate(PEGAR_JS, ["#r-bloques [data-desc]", MAIL_SUPERVISOR])
    w.wait_for_timeout(1500)
    equipos = w.eval_on_selector_all("#r-bloques [data-ci]", "els => els.map(e => e.value)")
    ok("web: el mail arma un equipo por encabezado", equipos == ["M-24", "M-26", "C-28", "C-16", "M-40", "M-34", "C-24"], str(equipos))
    ok("web: un ítem por renglón (renglones cortados unidos, saludo descartado)", w.text_content("#r-cuenta") == "33 ítem(s) en 7 equipo(s)", w.text_content("#r-cuenta"))
    primero = w.input_value("#r-bloques [data-b='0'] [data-desc]")
    ok("web: renglón cortado unido", primero.endswith("bomba inyectora."), primero[-40:])
    w.fill("#r-bloques [data-b='2'] [data-i='1'] [data-desc]", "Cubiertas con desgaste notorio (e2e3)")
    w.locator("#r-bloques [data-b='2'] [data-i='1'] [data-urg]").check()
    captura(w, "03b_web_reporte_pegado")
    w.click("#r-enviar")
    w.wait_for_selector("#drawer-root:empty", state="attached")
    w.wait_for_selector("#tabla tbody tr:has-text('(e2e3)')")
    fila = w.locator("#tabla tbody tr", has_text="(e2e3)").first
    ok("web: reporte cargado (urgente y con número de reporte)", "Urgente" in fila.text_content() and "Reporte #" in fila.text_content())
    fila.click()
    w.wait_for_selector(".drawer h2:has-text('Novedad')")
    ok("web: la novedad muestra el resto del reporte", "Mismo reporte" in w.text_content(".drawer") and w.locator(".drawer [data-nov]").count() == 32)
    captura(w, "03c_web_novedad_de_reporte")
    w.click("[data-cerrar]")

    # ---------------------------------------------------------------- mecánico
    mec = app(nav, t.MEC)
    mec.wait_for_selector('.tabbar [data-tab="mto"]:not([hidden])')
    ok("app: mecánico ve la pestaña Mantenimiento", True)
    mec.click('.tabbar [data-tab="mto"]')
    mec.wait_for_selector("#mto-lista [data-ot]")
    ok("mecánico: su OT en la lista", numero in mec.text_content("#mto-lista"))
    mec.click("#mto-lista [data-ot]")
    mec.wait_for_selector("#mto-crono")
    mec.click("#mto-crono")
    mec.wait_for_selector("#mto-crono:has-text('Pausar')")
    ok("mecánico: cronómetro iniciado", True)
    ok("mecánico: ve la foto de la novedad", mec.locator(".sheet img").count() == 1)
    mec.set_input_files("#mto-foto", IMAGEN)
    mec.wait_for_function("document.querySelectorAll('.sheet img').length === 2")
    ok("mecánico: agregó una foto a la OT", True)
    mec.fill("#mto-txt", "Cambio de retén de la transmisión")
    mec.click('[data-acc="avance"]')
    mec.wait_for_timeout(700)
    mec.fill("#mto-txt", "Probado y sin pérdidas")
    mec.click('[data-acc="final"]')
    mec.wait_for_timeout(900)
    captura(mec, "05_app_mecanico")
    ok("mecánico: OT en revisión, sin botones", mec.locator("#mto-crono").count() == 0 and "revisión" in mec.text_content(".sheet"), mec.text_content(".sheet h3"))

    # ---------------------------------------------------------------- web: administrador
    w.click('#sidebar [data-panel="ordenes"]')
    w.wait_for_selector("#tabla table")
    w.locator("#tabla tbody tr", has_text=numero).first.click()
    w.wait_for_selector("#ot-fin")
    ok("web: historial con avance y corrección final", "Corrección final" in w.text_content(".drawer .mt-tl") and "retén" in w.text_content(".drawer .mt-tl"))
    w.wait_for_function("[...document.querySelectorAll('.drawer .fotos img')].filter(i => i.complete && i.naturalWidth > 0).length === 2")
    ok("web: fotos de la novedad y de la OT visibles", True)
    ok("web: control contra la Tarja", "Control contra la Tarja" in w.text_content(".drawer") and "Sin el día en la Tarja" in w.text_content(".drawer"))
    captura(w, "05b_web_ot_revision")
    w.click("#ot-fin")
    w.wait_for_selector(".drawer h2:has-text('Finalizada')")
    captura(w, "06_web_finalizada")
    ok("web: OT finalizada", True)
    mec.keyboard.press("Escape")
    mec.click('.tabbar [data-tab="nuevo"]')
    mec.click('.tabbar [data-tab="mto"]')
    mec.wait_for_selector(f"#mto-hist [data-ot]:has-text('{numero}')")
    mec.click(f"#mto-hist [data-ot]:has-text('{numero}')")
    mec.wait_for_selector(".sheet :text('Finalizada')")
    ok("mecánico: la OT finalizada queda en su historial (solo lectura)", mec.locator("#mto-crono").count() == 0 and "Mis horas" in mec.text_content(".sheet"))
    captura(mec, "05c_app_historial")
    mec.click(".sheet [data-cancel]")
    w.click("[data-cerrar]")
    w.click('#sidebar [data-panel="equipos"]')
    w.wait_for_selector("#tabla table")
    filas = w.locator("#tabla tbody tr").count()
    ok("web: equipos listados", filas > 50, str(filas))
    w.click('[data-f="eq"][data-v="FUERA_DE_SERVICIO"]')
    w.click('[data-f="eq"][data-v=""]')
    w.locator(f'#tabla tr[data-ci="{CI}"]').click()
    w.wait_for_selector("#e-est-ok")
    ok("web: ficha del equipo con historial de estado", "Funcional" in w.text_content(".drawer") and "Operativo" in w.text_content(".drawer"))
    w.select_option("#e-est", "FUERA_DE_SERVICIO")
    w.fill("#e-mot", "Prueba e2e")
    w.click("#e-est-ok")
    w.wait_for_selector(".drawer h2 .chip-bad")
    proyectos = w.eval_on_selector_all("#e-proy option", "els => els.length")
    ok("web: ubicación manual con proyecto → CC", proyectos >= 1, f"{proyectos} opciones")
    captura(w, "07_web_equipo")
    w.click("[data-cerrar]")
    w.click('#sidebar [data-panel="configuracion"]')
    w.fill("#c-vh", "2500")
    w.click("#c-ok")
    w.wait_for_timeout(500)
    ok("web: configuración guardada", w.is_visible("#toasts") and "guardada" in w.text_content("#toasts"))
    w.click('#sidebar [data-panel="ordenes"]')
    w.click("#btn-nueva-ot")
    w.wait_for_selector("#o-ci")
    ok("web: nueva OT solo con equipo (sin centro de costo)", w.locator("#o-cc").count() == 0)
    captura(w, "05b_web_nueva_ot")
    w.click("[data-cerrar]")

    # ---------------------------------------------------------------- check list (etapa 2)
    # Tipos de equipo: se estandarizan en Logística › Vehículos
    v = ctx.new_page()
    vigilar(v, "vehiculos")
    v.on("dialog", lambda d: d.accept())
    v.goto(f"{URL}/vehiculos_form")
    v.wait_for_selector("#lista-body tr[data-codigo]")
    v.wait_for_timeout(500)
    v.select_option("#page-size", "100")  # la tabla pagina de a 25
    pendientes = v.locator("#lista-body .tipo-pendiente").count()
    ok("vehículos: tipos sin estandarizar marcados en la tabla", pendientes > 50, str(pendientes))
    v.click("#btn-mas")
    v.click("#menu-mas [data-act=estandarizar]")
    v.wait_for_selector("#tipos-estandarizar")
    captura(v, "09_vehiculos_tipos")
    v.click("#tipos-estandarizar")
    v.click("#confirm-ok")
    v.wait_for_function("!document.querySelector('#tipos-estandarizar') && document.querySelectorAll('#tipos-cuerpo tbody tr').length === 1")
    v.wait_for_timeout(800)
    ok("vehículos: estandarizados (queda uno sin propuesta)", v.locator("#lista-body .tipo-pendiente").count() == 1)
    v.click("#modal-tipos [data-vista=catalogo]")
    v.wait_for_selector("#tipos-cuerpo .tipo-fila")
    ok("vehículos: catálogo de tipos", v.locator("#tipos-cuerpo .tipo-fila").count() == 19)
    v.click("#mt-cerrar")
    v.click("#btn-nuevo")
    ok("vehículos: alta con lista de tipos", v.locator("#tipo option").count() == 20)
    captura(v, "10_vehiculos_catalogo")
    v.close()
    w.click('#sidebar [data-panel="configuracion"]')
    w.click('[data-f="cfg"][data-v="plantilla"]')
    w.wait_for_selector("#cfg-vista details")
    ok("config: plantilla con 27 módulos", w.locator("#cfg-vista details").count() == 27)
    ok("config: sin pantallas de tipos (pasaron a Vehículos)", w.locator('[data-f="cfg"]').count() == 2)

    w.click('#sidebar [data-panel="checklist"]')
    w.wait_for_selector("#btn-nuevo-ck")
    w.click("#btn-nuevo-ck")
    w.select_option("#ck-ci", CI)
    w.wait_for_selector("#ck-enviar")
    filas = w.locator(".ck-row").count()
    ok("check list: armado para la excavadora", filas > 60 and "Tren rodante" in w.text_content("#ck-form") and "Neumáticos" not in w.text_content("#ck-form"), f"{filas} ítems")
    w.evaluate("document.querySelectorAll('.ck-row').forEach(r => r.querySelector('.ck-vals .chip').click())")
    fila_malo = w.locator(".ck-row", has_text="Pérdida hidráulica").first
    fila_malo.locator('.chip[data-v="MALO"]').click()
    fila_malo.locator("[data-com]").fill("Pierde aceite el cilindro del aguilón (e2e)")
    fila_malo.locator("[data-foto-in]").set_input_files(IMAGEN)
    for texto in ("Nivel de aceite", "Nivel de refrigerante"):
        for i in range(w.locator(".ck-row[data-foto='1']", has_text=texto).count()):
            w.locator(".ck-row[data-foto='1']", has_text=texto).nth(i).locator("[data-foto-in]").set_input_files(IMAGEN)
    ok("check list: contador completo", w.text_content("#ck-cuenta").startswith(f"{filas} de {filas}"), w.text_content("#ck-cuenta"))
    captura(w, "11_web_checklist_form")
    w.click("#ck-enviar")
    w.wait_for_selector(".drawer h2:has-text('Check list')")
    captura(w, "12_web_checklist_no_apto")
    ok("check list: guardado No apto", "No apto" in w.text_content(".drawer h2"))
    ok("check list: el Malo pasó a Novedades", w.locator(".drawer [data-nov]").count() == 1)
    w.click(".drawer [data-nov]")
    w.wait_for_selector(".drawer h2:has-text('Novedad')")
    ok("check list: novedad urgente con foto", "Urgente" in w.text_content(".drawer") and w.locator(".drawer .fotos img").count() == 1)
    w.click("[data-cerrar]")

    # Preventivo: plan, asignación con el último service, vencido, OT preventiva con repuestos y cierre
    w.click('#sidebar [data-panel="preventivo"]')
    w.wait_for_selector("#tabla .empty")
    w.click('[data-f="prev"][data-v="planes"]')
    w.wait_for_selector("#btn-nuevo-plan")
    w.click("#btn-nuevo-plan")
    w.fill("#p-nom", "Programado simple (e2e)")
    w.fill("#p-hs", "250")
    w.fill("#p-tareas", "Cambio de aceite de motor\nFiltro de aceite\nFiltro de aire")
    w.click("#p-ok")
    w.wait_for_selector("[data-buscar-eq]")
    w.fill("[data-buscar-eq]", "motoniveladora")
    filtrados = w.locator("[data-eqs] label:visible").count()
    ok("preventivo: filtro de equipos por tipo", 0 < filtrados < 20, str(filtrados))
    w.fill("[data-buscar-eq]", "C-28")
    w.click("[data-todos]")
    w.check('input[name=base][value=MANUAL]')
    w.fill("#a-fecha", "2026-07-01")
    w.fill("#a-hs", "3000")
    captura(w, "13_web_preventivo_asignar")
    w.click("#a-ok")
    w.wait_for_selector("#tabla tr[data-id]")
    fila = w.locator("#tabla tr[data-id]", has_text="C-28").first
    ok("preventivo: C-28 vencido por horas", "Vencido" in fila.text_content() and "pasado" in fila.text_content(), fila.text_content()[:160])
    captura(w, "14_web_preventivo_vencimientos")
    fila.click()
    w.wait_for_selector("#pv-ot")
    w.fill("[data-buscar-mec]", "PRUEBA MECANICO")
    w.check(f'[data-mecs] input[value="{t.MEC}"]')
    w.click("#pv-ot")
    w.wait_for_selector(".drawer h2:has-text('Preventiva')")
    ok("preventivo: OT con las tareas del plan", "Filtro de aire" in w.text_content(".drawer"))
    w.locator('.drawer [data-tarea="1"]').check()
    w.wait_for_selector(".drawer :text('1 de 3 hechas')")
    ok("OT: tarea tildada desde la web", True)
    w.click("#rep-form summary")
    w.check('input[name=rep-or][value=OTRO]')
    w.fill("#rep-desc", "Filtro de aceite (e2e)")
    w.fill("#rep-cant", "2")
    w.fill("#rep-costo", "15000")
    w.click("#rep-ok")
    w.wait_for_selector(".drawer td:has-text('Filtro de aceite (e2e)')")
    ok("OT: repuesto cargado y sumado al costo", "total" in w.text_content(".drawer") and "30.000" in w.text_content(".drawer"))
    captura(w, "15_web_ot_preventiva_repuestos")
    w.click("#ot-fin-dir")
    w.wait_for_selector(".drawer h2:has-text('Finalizada')")
    w.click("[data-cerrar]")
    w.click('#sidebar [data-panel="preventivo"]')
    w.click('[data-f="prev"][data-v="estado"]')
    w.wait_for_selector("#tabla tr[data-id]")
    ok("preventivo: después del service queda al día", "Al día" in w.locator("#tabla tr[data-id]", has_text="C-28").first.text_content())
    w.click('[data-f="prev"][data-v="lecturas"]')
    w.wait_for_selector("#tabla .empty, #tabla table")
    ok("preventivo: vista de lecturas a revisar", w.locator("#tabla").count() == 1)
    w.click('#sidebar [data-panel="panel"]')
    w.wait_for_selector(".ind-kpis")
    w.click('[data-f="ind"][data-v="365"]')
    w.wait_for_selector(".ind-barras .ind-mes >> nth=12")
    ok("indicadores: tarjetas, costo por mes y tablas", w.locator(".ind-kpi").count() == 8 and w.locator(".ind-card").count() == 6)
    ok("indicadores: costo de la OT preventiva con repuestos", "repuestos ARS 30.000" in w.text_content(".ind-kpis"), w.text_content(".ind-kpis")[:300])
    captura(w, "16_web_indicadores")

    # Fuera de servicio no bloquea: el operador ve el aviso y sigue
    op.click('.tabbar [data-tab="nuevo"]')
    op.click('[data-picker="ci"]')
    op.fill("#pk-q", "C-0")
    op.click('#pk-list .opt >> nth=0')
    op.click('[data-picker="ci"]')
    op.fill("#pk-q", CI)
    op.click(f'#pk-list .opt[data-v="{CI}"]')
    op.wait_for_selector("#mt-aviso-eq.err:not([hidden])")
    captura(op, "08_app_fuera_de_servicio")
    ok("parte: aviso Fuera de servicio sin bloquear", "cargar el parte igual" in op.text_content("#mt-aviso-eq"))
    nav.close()

print("\n".join(RESULTADOS))
print(f"\nErrores de JS: {len(ERRORES)}")
for e in ERRORES:
    print("  ", e)
t.MantenimientoTests.tearDownClass()
sys.exit(1 if ERRORES or any(r.startswith("FALLA") for r in RESULTADOS) else 0)
