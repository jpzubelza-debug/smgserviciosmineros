"""Prueba de punta a punta de Operaciones › Actas de Medición (Edge real vía Playwright, base copiada).

Uso: .venv-1\\Scripts\\python.exe tests/e2e_actas_medicion.py <carpeta_capturas>
Reutiliza la preparación de tests/test_actas_medicion.py (copia de la base + partes de la Foja 23 solo en la copia) y
recorre: nueva acta, equipos con partes, ítems de la OC, columnas, resultado en vivo, guardar, cerrar y la precarga del
certificado desde Seguimiento de Cliente. Informa errores de JavaScript y respuestas con error.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(AQUI))
CAPTURAS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(AQUI), "capturas"))
os.makedirs(CAPTURAS, exist_ok=True)

import test_actas_medicion as t  # noqa: E402  (copia la base y levanta el servidor)
from playwright.sync_api import sync_playwright  # noqa: E402

URL = f"http://127.0.0.1:{t.PUERTO}"
RESULTADOS, ERRORES, RESPUESTAS = [], [], []

T = t.ActasMedicionTests
T.setUpClass()
c = T.c


def ok(nombre, condicion, detalle=""):
    RESULTADOS.append(f"{'OK   ' if condicion else 'FALLA'} {nombre}{f' · {detalle}' if detalle else ''}")


def captura(pagina, nombre, completa=True):
    pagina.screenshot(path=os.path.join(CAPTURAS, f"am_{nombre}.png"), full_page=completa)


def esperar_calculo(pagina):
    pagina.wait_for_timeout(500)
    pagina.wait_for_function("!document.getElementById('am-res') || document.getElementById('am-res').style.opacity === ''")


with sync_playwright() as p:
    navegador = p.chromium.launch(channel="msedge", headless=True)
    contexto = navegador.new_context(viewport={"width": 1500, "height": 1000}, locale="es-AR")
    pagina = contexto.new_page()
    pagina.on("pageerror", lambda e: ERRORES.append(str(e)))
    pagina.on("console", lambda m: ERRORES.append(f"console: {m.text}") if m.type == "error" else None)
    def _respuesta(r):
        if r.status >= 400:
            try:
                cuerpo = r.text()[:300]
            except Exception:  # noqa: BLE001
                cuerpo = ""
            RESPUESTAS.append(f"{r.status} {r.request.method} {r.url.replace(URL, '')} {cuerpo}")
    pagina.on("response", _respuesta)
    pagina.on("dialog", lambda d: d.accept())
    r = contexto.request.post(f"{URL}/login", form={"username": "contingencia_test", "password": "clave-contingencia-test"}, max_redirects=0)
    assert r.headers.get("location") == "/", "login fallido"
    pagina.goto(f"{URL}/operaciones")
    pagina.wait_for_selector("#btn-sidebar-actas-medicion:not(.hidden)")

    # ---- Lista y nueva acta
    pagina.click("#btn-sidebar-actas-medicion")
    pagina.wait_for_selector("#am-nueva")
    captura(pagina, "01_lista_vacia", completa=False)
    pagina.click("#am-nueva")
    pagina.select_option("#am-seg", "1")
    pagina.select_option("#am-oc", str(T.oc_id))
    pagina.wait_for_selector("#am-equipos")
    ok("período sugerido", pagina.input_value("#am-desde") != "" and pagina.input_value("#am-num") == "1",
       f"{pagina.input_value('#am-num')} {pagina.input_value('#am-desde')} {pagina.input_value('#am-hasta')}")
    pagina.fill("#am-num", "23")
    pagina.fill("#am-desde", t.DESDE)
    pagina.dispatch_event("#am-desde", "change")
    pagina.fill("#am-hasta", t.HASTA)
    pagina.dispatch_event("#am-hasta", "change")
    pagina.wait_for_selector("#am-eq-partes")

    # ---- Equipos con partes en el período
    pagina.click("#am-eq-partes")
    pagina.wait_for_selector("#am-modal.open [data-am-ci='M-25']")
    pagina.fill("#am-eq-buscar", "c-")
    captura(pagina, "02_equipos_con_partes", completa=False)
    pagina.fill("#am-eq-buscar", "")
    for ci in ("M-25", "C-28", "C-16"):
        pagina.check(f"#am-modal [data-am-ci='{ci}']")
    pagina.click("#am-eq-ok")
    pagina.wait_for_selector("[data-am-eq-item='2']")
    pagina.fill("#am-eq-ci", "V-32")
    pagina.click("#am-eq-add")
    pagina.wait_for_selector("[data-am-eq-item='3']")
    filas = pagina.locator("#am-equipos tbody tr b").all_inner_texts()
    ok("equipos agregados (modal + CI escrito)", sorted(f for f in filas if "-" in f) == ["C-16", "C-28", "M-25", "V-32"], str(filas))
    cis = [x for x in filas if "-" in x]
    for ci, item in (("M-25", T.it_moto), ("C-28", T.it_regador), ("C-16", T.it_regador), ("V-32", T.it_camioneta)):
        pagina.select_option(f"[data-am-eq-item='{cis.index(ci)}']", str(item))
        esperar_calculo(pagina)
    pagina.check("[data-am-col='gasoil']")
    esperar_calculo(pagina)
    texto = pagina.locator("#am-res").inner_text()
    ok("resultado en vivo por ítem de la OC", "267" in texto and "899" in texto, "")
    ok("indicadores por equipo (combustible y consumo)", "Indicadores por equipo" in texto and "2.361" in texto and "8,84" in texto, "")
    captura(pagina, "03_acta_borrador")

    # ---- Mínimo y objetivo por ítem + gráficos KPI
    pagina.fill(f"[data-am-mt='{T.it_moto}'][data-k='minimo']", "300")
    pagina.dispatch_event(f"[data-am-mt='{T.it_moto}'][data-k='minimo']", "change")
    pagina.fill(f"[data-am-mt='{T.it_moto}'][data-k='objetivo']", "400")
    pagina.dispatch_event(f"[data-am-mt='{T.it_moto}'][data-k='objetivo']", "change")
    pagina.fill(f"[data-am-mt='{T.it_regador}'][data-k='objetivo']", "800")
    pagina.dispatch_event(f"[data-am-mt='{T.it_regador}'][data-k='objetivo']", "change")
    esperar_calculo(pagina)
    metas = pagina.locator("#am-metas").inner_text()
    ok("estado de cumplimiento por ítem", "Bajo el mínimo" in metas and "Alcanzó el objetivo" in metas, metas[:200])
    ok("gráficos KPI por defecto", pagina.locator("#am-kpi svg").count() >= 3 and "Avance sobre el objetivo" in pagina.locator("#am-kpi").inner_text())
    captura(pagina, "03b_metas_y_kpi")
    pagina.click("#am-g-acomodar")
    pagina.select_option("#am-g-tipo", "avance_item")
    pagina.click("#am-g-add")
    pagina.wait_for_selector("[data-am-g-item='4']")
    pagina.select_option("[data-am-g-item='4']", str(T.it_moto))
    pagina.click("[data-am-g-mov='4'][data-d='-1']")
    pagina.click("[data-am-g-del='0']")
    titulos = pagina.locator("#am-kpi h4").all_inner_texts()
    ok("acomodar: agregar, mover y quitar", titulos[0].startswith("Cantidad por ítem") and any("Avance acumulado" in x for x in titulos) and len(titulos) == 4, str(titulos))
    captura(pagina, "03c_acomodar")
    pagina.click("#am-g-listo")

    # ---- Guardar y cerrar
    pagina.click("#am-guardar")
    pagina.wait_for_selector("#am-cerrar")
    ok("acta creada en borrador", "BORRADOR" in pagina.locator("#am-body").inner_text())
    pagina.click("#am-cerrar")
    pagina.wait_for_selector("#am-reabrir")
    ok("acta cerrada sin campos editables", pagina.locator("#am-num").is_disabled() and pagina.locator("[data-am-eq-item]").first.is_disabled())
    captura(pagina, "04_acta_cerrada")
    ok("gráficos y metas guardados con el acta", "Avance acumulado" in pagina.locator("#am-kpi").inner_text() and pagina.input_value(f"[data-am-mt='{T.it_moto}'][data-k='minimo']") == "300")
    ok("en el acta cerrada se puede acomodar", pagina.locator("#am-g-acomodar").count() == 1)
    pagina.click("#am-volver")
    pagina.wait_for_selector("[data-am-abrir]")
    captura(pagina, "05_lista", completa=False)
    acta_id = int(pagina.locator("tr", has_text="TEST-470-069").locator("[data-am-abrir]").get_attribute("data-am-abrir"))

    # ---- Precarga del certificado
    pagina.click("#btn-sidebar-seguimiento-cliente")
    pagina.wait_for_function("[...document.querySelectorAll('#seg-registro option')].some(o => o.value === '1')")
    pagina.select_option("#seg-registro", "1")
    pagina.wait_for_function("segSeguimientoId() !== null && !segState.ocupado")
    pagina.click("#btn-certificados-cliente")
    pagina.wait_for_function("certState.rj && !certState.ocupado")
    pagina.click("#btn-cert-nuevo")
    pagina.wait_for_selector("#cert-m-editor.open #btn-cert-ed-acta")
    pagina.click("#btn-cert-ed-acta")
    pagina.wait_for_selector(f"#am-modal.open [data-am-pc='{acta_id}']")
    captura(pagina, "06_precarga_lista", completa=False)
    pagina.click(f"[data-am-pc='{acta_id}']")
    pagina.wait_for_function("certState.ed && certState.ed.amActaId")
    carga = pagina.evaluate("certState.ed.carga")
    ok("precarga completa los ítems de la OC", carga.get(f"i{T.it_moto}", {}).get("valor") == "267" and carga.get(f"i{T.it_regador}", {}).get("valor") == "899", str(carga))
    pagina.wait_for_timeout(1200)
    captura(pagina, "07_certificado_precargado", completa=False)
    pagina.click("#btn-cert-ed-guardar")
    try:
        pagina.wait_for_function("!certState.ed", timeout=8000)
    except Exception:  # noqa: BLE001
        print("NO GUARDÓ:", RESPUESTAS, pagina.locator(".toast, #toast-container").all_inner_texts())
        raise
    vinculo = t._ok(c.get(f"/operaciones/seguimiento-cliente/1/actas-medicion?oc={T.oc_id}"))
    fila = next(a for a in vinculo if a["acta_id"] == acta_id)
    ok("al guardar el certificado queda vinculado al acta", fila["certificado_vigente"] is not None, str(fila["certificado_vigente"]))
    reabrir = c.post(f"{t.API}/actas/{acta_id}/reabrir")
    ok("con certificado vigente no se reabre", reabrir.status_code == 409, reabrir.text[:120])
    navegador.close()

print("\n".join(RESULTADOS))
print("Errores JS:", ERRORES or "ninguno")
print("Respuestas con error:", RESPUESTAS or "ninguna")
print("Capturas en", CAPTURAS)
assert os.path.getmtime(t.DB_REAL) == t.MTIME_REAL, "¡La base real fue modificada!"
