"""Prueba de punta a punta de Operaciones › Tarja General (Edge real vía Playwright, base copiada).

Uso: .venv-1\\Scripts\\python.exe tests/e2e_tarja.py <carpeta_capturas>
Reutiliza la preparación de tests/test_tarja.py (copia de la base y servidor). Recorre: menú, grilla, carga escribiendo,
arrastre de un rango, autofiltro por columna, pegado, panel del día, configuración y exportación. Informa errores de JS.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.dirname(AQUI))
CAPTURAS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(AQUI), "capturas"))
os.makedirs(CAPTURAS, exist_ok=True)

import test_tarja as t  # noqa: E402  (copia la base y levanta el servidor)
from playwright.sync_api import sync_playwright  # noqa: E402

URL = f"http://127.0.0.1:{t.PUERTO}"
RESULTADOS, ERRORES, RESPUESTAS = [], [], []
t.TarjaTests.setUpClass()


def ok(nombre, condicion, detalle=""):
    RESULTADOS.append(f"{'OK   ' if condicion else 'FALLA'} {nombre}{f' · {detalle}' if detalle else ''}")


def captura(pagina, nombre):
    pagina.screenshot(path=os.path.join(CAPTURAS, f"tj_{nombre}.png"), full_page=False)


def celda(pagina, legajo, dia):
    fila = pagina.locator("#tj-scroll tbody tr", has=pagina.locator("td.fx1", has_text=legajo)).first
    return fila.locator("td.d").nth(dia - 1)


with sync_playwright() as p:
    navegador = p.chromium.launch(channel="msedge", headless=True)
    contexto = navegador.new_context(viewport={"width": 1600, "height": 950}, locale="es-AR", accept_downloads=True)
    contexto.grant_permissions(["clipboard-read", "clipboard-write"], origin=URL)
    pagina = contexto.new_page()
    pagina.on("pageerror", lambda e: ERRORES.append(str(e)))
    pagina.on("console", lambda m: ERRORES.append(f"console: {m.text}") if m.type == "error" and "404" not in m.text else (RESPUESTAS.append(f"404 consola: {m.location}") if m.type == "error" else None))
    pagina.on("requestfailed", lambda q: RESPUESTAS.append(f"falló {q.url}"))
    pagina.on("response", lambda r: RESPUESTAS.append(f"{r.status} {r.request.method} {r.url.replace(URL, '')}") if r.status >= 400 else None)
    r = contexto.request.post(f"{URL}/login", form={"username": "contingencia_test", "password": "clave-contingencia-test"}, max_redirects=0)
    assert r.headers.get("location") == "/", "login fallido"
    pagina.goto(f"{URL}/operaciones")
    pagina.wait_for_selector("#btn-sidebar-tarja:not(.hidden)")

    ids = pagina.eval_on_selector_all(".sidebar .menu > .menu-btn", "els => els.map(e => e.id || e.textContent.trim())")
    ok("menú: Tarja General justo debajo de Dashboard", ids[:2] == ["btn-sidebar-dashboard", "btn-sidebar-tarja"], str(ids[:3]))
    ok("menú: tooltip Registro Diario", "Registro Diario" in (pagina.get_attribute("#btn-sidebar-tarja", "title") or ""))

    pagina.click("#btn-sidebar-tarja")
    pagina.wait_for_selector("#tj-scroll table.tj-t")
    pagina.wait_for_timeout(300)
    captura(pagina, "01_grilla")
    cuenta = pagina.text_content("#tj-cuenta")
    ok("grilla: indicador Mostrando X de Y", cuenta.startswith("Mostrando") and " de " in cuenta, cuenta)
    ok("grilla: tema claro activo", pagina.evaluate("document.body.classList.contains('tema-claro')"))
    ok("grilla: hoy resaltado", pagina.locator("#tj-scroll th.dia.hoy").count() == 1)
    ok("grilla: columna Obra con P en rojo para pendientes", pagina.locator("#tj-scroll .tj-pend").count() > 0)
    # Columnas inmovilizadas
    pagina.eval_on_selector("#tj-scroll", "el => { el.scrollLeft = 600; el.scrollTop = 200; }")
    pagina.wait_for_timeout(150)
    x_leg = pagina.eval_on_selector("#tj-scroll tbody tr:nth-child(10) td.fx1", "el => el.getBoundingClientRect().left - el.closest('.tj-scroll').getBoundingClientRect().left")
    y_cab = pagina.eval_on_selector("#tj-scroll thead th.dia", "el => el.getBoundingClientRect().top - el.closest('.tj-scroll').getBoundingClientRect().top")
    ok("inmovilizar paneles: legajo y encabezado fijos", abs(x_leg) < 2 and abs(y_cab) < 2, f"x={x_leg} y={y_cab}")
    captura(pagina, "02_scroll")
    pagina.eval_on_selector("#tj-scroll", "el => { el.scrollLeft = 0; el.scrollTop = 0; }")

    # Ir a octubre 2026 y buscar a los operadores de prueba
    pagina.select_option("#tj-mes", "10")
    pagina.wait_for_timeout(500)
    pagina.fill("#tj-f-q", "PRUEBA")
    pagina.wait_for_timeout(500)
    ok("búsqueda rápida por nombre", pagina.locator("#tj-scroll tbody tr").count() == 2, pagina.text_content("#tj-cuenta"))

    # Escribir un código en una celda
    celda(pagina, t.ROT, 1).click()
    pagina.keyboard.type("10")
    pagina.wait_for_selector(".tj-ed .tj-sug div")
    sug = pagina.locator(".tj-ed .tj-sug div").first.text_content()
    ok("autocompletar: sugiere centros de costo", sug.startswith("10"), sug)
    pagina.keyboard.type("6")
    pagina.keyboard.press("Enter")
    pagina.wait_for_timeout(600)
    ok("carga: 106 escrito en la celda", celda(pagina, t.ROT, 1).text_content() == "106")
    # Rango por arrastre: días 2..5 → 209
    a = celda(pagina, t.ROT, 2).bounding_box()
    b = celda(pagina, t.ROT, 5).bounding_box()
    pagina.mouse.move(a["x"] + 5, a["y"] + 5)
    pagina.mouse.down()
    pagina.mouse.move(b["x"] + 5, b["y"] + 5, steps=5)
    pagina.mouse.up()
    ok("selección por arrastre", pagina.locator("#tj-scroll td.d.sel").count() == 4)
    pagina.keyboard.type("209")
    pagina.keyboard.press("Enter")
    pagina.wait_for_timeout(600)
    ok("carga de rango", [celda(pagina, t.ROT, d).text_content() for d in range(2, 6)] == ["209"] * 4)
    celda(pagina, t.ROT, 6).click()
    pagina.keyboard.type("d")
    pagina.keyboard.press("Enter")
    pagina.wait_for_timeout(600)
    tip = celda(pagina, t.ROT, 5).get_attribute("title") or ""
    ok("tooltip: descripción y horas (bajada 5+1)", "MANTENIMIENTO DE CAMINO SSR" in tip and "5 h efectivas + 1 h break" in tip, tip.replace("\n", " | "))
    ok("cambio de turno marcado", "ct" in (celda(pagina, t.ROT, 1).get_attribute("class") or ""))
    # Código inválido
    celda(pagina, t.ROT, 7).click()
    pagina.keyboard.type("107")
    pagina.keyboard.press("Escape")
    pagina.keyboard.type("Q")
    pagina.keyboard.press("Enter")
    pagina.wait_for_timeout(500)
    ok("validación: código inexistente rechazado", celda(pagina, t.ROT, 7).text_content() == "")
    # Copiar / pegar: copia días 1..3 de ROT y pega en DIA día 10
    celda(pagina, t.ROT, 1).click()
    celda(pagina, t.ROT, 3).click(modifiers=["Shift"])
    pagina.keyboard.press("Control+C")
    celda(pagina, t.DIA, 10).click()
    pagina.keyboard.press("Control+V")
    pagina.wait_for_timeout(700)
    ok("copiar / pegar", [celda(pagina, t.DIA, d).text_content() for d in (10, 11, 12)] == ["106", "209", "209"])
    captura(pagina, "03_carga")

    # Autofiltro en la columna del día 1: solo 106
    pagina.fill("#tj-f-q", "")
    pagina.wait_for_timeout(400)
    total = pagina.locator("#tj-scroll tbody tr").count()
    pagina.click("#tj-scroll th.dia:nth-of-type(4) [data-tj-f]")
    pagina.wait_for_selector("#tj-menu")
    captura(pagina, "04_autofiltro")
    ok("autofiltro: lista de valores con descripción", pagina.locator("#tj-menu .vals label small").count() > 0)
    pagina.uncheck("#tj-m-todo")
    pagina.fill("#tj-m-q", "106")
    pagina.wait_for_timeout(100)
    pagina.click("#tj-m-ok")
    pagina.wait_for_timeout(400)
    filtradas = pagina.locator("#tj-scroll tbody tr").count()
    dias1 = pagina.eval_on_selector_all("#tj-scroll tbody tr td.d:nth-child(4)", "els => [...new Set(els.map(e => e.textContent))]")
    ok("autofiltro por valor", 0 < filtradas < total and dias1 == ["106"], f"{filtradas}/{total} {dias1}")
    ok("autofiltro: ícono activo", pagina.locator("#tj-scroll th.dia:nth-of-type(4) .tj-fbtn.on").count() == 1)
    # Filtro de texto en Nombre (condición Y)
    pagina.click("#tj-scroll th.fx2 [data-tj-f]")
    pagina.wait_for_selector("#tj-menu")
    pagina.select_option("#tj-m-op", "contains")
    pagina.fill("#tj-m-a", "PRUEBA")
    pagina.click("#tj-m-ok")
    pagina.wait_for_timeout(400)
    ok("filtros combinados (Y)", pagina.locator("#tj-scroll tbody tr").count() == 1, pagina.text_content("#tj-cuenta"))
    # Orden Z→A en Legajo y 10 mejores en horas
    pagina.click("#tj-borrar-todo")
    pagina.wait_for_timeout(400)
    ok("borrar todos los filtros", pagina.locator("#tj-scroll tbody tr").count() == total)
    ok("totales ocultos por defecto", pagina.locator("#tj-scroll th.tot").count() == 0 and pagina.text_content("#tj-tot") == "Mostrar totales")
    pagina.click("#tj-tot")
    pagina.wait_for_timeout(200)
    ok("Mostrar totales: aparecen las 7 columnas", pagina.locator("#tj-scroll th.tot").count() == 7)
    pagina.click("#tj-scroll th.tot:nth-last-of-type(2) [data-tj-f]")
    pagina.wait_for_selector("#tj-menu")
    pagina.select_option("#tj-m-op", "top")
    pagina.click("#tj-m-ok")
    pagina.wait_for_timeout(400)
    n_top = pagina.locator("#tj-scroll tbody tr").count()
    ok("filtro de número: 10 mejores", 10 <= n_top < total, str(n_top))
    pagina.click("#tj-tot")
    pagina.wait_for_timeout(200)
    ok("Ocultar totales: el filtro sigue y el botón lo avisa", pagina.locator("#tj-scroll th.tot").count() == 0
       and pagina.locator("#tj-scroll tbody tr").count() == n_top and "(con filtro)" in pagina.text_content("#tj-tot"))
    pagina.wait_for_timeout(900)
    ok("preferencia mostrar totales guardada", t._ok(t.TarjaTests.c.get(f"{t.API}/preferencias")).get("mostrarTotales") is False)
    pagina.click("#tj-scroll th.fx1 [data-tj-f]")
    pagina.wait_for_selector("#tj-menu")
    pagina.click("#tj-menu [data-ord=desc]")
    pagina.wait_for_timeout(300)
    legs = pagina.eval_on_selector_all("#tj-scroll tbody td.fx1", "els => els.map(e => e.textContent)")
    nums = [int(x) for x in legs if x.isdigit()]
    ok("orden mayor a menor", nums == sorted(nums, reverse=True), str(legs[:5]))
    # Filtro por color: cambio de turno en el día 1
    pagina.click("#tj-borrar-todo")
    pagina.wait_for_timeout(300)
    pagina.click("#tj-scroll th.dia:nth-of-type(4) [data-tj-f]")
    pagina.wait_for_selector("#tj-menu")
    pagina.check("#tj-menu [data-col=cambio]")
    pagina.click("#tj-m-ok")
    pagina.wait_for_timeout(300)
    clases = pagina.eval_on_selector_all("#tj-scroll tbody tr td.d:nth-child(4)", "els => els.map(e => e.className.includes('ct'))")
    ok("filtro por color (cambio de turno)", clases and all(clases), str(len(clases)))
    # Los filtros se mantienen al cambiar de mes y se guardan por usuario
    pagina.click("#tj-next")
    pagina.wait_for_timeout(600)
    ok("filtro se mantiene al cambiar de mes", pagina.locator("#tj-scroll th.dia:nth-of-type(4) .tj-fbtn.on").count() == 1)
    pagina.wait_for_timeout(900)
    prefs = t._ok(t.TarjaTests.c.get(f"{t.API}/preferencias"))
    ok("preferencias guardadas por usuario", "d1" in (prefs.get("filtros") or {}), str(prefs)[:120])
    pagina.click("#tj-prev")
    pagina.wait_for_timeout(500)
    pagina.click("#tj-borrar-todo")
    pagina.wait_for_timeout(300)

    # Panel del día: editar horas a mano
    pagina.fill("#tj-f-q", "PRUEBA ROT")
    pagina.wait_for_timeout(400)
    celda(pagina, t.ROT, 3).dblclick()
    pagina.wait_for_selector("#tj-d-h")
    captura(pagina, "05_dia")
    pagina.fill("#tj-d-h", "7")
    pagina.fill("#tj-d-o", "Salió temprano")
    pagina.click("#tj-d-ok")
    pagina.wait_for_timeout(600)
    cl = celda(pagina, t.ROT, 3).get_attribute("class") or ""
    ok("horas a mano: marca editado_manual", "man" in cl and "7 h efectivas" in (celda(pagina, t.ROT, 3).get_attribute("title") or ""))

    # Configuración
    pagina.click("#tj-conf")
    pagina.wait_for_selector("#tj-c-1")
    ok("configuración: jornada 10 / 2 / 5 / 1", [pagina.input_value(f"#tj-c-{i}") for i in range(1, 5)] == ["10", "2", "5", "1"])
    pagina.click("[data-tj-tab=codigos]")
    pagina.wait_for_selector(".tj-k-cod")
    captura(pagina, "06_codigos")
    ok("configuración: códigos editables", pagina.locator(".tj-k-cod").count() >= 15)
    pagina.click("[data-tj-tab=feriados]")
    pagina.fill("#tj-f-fecha", "2026-10-12")
    pagina.fill("#tj-f-desc", "Diversidad Cultural")
    pagina.click("#tj-f-add")
    pagina.wait_for_timeout(800)
    pagina.click("#tj-modal [data-tj-cerrar]")
    ok("feriado resaltado en el encabezado", pagina.locator("#tj-scroll th.dia.fer").count() == 1)

    # Operador: régimen
    pagina.click(f"#tj-scroll td[data-tj-op='{t.ROT}']")
    pagina.wait_for_selector("#tj-o-reg")
    ok("operador: régimen editable", pagina.input_value("#tj-o-reg") == "Rotativo")
    pagina.click("#tj-modal [data-tj-cerrar]")

    # Período desde / hasta (20/10 al 20/12), ▶ corre un mes y queda guardado para el usuario
    pagina.fill("#tj-f-q", "")
    pagina.fill("#tj-desde", "2026-10-20")
    pagina.fill("#tj-hasta", "2026-12-20")
    pagina.click("#tj-aplicar")
    pagina.wait_for_timeout(900)
    meses = pagina.eval_on_selector_all("#tj-scroll th.dia .tj-mes", "els => els.map(e => e.textContent)")
    ok("período 20/10 al 20/12: 62 columnas", pagina.locator("#tj-scroll th.dia").count() == 62, str(pagina.locator("#tj-scroll th.dia").count()))
    ok("período: mes marcado en el primer día y en cada día 1", meses == ["oct", "nov", "dic"], str(meses))
    ok("período: resumen con el rango", "20/10/2026 al 20/12/2026" in pagina.text_content(".tj-resumen h3"))
    captura(pagina, "09_periodo")
    pagina.click("#tj-next")
    pagina.wait_for_timeout(900)
    ok("▶ corre el período un mes", (pagina.input_value("#tj-desde"), pagina.input_value("#tj-hasta")) == ("2026-11-20", "2027-01-20"))
    pagina.click("#tj-prev")
    pagina.wait_for_timeout(1500)
    pagina.reload()
    pagina.wait_for_selector("#btn-sidebar-tarja:not(.hidden)")
    pagina.click("#btn-sidebar-tarja")
    pagina.wait_for_selector("#tj-scroll table.tj-t")
    pagina.wait_for_timeout(500)
    ok("el período queda establecido al volver a entrar", (pagina.input_value("#tj-desde"), pagina.input_value("#tj-hasta")) == ("2026-10-20", "2026-12-20"))
    pagina.fill("#tj-hasta", "2026-01-01")
    pagina.click("#tj-aplicar")
    pagina.wait_for_timeout(300)
    ok("Hasta anterior a Desde: no se aplica", pagina.locator("#tj-scroll th.dia").count() == 62)
    pagina.fill("#tj-f-q", "PRUEBA ROT")
    pagina.wait_for_timeout(500)

    # Exportar
    with pagina.expect_download() as d:
        pagina.click("#tj-exp-f")
    ruta = os.path.join(CAPTURAS, "tarja_filtrado.xlsx")
    d.value.save_as(ruta)
    from openpyxl import load_workbook
    ws = load_workbook(ruta)["Tarja"]
    ok("exportar filtrado del período", ws.cell(6, 1).value == t.ROT and ws.cell(7, 1).value is None and "20/10/2026 al 20/12/2026" in ws["A1"].value
       and "2026-10-20_a_2026-12-20" in d.value.suggested_filename, d.value.suggested_filename)
    pagina.fill("#tj-f-q", "")
    pagina.wait_for_timeout(400)
    captura(pagina, "07_final")
    pagina.evaluate("document.querySelector('.tj-resumen').scrollIntoView()")
    captura(pagina, "08_resumen_cc")
    navegador.close()

print("\n".join(RESULTADOS))
print(f"\n{sum(r.startswith('OK') for r in RESULTADOS)}/{len(RESULTADOS)} OK")
print("Errores JS:", ERRORES or "ninguno")
print("Respuestas con error:", RESPUESTAS or "ninguna")
