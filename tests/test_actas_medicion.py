"""Pruebas de Operaciones › Actas de Medición (motor configurable de fojas de medición).

Se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal (DATA_DIR se fija antes de importar main).
Los partes de la Foja N° 23 (21/09/2026 al 20/10/2026) se cargan SOLO en esa copia, desde
tests/fixtures/foja23_partes.json (extraídos de las hojas de detalle de la planilla Excel).

    .venv-1\\Scripts\\python.exe -m unittest tests.test_actas_medicion -v
"""
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

if "main" in sys.modules:
    raise unittest.SkipTest("main ya fue importado con otra DATA_DIR; ejecute este módulo por separado.")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_REAL = os.path.join(BASE_DIR, "dashboard.db")
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_actas_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")
MTIME_REAL = os.path.getmtime(DB_REAL)
_o = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
_d = sqlite3.connect(DB_COPIA)
_o.backup(_d)
_d.close()
_o.close()
os.environ["DATA_DIR"] = TMP_DIR
os.environ["APP_LOGIN_USER"] = "contingencia_test"
os.environ["APP_LOGIN_PASSWORD"] = "clave-contingencia-test"

import main  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB_COPIA), "Las pruebas no apuntan a la copia"
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

API = "/api/actas-medicion"
DESDE, HASTA = "2026-09-21", "2026-10-20"
# Hoja de detalle de la planilla -> (ítem, CI del equipo en el sistema, turno, unidad)
EQUIPOS = {"I.1": ("I.1", "M-25", None, 1), "I.2": ("I.2", "C-28", "DIA", 1), "I.3": ("I.3", "M-24", None, 1), "I.4": ("I.4", "M-34", None, 1),
           "I.5": ("I.5", "C-34", None, 1), "I.6": ("I.6", "M-33", None, 1), "I.7a": ("I.7", "V-32", None, 1), "I.7b": ("I.7", "V-27", None, 2),
           "I.8": ("I.8", "C-22", None, 1), "I.9": ("I.9", "C-16", "DIA", 1), "I.10": ("I.10", "C-28", "NOCHE", 1)}
SALDOS = {"I.1": 5664, "I.2": 5989, "I.3": 5050, "I.4": 6134, "I.5": 6161, "I.6": 5450, "I.7": 12760, "I.8": 4885, "I.9": 6384,
          "I.10": 5257, "II.1": 6000, "II.2": 6000, "II.3": 12000}
# Foja N° 23: registrado, mínimas, extras, litros
FOJA23 = {"I.1": (267, 230, 37, 2361), "I.2": (300, 230, 70, 1804), "I.3": (248, 230, 18, 1414), "I.4": (294, 230, 64, 3037),
          "I.5": (295, 230, 65, 2802), "I.6": (259, 230, 29, 2995), "I.7": (580, 460, 120, 722), "I.8": (244, 230, 14, 997),
          "I.9": (299, 230, 69, 1763), "I.10": (300, 230, 70, 0), "II.1": (300, 300, 0, 0), "II.2": (300, 300, 0, 0), "II.3": (600, 600, 0, 0)}


def _db():
    conn = sqlite3.connect(DB_COPIA)
    conn.row_factory = sqlite3.Row
    return conn


def _cliente():
    c = httpx.Client(base_url=f"http://127.0.0.1:{PUERTO}", follow_redirects=False, timeout=60)
    r = c.post("/login", data={"username": "contingencia_test", "password": "clave-contingencia-test"})
    assert r.status_code == 302, r.text
    return c


def _ok(r, codigo=200):
    assert r.status_code == codigo, f"{r.status_code}: {r.text[:800]}"
    return r.json()


class ActasMedicionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = _cliente()
        plantillas = _ok(cls.c.get(f"{API}/plantillas"))
        cls.plantilla_id = next(p["id"] for p in plantillas if p["nombre"] == "Foja SMG 470-069")
        # OC del seguimiento 1 con ítems "mínimas" y "extras" de la motoniveladora (precios del Cert. N° 22)
        with _db() as conn:
            cls.oc_id = conn.execute("INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, moneda, monto_oc) VALUES (1, 90, 'TEST-470-069', 'USD', 1691250)").lastrowid
            cls.oc_min = conn.execute("INSERT INTO items_oc_cliente (orden_compra_cliente_id, numero, descripcion, unidad, cantidad, moneda, precio, total) VALUES (?, 1, 'Motoniveladora hs mínimas', 'hs', 5000, 'USD', 178.03, 890150)", (cls.oc_id,)).lastrowid
            cls.oc_ext = conn.execute("INSERT INTO items_oc_cliente (orden_compra_cliente_id, numero, descripcion, unidad, cantidad, moneda, precio, total) VALUES (?, 2, 'Motoniveladora hs extras', 'hs', 5000, 'USD', 160.22, 801100)", (cls.oc_id,)).lastrowid
            for p in json.load(open(os.path.join(BASE_DIR, "tests", "fixtures", "foja23_partes.json"), encoding="utf-8")):
                _, ci, turno, _ = EQUIPOS[p["hoja"]]
                conn.execute(
                    "INSERT INTO horas_maquinas (ci, fecha_parte, nro_parte, maquinista, descripcion_trabajos, turno, hs_inicio, hs_final, total_hs, gasoil, cliente) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '000070 - MINERA EXAR S.A.')",
                    (ci, p["fecha"], f"T{p['nro_parte']}", p["maquinista"], p["descripcion"], turno or "DIA", p["hs_inicio"], p["hs_final"], p["total_hs"], p["gasoil"]))
        contrato = _ok(cls.c.post(f"{API}/contratos", json={
            "nombre": "470-069 Mantenimiento de caminos EXAR (prueba)", "plantilla_id": cls.plantilla_id, "numero_contrato": "470-069",
            "proyecto": "MANTENIMIENTO DE CAMINOS Y DISTRIBUCIÓN DE COMBUSTIBLE", "comitente": "MINERA EXAR S.A.", "seguimiento_cc_id": 1,
            "orden_compra_cliente_id": cls.oc_id, "saldo_hasta_numero": 22, "fecha_inicio": "2025-01-01"}))
        cls.cid = contrato["id"]
        cls.items = {i["codigo"]: i for i in contrato["items"]}
        for codigo, saldo in SALDOS.items():
            it = cls.items[codigo]
            datos = {k: it[k] for k in ("seccion_id", "codigo", "descripcion", "unidad", "unidades", "cantidad_mensual", "cantidad_contratada", "orden")}
            datos.update({"saldo_inicial": saldo, "config": it["config"], "activo": True})
            if codigo == "I.1":
                datos["oc"] = {"minimas": cls.oc_min, "extras": cls.oc_ext}
            _ok(cls.c.put(f"{API}/contratos/{cls.cid}/items/{it['id']}", json=datos))
        cls.asig = {}
        for hoja, (codigo, ci, turno, unidad) in EQUIPOS.items():
            r = _ok(cls.c.post(f"{API}/contratos/{cls.cid}/asignaciones", json={
                "item_id": cls.items[codigo]["id"], "recurso_codigo": ci, "turno": turno, "unidad": unidad, "desde": "2025-01-01"}))
            cls.asig[hoja] = r["id"]

    @classmethod
    def tearDownClass(cls):
        assert os.path.getmtime(DB_REAL) == MTIME_REAL, "¡La base real fue modificada!"

    def _simular(self, **extra):
        return _ok(self.c.post(f"{API}/contratos/{self.cid}/simular", json={"periodo_desde": DESDE, "periodo_hasta": HASTA, "numero": 23, **extra}))

    def _item(self, res, codigo):
        return next(i for i in res["items"] if i["codigo"] == codigo)

    def _put_item(self, codigo, **cambios):
        it = _ok(self.c.get(f"{API}/contratos/{self.cid}"))
        it = next(i for i in it["items"] if i["codigo"] == codigo)
        datos = {k: it[k] for k in ("seccion_id", "codigo", "descripcion", "unidad", "unidades", "cantidad_mensual", "cantidad_contratada", "orden", "saldo_inicial")}
        datos.update({"config": it["config"], "activo": True})
        datos.update(cambios)
        return _ok(self.c.put(f"{API}/contratos/{self.cid}/items/{it['id']}", json=datos))

    # ---------------------------------------------------------------- Criterio 1: Foja 23
    def test_01_reproduce_foja_23(self):
        res = self._simular()
        for codigo, (reg, mn, ex, lit) in FOJA23.items():
            it = self._item(res, codigo)
            self.assertEqual((it["salidas"]["registrado"], it["salidas"]["minimas"], it["salidas"]["extras"], it["medidas"]["litros"]),
                             (reg, mn, ex, lit), codigo)
            self.assertEqual(it["salidas"]["certificar"], reg, codigo)
            self.assertEqual(it["anterior"], SALDOS[codigo], codigo)
            self.assertEqual(it["acumulado"], SALDOS[codigo] + reg, codigo)
        self.assertEqual(self._item(res, "I.1")["contratado"], 3220)
        self.assertEqual(self._item(res, "I.7")["contratado"], 8400)
        self.assertEqual(self._item(res, "II.3")["contratado"], 8400)
        self.assertAlmostEqual(self._item(res, "I.1")["acumulado_pct"], 5931 / 3220, places=5)
        self.assertAlmostEqual(self._item(res, "I.1")["indicadores"]["consumo"], 8.84, places=2)
        gasoil = next(c for c in res["conceptos"] if c["codigo"] == "GASOIL")
        self.assertEqual(gasoil["base"], 17895)
        # Reporte Resumen: 11 filas (I.7 en dos unidades) + personal; detalle: una sección por unidad con partes
        acta = _ok(self.c.post(f"{API}/contratos/{self.cid}/actas", json={"numero": 23, "periodo_desde": DESDE, "periodo_hasta": HASTA, "fecha": "2026-10-27"}))
        type(self).acta23 = acta["id"]
        reps = {r["codigo"]: r for r in _ok(self.c.get(f"{API}/actas/{acta['id']}/reportes"))}
        self.assertEqual(set(reps), {"certificado", "resumen", "detalle", "avance"})
        cert = reps["certificado"]
        fila = next(f for s in cert["secciones"] for f in s["filas"] if f["celdas"][0] == "I.7")
        self.assertEqual(fila["celdas"][-2:], [460, 120])
        detalle = reps["detalle"]
        self.assertEqual(len(detalle["secciones"]), 11)
        tot_moto = next(s for s in detalle["secciones"] if s["titulo"].startswith("I.1 "))["totales"]
        self.assertIn(267, tot_moto)
        self.assertIn(2361, tot_moto)
        self.assertEqual(detalle["firmas"][1]["empresa"], "MINERA EXAR S.A.")
        # Desde cualquier número se llega a los partes
        partes = _ok(self.c.post(f"{API}/actas/{acta['id']}/partes", json={"ids": fila_partes(cert, "I.1")}))
        self.assertEqual(len(partes), 30)
        self.assertEqual(sum(p["cantidad"] for p in partes), 267)
        # Excel
        r = self.c.get(f"{API}/actas/{acta['id']}/excel")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"PK"))

    def test_02_validaciones_reales(self):
        res = self._simular()
        codigos = {v["codigo"]: v for v in res["validaciones"]}
        self.assertIn("DUPLICADO_EQUIPO_FECHA_TURNO", codigos)  # C-22 tiene dos partes el 28/09 en la planilla
        self.assertIn("NRO_PARTE_REPETIDO", codigos)  # el parte 76374 figura en volquete y excavadora
        self.assertTrue(all(v["severidad"] == "AVISO" for v in res["validaciones"]))

    # ---------------------------------------------------------------- Criterio 2 (sin tocar código)
    def test_03_tope_a_minimo_garantizado(self):
        it = self.items["I.1"]
        self._put_item("I.1", config={**it["config"], "regla": {"tipo": "MINIMO_GARANTIZADO", "minimo": 300, "ambito": "UNIDAD"}})
        i1 = self._item(self._simular(), "I.1")
        self.assertEqual((i1["salidas"]["minimas"], i1["salidas"]["extras"], i1["salidas"]["certificar"]), (300, 0, 300))
        self._put_item("I.1", config=it["config"])
        self.assertEqual(self._item(self._simular(), "I.1")["salidas"]["certificar"], 267)

    def test_04_dia_de_corte_y_minimo(self):
        contrato = _ok(self.c.get(f"{API}/contratos/{self.cid}"))
        datos = {k: contrato[k] for k in ("nombre", "plantilla_id", "numero_contrato", "proyecto", "comitente", "seguimiento_cc_id",
                                          "orden_compra_cliente_id", "saldo_hasta_numero", "fecha_inicio", "duracion_meses")}
        _ok(self.c.put(f"{API}/contratos/{self.cid}", json={**datos, "config": {"periodo": {"dia_corte": 15}}}))
        sug = _ok(self.c.get(f"{API}/contratos/{self.cid}/sugerencia"))
        self.assertTrue(sug["periodo_hasta"].endswith("-15"), sug)
        _ok(self.c.put(f"{API}/contratos/{self.cid}", json={**datos, "config": {}}))
        it = self.items["I.3"]
        self._put_item("I.3", config={**it["config"], "regla": {**it["config"]["regla"], "minimo": 200}})
        i3 = self._item(self._simular(), "I.3")
        self.assertEqual((i3["salidas"]["minimas"], i3["salidas"]["extras"]), (200, 48))
        self._put_item("I.3", config=it["config"])

    def test_05_item_por_viajes_y_m3_con_factor(self):
        with _db() as conn:
            for i in range(10):
                conn.execute("INSERT INTO horas_maquinas (ci, fecha_parte, nro_parte, turno, hs_inicio, hs_final, total_hs, viajes, km_inicio, km_final, gasoil) "
                             "VALUES ('CA-03', ?, ?, 'DIA', '0', '0', 10, 4, ?, ?, 0)", (f"2026-10-{i + 1:02d}", f"VJ{i}", 1000 + i * 50, 1000 + i * 50 + 40))
        sid = self.items["I.1"]["seccion_id"]
        nuevo = _ok(self.c.post(f"{API}/contratos/{self.cid}/items", json={
            "seccion_id": sid, "codigo": "III.1", "descripcion": "Transporte de material (m³)", "unidad": "m³", "unidades": 1, "orden": 99999,
            "config": {"medida": "m3", "modo": "PARTE", "regla": {"tipo": "SIN_MINIMO", "ambito": "ITEM"}}}))
        _ok(self.c.post(f"{API}/contratos/{self.cid}/asignaciones", json={"item_id": nuevo["id"], "recurso_codigo": "CA-03", "desde": "2026-01-01"}))
        _ok(self.c.post(f"{API}/contratos/{self.cid}/factores", json={"recurso_codigo": "CA-03", "codigo": "carga_m3", "valor": 17, "desde": "2026-01-01"}))
        it = self._item(self._simular(), "III.1")
        self.assertEqual(it["salidas"]["certificar"], 10 * 4 * 17)
        self.assertEqual(it["medidas"]["viajes"], 40)
        self.assertEqual(it["medidas"]["km"], 400)
        # Cambio del índice de carga desde una fecha: vigencias
        fac = _ok(self.c.get(f"{API}/contratos/{self.cid}"))["factores"][0]
        _ok(self.c.put(f"{API}/contratos/{self.cid}/factores/{fac['id']}", json={**fac, "hasta": "2026-10-05"}))
        _ok(self.c.post(f"{API}/contratos/{self.cid}/factores", json={"recurso_codigo": "CA-03", "codigo": "carga_m3", "valor": 20, "desde": "2026-10-06"}))
        it = self._item(self._simular(), "III.1")
        self.assertEqual(it["salidas"]["certificar"], 5 * 4 * 17 + 5 * 4 * 20)
        # el mismo ítem medido por km en vez de m³
        self._put_item("III.1", config={"medida": "km", "modo": "PARTE", "regla": {"tipo": "SIN_MINIMO", "ambito": "ITEM"}})
        self.assertEqual(self._item(self._simular(), "III.1")["salidas"]["certificar"], 400)
        _ok(self.c.delete(f"{API}/contratos/{self.cid}/items/{nuevo['id']}"))

    def test_06_columnas_y_concepto_nuevo_en_plantilla(self):
        p = _ok(self.c.get(f"{API}/plantillas/{self.plantilla_id}"))
        original = json.loads(json.dumps(p["config"]))
        cfg = p["config"]
        det = next(r for r in cfg["reportes"] if r["codigo"] == "detalle")
        det["columnas"] = [c for c in det["columnas"] if c["valor"] != "maquinista"] + [{"valor": "novedades", "titulo": "Novedades"}, {"valor": "km_total", "titulo": "Km total"}]
        cfg["conceptos"].append({"codigo": "MULTA", "titulo": "Multa por hs extras", "base": "extras", "unidad": "hs", "signo": -1, "precio": 10, "activo": True})
        _ok(self.c.put(f"{API}/plantillas/{self.plantilla_id}", json={"nombre": p["nombre"], "descripcion": p["descripcion"], "config": cfg}))
        acta = self.acta23
        reps = {r["codigo"]: r for r in _ok(self.c.get(f"{API}/actas/{acta}/reportes"))}
        titulos = [c["titulo"] for c in reps["detalle"]["columnas"]]
        self.assertNotIn("Maquinista", titulos)
        self.assertIn("Novedades", titulos)
        self.assertIn("Km total", titulos)
        res = _ok(self.c.get(f"{API}/actas/{acta}"))["resultado"]
        multa = next(c for c in res["conceptos"] if c["codigo"] == "MULTA")
        self.assertEqual(multa["base"], sum(v[2] for v in FOJA23.values()))
        self.assertEqual(multa["importe"], -10 * multa["base"])
        _ok(self.c.put(f"{API}/plantillas/{self.plantilla_id}", json={"nombre": p["nombre"], "descripcion": p["descripcion"], "config": original}))

    def test_07_mover_equipo_de_item_desde_fecha(self):
        r = _ok(self.c.post(f"{API}/contratos/{self.cid}/asignaciones/{self.asig['I.3']}/mover", json={"item_id": self.items["I.6"]["id"], "fecha": "2026-10-06"}))
        res = self._simular()
        i3, i6 = self._item(res, "I.3"), self._item(res, "I.6")
        self.assertLess(i3["salidas"]["registrado"], 248)
        self.assertEqual(i3["salidas"]["registrado"] + i6["salidas"]["registrado"], 248 + 259)
        # deshacer
        _ok(self.c.delete(f"{API}/contratos/{self.cid}/asignaciones/{r['id']}"))
        with _db() as conn:
            conn.execute("UPDATE am_asignaciones SET hasta = NULL WHERE id = ?", (self.asig["I.3"],))
        self.assertEqual(self._item(self._simular(), "I.3")["salidas"]["registrado"], 248)

    def test_08_plantilla_distinta_para_otro_contrato(self):
        p = _ok(self.c.post(f"{API}/plantillas/{self.plantilla_id}/copiar", json={"nombre": "Alquiler por km quincenal (prueba)"}))
        cfg = p["config"]
        cfg["periodo"] = {"dia_corte": 0, "duracion": "QUINCENAL"}
        cfg["item_defecto"] = {"medida": "km", "modo": "PARTE", "regla": {"tipo": "MINIMO_GARANTIZADO", "minimo": 1000, "ambito": "EQUIPO"}, "facturar": ["certificar"]}
        cfg["estructura"] = [{"codigo": "A", "titulo": "A - Alquiler", "items": [{"codigo": "A.1", "descripcion": "Camión por km", "unidad": "km", "unidades": 1, "config": {}}]}]
        cfg["reportes"] = [{"codigo": "km", "titulo": "Km por equipo", "activo": True, "nivel": "EQUIPO", "una_tabla": True,
                            "columnas": [{"valor": "equipo", "titulo": "Equipo"}, {"valor": "med_km", "titulo": "Km", "formato": "entero", "total": True},
                                         {"valor": "certificar", "titulo": "A certificar", "formato": "entero", "total": True}]}]
        _ok(self.c.put(f"{API}/plantillas/{p['id']}", json={"nombre": p["nombre"], "config": cfg}))
        c2 = _ok(self.c.post(f"{API}/contratos", json={"nombre": "Otro contrato por km", "plantilla_id": p["id"], "duracion_meses": 6}))
        a1 = c2["items"][0]
        _ok(self.c.post(f"{API}/contratos/{c2['id']}/asignaciones", json={"item_id": a1["id"], "recurso_codigo": "CA-03", "desde": "2026-01-01"}))
        res = _ok(self.c.post(f"{API}/contratos/{c2['id']}/simular", json={"periodo_desde": "2026-10-01", "periodo_hasta": "2026-10-15"}))
        it = res["items"][0]
        self.assertEqual(it["salidas"]["registrado"], 400)
        self.assertEqual(it["salidas"]["certificar"], 1000)  # mínimo garantizado
        acta = _ok(self.c.post(f"{API}/contratos/{c2['id']}/actas", json={"periodo_desde": "2026-10-01", "periodo_hasta": "2026-10-15"}))
        self.assertEqual(acta["numero"], 1)
        rep = _ok(self.c.get(f"{API}/actas/{acta['id']}/reportes"))[0]
        self.assertEqual(rep["titulo"], "Km por equipo")
        self.assertEqual(rep["secciones"][0]["filas"][0]["celdas"][0], "CA-03")
        sug = _ok(self.c.get(f"{API}/contratos/{c2['id']}/sugerencia"))
        self.assertEqual(sug["periodo_desde"], "2026-10-16")
        self.assertEqual(sug["periodo_hasta"], "2026-10-30")

    # ---------------------------------------------------------------- Criterio 3: acta emitida congelada
    def test_09_acta_congelada_no_cambia(self):
        acta = self.acta23
        _ok(self.c.post(f"{API}/actas/{acta}/estado", json={"destino": "EN_REVISION"}))
        a = _ok(self.c.post(f"{API}/actas/{acta}/estado", json={"destino": "APROBADA"}))
        self.assertEqual(a["congelada"], 1)
        antes = a["resultado"]
        # cambia la configuración y los partes
        it = self.items["I.1"]
        self._put_item("I.1", config={**it["config"], "regla": {"tipo": "MINIMO_GARANTIZADO", "minimo": 400, "ambito": "UNIDAD"}})
        with _db() as conn:
            conn.execute("UPDATE horas_maquinas SET hs_final = CAST(hs_final AS REAL) + 50 WHERE ci = 'M-25' AND nro_parte = 'T72457'")
        despues = _ok(self.c.get(f"{API}/actas/{acta}"))["resultado"]
        self.assertEqual(json.dumps(antes["items"], sort_keys=True), json.dumps(despues["items"], sort_keys=True))
        self.assertEqual(self._item(despues, "I.1")["salidas"]["certificar"], 267)
        # la base impide modificarla directamente
        with _db() as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE am_actas SET resultado_json = '{}' WHERE id = ?", (acta,))
        r = self.c.put(f"{API}/actas/{acta}", json={"periodo_hasta": "2026-10-19"})
        self.assertEqual(r.status_code, 409)
        # una revisión sí toma lo nuevo
        rev = _ok(self.c.post(f"{API}/actas/{acta}/revision", json={"motivo": "Corrección de horómetro"}))
        self.assertEqual((rev["numero"], rev["revision"]), (23, 1))
        self.assertEqual(self._item(rev["resultado"], "I.1")["salidas"]["certificar"], 400)
        _ok(self.c.delete(f"{API}/actas/{rev['id']}"))
        self._put_item("I.1", config=it["config"])
        with _db() as conn:
            conn.execute("UPDATE horas_maquinas SET hs_final = CAST(hs_final AS REAL) - 50 WHERE ci = 'M-25' AND nro_parte = 'T72457'")

    def test_10_acumulado_anterior_desde_actas_congeladas(self):
        res = _ok(self.c.post(f"{API}/contratos/{self.cid}/simular", json={"periodo_desde": "2026-10-21", "periodo_hasta": "2026-11-20", "numero": 24}))
        i1 = self._item(res, "I.1")
        self.assertEqual(i1["anterior"], 5664 + 267)
        self.assertEqual(self._item(res, "II.1")["anterior"], 6300)
        sug = _ok(self.c.get(f"{API}/contratos/{self.cid}/sugerencia"))
        self.assertEqual((sug["numero"], sug["periodo_desde"], sug["periodo_hasta"]), (24, "2026-10-21", "2026-11-20"))

    def test_11_bloqueo_impide_avanzar(self):
        acta = _ok(self.c.post(f"{API}/contratos/{self.cid}/actas", json={"numero": 99, "periodo_desde": DESDE, "periodo_hasta": HASTA}))
        _ok(self.c.put(f"{API}/actas/{acta['id']}", json={"overrides": {"validaciones": [
            {"codigo": "DUPLICADO_EQUIPO_FECHA_TURNO", "activa": True, "severidad": "BLOQUEO", "param": {}}]}}))
        r = self.c.post(f"{API}/actas/{acta['id']}/estado", json={"destino": "EN_REVISION"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("bloquean", r.json()["detail"])
        _ok(self.c.delete(f"{API}/actas/{acta['id']}"))

    # ---------------------------------------------------------------- Precarga del certificado
    def test_12_precarga_y_vinculo_con_certificado(self):
        lista = _ok(self.c.get(f"/operaciones/seguimiento-cliente/1/actas-medicion?oc={self.oc_id}"))
        fila = next(a for a in lista if a["acta_id"] == self.acta23)
        self.assertIsNone(fila["certificado_vigente"])
        p = _ok(self.c.get(f"/operaciones/seguimiento-cliente/1/actas-medicion/{self.acta23}/precarga"))
        lineas = {l["item_oc_cliente_id"]: l["cantidad_periodo"] for l in p["lineas"]}
        self.assertEqual(lineas, {self.oc_min: 230, self.oc_ext: 37})
        self.assertEqual(p["ordenes"], [self.oc_id])
        self.assertIn("Gasoil a descontar", p["conceptos_sin_precio"])
        cert = _ok(self.c.post("/operaciones/seguimiento-cliente/1/certificados", json={
            "modalidad": "ITEMS", "alcance": "OC", "orden_compra_cliente_id": self.oc_id, "tipo": "PARCIAL", "moneda": "USD",
            "fecha_certificado": "2026-10-27", "periodo_desde": p["periodo_desde"], "periodo_hasta": p["periodo_hasta"],
            "observaciones": p["observaciones"], "lineas": [{"item_oc_cliente_id": l["item_oc_cliente_id"], "cantidad_periodo": l["cantidad_periodo"]} for l in p["lineas"]],
            "descuentos": p["descuentos"]}))
        _ok(self.c.post(f"/operaciones/seguimiento-cliente/1/actas-medicion/{self.acta23}/vincular", json={"certificado_id": cert["certificado_id"]}))
        self.assertAlmostEqual(cert["calculo"]["cabecera"]["importe_periodo"], round(230 * 178.03 + 37 * 160.22, 2), places=2)
        lista = _ok(self.c.get(f"/operaciones/seguimiento-cliente/1/actas-medicion?oc={self.oc_id}"))
        self.assertEqual(next(a for a in lista if a["acta_id"] == self.acta23)["certificado_vigente"]["id"], cert["certificado_id"])
        otro = _ok(self.c.post("/operaciones/seguimiento-cliente/1/certificados", json={
            "modalidad": "ITEMS", "alcance": "OC", "orden_compra_cliente_id": self.oc_id, "tipo": "PARCIAL", "moneda": "USD",
            "fecha_certificado": "2026-10-27", "periodo_desde": DESDE, "periodo_hasta": HASTA, "lineas": [{"item_oc_cliente_id": self.oc_min, "cantidad_periodo": 1}]}))
        r = self.c.post(f"/operaciones/seguimiento-cliente/1/actas-medicion/{self.acta23}/vincular", json={"certificado_id": otro["certificado_id"]})
        self.assertEqual(r.status_code, 409)
        # con un certificado vigente el acta no se anula
        r = self.c.post(f"{API}/actas/{self.acta23}/anular", json={"motivo": "Prueba de anulación con certificado"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(_ok(self.c.get(f"/operaciones/seguimiento-cliente/1/certificados/{cert['certificado_id']}/acta-medicion"))["numero"], 23)

    def test_13_campos_dinamicos_y_formula_segura(self):
        campos = {c["campo"]: c for c in _ok(self.c.get(f"{API}/campos"))}
        for k in ("hs_inicio", "hs_final", "gasoil", "viajes", "km_total", "origen_carga", "centro_costo_id"):
            self.assertIn(k, campos)
        self.assertTrue(campos["hs_inicio"]["numerico"])
        r = self.c.post(f"{API}/formula/validar", json={"formula": "__import__('os').system('x')"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(_ok(self.c.post(f"{API}/formula/validar", json={"formula": "si(viajes > 0, viajes * f.carga_m3, 0)"}))["nombres"], ["f.carga_m3", "viajes"])
        # columna nueva del parte: aparece sola en el catálogo del reporte por parte
        with _db() as conn:
            conn.execute("ALTER TABLE horas_maquinas ADD COLUMN prueba_campo_nuevo REAL")
        cat = _ok(self.c.get(f"{API}/catalogo?contrato_id={self.cid}"))
        self.assertIn("prueba_campo_nuevo", [x["valor"] for x in cat["PARTE"]])

    def test_14_pantalla(self):
        r = self.c.get("/operaciones/actas-medicion.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("amPrecargarCertificado", r.text)
        html = self.c.get("/operaciones").text
        self.assertIn('id="btn-sidebar-actas-medicion"', html)
        self.assertIn("btn-cert-ed-acta", html)


def fila_partes(reporte, codigo):
    return next(f["partes"] for s in reporte["secciones"] for f in s["filas"] if f["celdas"][0] == codigo)


if __name__ == "__main__":
    unittest.main()
