"""Prueba de punta a punta de Operaciones › Actas de Medición (Edge real vía Playwright, base copiada).

Uso: .venv-1\\Scripts\\python.exe tests/e2e_actas_medicion.py <carpeta_capturas>
Reutiliza la preparación de tests/test_actas_medicion.py (copia de la base + partes de la Foja 23 solo en la copia),
congela el acta 23 y recorre: actas, reportes, partes de una fila, contrato, ítem, plantilla, campos y la precarga
del certificado desde Seguimiento de Cliente. Informa errores de JavaScript y respuestas con error.
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
acta = t._ok(c.post(f"{t.API}/contratos/{T.cid}/actas", json={"numero": 23, "periodo_desde": t.DESDE, "periodo_hasta": t.HASTA, "fecha": "2026-10-27"}))
t._ok(c.post(f"{t.API}/actas/{acta['id']}/estado", json={"destino": "EN_REVISION"}))
t._ok(c.post(f"{t.API}/actas/{acta['id']}/estado", json={"destino": "APROBADA"}))


def ok(nombre, condicion, detalle=""):
    RESULTADOS.append(f"{'OK   ' if condicion else 'FALLA'} {nombre}{f' · {detalle}' if detalle else ''}")


def captura(pagina, nombre, completa=True):
    pagina.screenshot(path=os.path.join(CAPTURAS, f"am_{nombre}.png"), full_page=completa)


with sync_playwright() as p:
    navegador = p.chromium.launch(channel="msedge", headless=True)
    contexto = navegador.new_context(viewport={"width": 1500, "height": 1000}, locale="es-AR")
    pagina = contexto.new_page()
    pagina.on("pageerror", lambda e: ERRORES.append(str(e)))
    pagina.on("console", lambda m: ERRORES.append(f"console: {m.text}") if m.type == "error" else None)
    pagina.on("response", lambda r: RESPUESTAS.append(f"{r.status} {r.request.method} {r.url.replace(URL, '')}") if r.status >= 400 else None)
    r = contexto.request.post(f"{URL}/login", form={"username": "contingencia_test", "password": "clave-contingencia-test"}, max_redirects=0)
    assert r.headers.get("location") == "/", "login fallido"
    pagina.goto(f"{URL}/operaciones")
    pagina.wait_for_selector("#btn-sidebar-actas-medicion:not(.hidden)")
    ok("botón Actas de Medición en la barra", pagina.locator("#btn-sidebar-actas-medicion").is_visible())

    # ---- Actas
    pagina.click("#btn-sidebar-actas-medicion")
    pagina.wait_for_selector("#am-sel-contrato")
    pagina.select_option("#am-sel-contrato", str(T.cid))
    pagina.wait_for_selector("[data-am-abrir]")
    captura(pagina, "01_lista_actas", completa=False)
    pagina.click(f"[data-am-abrir='{acta['id']}']")
    pagina.wait_for_selector("#am-reporte table")
    texto = pagina.locator("#am-reporte").inner_text()
    ok("reporte Certificado muestra I.1 con 267", "Motoniveladora 180 HP" in texto and "267" in texto and "5.931" in texto, texto[:200].replace("\n", " "))
    captura(pagina, "02_acta_certificado")
    pagina.click("[data-am-rep='resumen']")
    texto = pagina.locator("#am-reporte").inner_text()
    ok("reporte Resumen con gasoil 17.895", "17.895" in texto, "")
    captura(pagina, "03_acta_resumen")
    pagina.click("[data-am-rep='detalle']")
    pagina.wait_for_selector("#am-reporte .cert-seccion")
    ok("detalle con una sección por unidad", pagina.locator("#am-reporte .cert-seccion").count() == 11, str(pagina.locator("#am-reporte .cert-seccion").count()))
    captura(pagina, "04_acta_detalle", completa=False)
    pagina.click("[data-am-rep='certificado']")
    pagina.locator("#am-reporte tbody tr[data-am-ref]").first.click()
    pagina.wait_for_selector("#am-modal.open table")
    ok("clic en una fila abre sus partes", pagina.locator("#am-modal tbody tr").count() == 30, str(pagina.locator("#am-modal tbody tr").count()))
    captura(pagina, "05_partes_de_fila", completa=False)
    pagina.click("#am-modal [data-am-cerrar] >> nth=0")
    ok("acta congelada sin botón Recalcular", pagina.locator("#am-recalc").count() == 0)

    # ---- Contratos
    pagina.click("[data-am-tab='contratos']")
    pagina.click(f"[data-am-c='{T.cid}']")
    pagina.wait_for_selector("[data-am-item]")
    captura(pagina, "06_contrato")
    pagina.click(f"[data-am-item='{T.items['I.1']['id']}']")
    pagina.wait_for_selector("#am-modal.open [data-b='it:config.regla.tipo']")
    pagina.select_option("#am-modal [data-b='it:config.regla.tipo']", "TRAMOS")
    pagina.wait_for_selector("#am-modal [data-arr='it:config.regla.tramos']")
    ok("regla por tramos muestra la tabla de tramos", True)
    vinc = pagina.locator("#am-modal [data-b='it:oc.minimas']").input_value()
    ok("ítem I.1 vinculado al ítem de OC de mínimas", vinc == str(T.oc_min), vinc)
    captura(pagina, "07_item", completa=False)
    pagina.click("#am-modal [data-am-cerrar] >> nth=0")
    pagina.click("#am-fac-nuevo")
    pagina.wait_for_selector("#am-modal.open [data-b='fa:codigo']")
    captura(pagina, "08_factor", completa=False)
    pagina.click("#am-modal [data-am-cerrar] >> nth=0")

    # ---- Plantillas y campos
    pagina.click("[data-am-tab='plantillas']")
    pagina.click(f"[data-am-p='{T.plantilla_id}']")
    pagina.wait_for_selector("#am-p-ed")
    pagina.click("#am-p-ed details:nth-of-type(11) summary")
    pagina.wait_for_selector("#am-p-rep")
    ok("editor de reportes con columnas", pagina.locator("[data-arr='p:cfg.reportes.0.columnas']").count() > 0)
    captura(pagina, "09_plantilla_reportes")
    pagina.click("[data-am-tab='campos']")
    pagina.wait_for_selector("#am-cp-e0")
    ok("campos del parte listados", pagina.locator("[id^='am-cp-e']").count() >= 37, str(pagina.locator("[id^='am-cp-e']").count()))
    captura(pagina, "10_campos", completa=False)

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
    pagina.wait_for_selector(f"#am-modal.open [data-am-pc='{acta['id']}']")
    captura(pagina, "11_precarga_lista", completa=False)
    pagina.click(f"[data-am-pc='{acta['id']}']")
    pagina.wait_for_function("certState.ed && certState.ed.amActaId")
    carga = pagina.evaluate("certState.ed.carga")
    ok("precarga completa los ítems de la OC", carga.get(f"i{T.oc_min}", {}).get("valor") == "230" and carga.get(f"i{T.oc_ext}", {}).get("valor") == "37", str(carga))
    pagina.wait_for_timeout(1200)
    captura(pagina, "12_certificado_precargado")
    pagina.click("#btn-cert-ed-guardar")
    pagina.wait_for_function("!certState.ed")
    vinculo = t._ok(c.get(f"/operaciones/seguimiento-cliente/1/actas-medicion?oc={T.oc_id}"))
    fila = next(a for a in vinculo if a["acta_id"] == acta["id"])
    ok("al guardar el borrador queda vinculado al acta", fila["certificado_vigente"] is not None, str(fila["certificado_vigente"]))
    navegador.close()

print("\n".join(RESULTADOS))
print("Errores JS:", ERRORES or "ninguno")
print("Respuestas con error:", RESPUESTAS or "ninguna")
print("Capturas en", CAPTURAS)
assert os.path.getmtime(t.DB_REAL) == t.MTIME_REAL, "¡La base real fue modificada!"
