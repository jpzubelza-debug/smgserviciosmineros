"""Pruebas de Operaciones › Actas de Medición (acta = OC + período + equipos + columnas del parte diario).

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
# Hoja de detalle de la planilla -> CI del equipo en el sistema y turno
EQUIPOS = {"I.1": ("M-25", None), "I.2": ("C-28", "DIA"), "I.3": ("M-24", None), "I.4": ("M-34", None), "I.5": ("C-34", None),
           "I.6": ("M-33", None), "I.7a": ("V-32", None), "I.7b": ("V-27", None), "I.8": ("C-22", None), "I.9": ("C-16", "DIA"),
           "I.10": ("C-28", "NOCHE")}
COLUMNAS = ["fecha_parte", "nro_parte", "maquinista", "descripcion_trabajos", "hs_inicio", "hs_final", "total_hs", "gasoil"]


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


def _err(r, codigo):
    assert r.status_code == codigo, f"esperaba {codigo}, vino {r.status_code}: {r.text[:500]}"
    return r.json()["detail"]


class ActasMedicionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = _cliente()
        with _db() as conn:
            cls.oc_id = conn.execute("INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, moneda, monto_oc) "
                                     "VALUES (1, 90, 'TEST-470-069', 'USD', 1840150)").lastrowid
            item = ("INSERT INTO items_oc_cliente (orden_compra_cliente_id, numero, descripcion, unidad, cantidad, moneda, precio, total) "
                    "VALUES (?, ?, ?, 'hs', 5000, 'USD', ?, ? * 5000)")
            cls.it_moto = conn.execute(item, (cls.oc_id, 1, "Motoniveladora hs", 178.03, 178.03)).lastrowid
            cls.it_regador = conn.execute(item, (cls.oc_id, 2, "Camión regador hs", 150.0, 150.0)).lastrowid
            cls.it_camioneta = conn.execute(item, (cls.oc_id, 3, "Camioneta hs", 40.0, 40.0)).lastrowid
            otra = conn.execute("INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, moneda) VALUES (1, 91, 'OTRA', 'USD')").lastrowid
            cls.it_otra = conn.execute(item, (otra, 1, "De otra OC", 1.0, 1.0)).lastrowid
            for p in json.load(open(os.path.join(BASE_DIR, "tests", "fixtures", "foja23_partes.json"), encoding="utf-8")):
                ci, turno = EQUIPOS[p["hoja"]]
                conn.execute(
                    "INSERT INTO horas_maquinas (ci, fecha_parte, nro_parte, maquinista, descripcion_trabajos, turno, hs_inicio, hs_final, total_hs, gasoil, cliente) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '000070 - MINERA EXAR S.A.')",
                    (ci, p["fecha"], f"T{p['nro_parte']}", p["maquinista"], p["descripcion"], turno or "DIA", p["hs_inicio"], p["hs_final"], p["total_hs"], p["gasoil"]))
            # Un parte con fecha en formato dd/mm/aaaa también entra; uno fuera del período no.
            conn.execute("INSERT INTO horas_maquinas (ci, fecha_parte, nro_parte, turno, total_hs) VALUES ('ZZ-1', '05/10/2026', 'X1', 'DIA', 8)")
            conn.execute("INSERT INTO horas_maquinas (ci, fecha_parte, nro_parte, turno, total_hs) VALUES ('ZZ-1', '2026-10-21', 'X2', 'DIA', 5)")

    @classmethod
    def tearDownClass(cls):
        assert os.path.getmtime(DB_REAL) == MTIME_REAL, "¡La base real fue modificada!"

    def _datos(self, **extra):
        datos = {"orden_compra_cliente_id": self.oc_id, "numero": 23, "periodo_desde": DESDE, "periodo_hasta": HASTA, "fecha": "2026-10-27",
                 "campo_cantidad": "total_hs", "columnas": COLUMNAS,
                 "equipos": [{"ci": "M-25", "item_oc_cliente_id": self.it_moto}, {"ci": "c-28", "item_oc_cliente_id": self.it_regador},
                             {"ci": "C-16", "item_oc_cliente_id": self.it_regador}, {"ci": "V-32", "item_oc_cliente_id": self.it_camioneta},
                             {"ci": "M-24"}]}
        datos.update(extra)
        return datos

    def _eq(self, res, ci):
        return next(e for e in res["equipos"] if e["ci"] == ci)

    def test_01_equipos_con_partes(self):
        lista = _ok(self.c.get(f"{API}/equipos-con-partes", params={"desde": DESDE, "hasta": HASTA}))
        por_ci = {x["ci"]: x for x in lista}
        self.assertEqual(por_ci["M-25"]["partes"], 30)
        self.assertEqual(por_ci["M-25"]["total_hs"], 267)
        self.assertEqual(por_ci["ZZ-1"]["partes"], 1, "el parte en dd/mm/aaaa entra y el del 21/10 no")

    def test_02_vista_previa_suma_por_equipo_e_item(self):
        res = _ok(self.c.post(f"{API}/vista-previa", json=self._datos()))
        m25 = self._eq(res, "M-25")
        self.assertEqual((len(m25["partes"]), m25["total"]), (30, 267))
        self.assertEqual(m25["totales"]["gasoil"], 2361)
        self.assertEqual(set(m25["totales"]), {"total_hs", "gasoil"}, "N° de parte, hs inicio y hs final no se suman")
        self.assertEqual([c["campo"] for c in res["columnas"]], COLUMNAS)
        self.assertEqual(m25["partes"][0]["valores"]["nro_parte"], "T72457")
        self.assertEqual(self._eq(res, "C-28")["total"], 600, "C-28 trabaja turno día y noche")
        items = {i["item_oc_cliente_id"]: i for i in res["items_oc"]}
        self.assertEqual(items[self.it_moto]["cantidad_periodo"], 267)
        self.assertEqual(items[self.it_moto]["importe"], round(267 * 178.03, 2))
        self.assertEqual(items[self.it_regador]["cantidad_periodo"], 600 + 299)
        self.assertEqual(items[self.it_regador]["equipos"], ["C-28", "C-16"])
        self.assertTrue(any("M-24" in a and "ítem" in a for a in res["avisos"]), res["avisos"])

    def test_02b_indicadores_por_equipo_y_general(self):
        datos = self._datos()
        datos["columnas"] = ["fecha_parte", "total_hs"]  # los indicadores no dependen de las columnas elegidas
        res = _ok(self.c.post(f"{API}/vista-previa", json=datos))
        ind = res["indicadores"]
        campos = [c["campo"] for c in ind["columnas"]]
        self.assertEqual(campos[:2], ["total_hs", "gasoil"])
        self.assertNotIn("viajes", campos, "sin valores en el período no se muestra")
        m25 = next(f for f in ind["equipos"] if f["ci"] == "M-25")
        self.assertEqual((m25["partes"], m25["valores"]["total_hs"], m25["valores"]["gasoil"]), (30, 267, 2361))
        self.assertEqual(m25["consumo_lh"], round(2361 / 267, 2))
        g = ind["general"]
        self.assertEqual(g["valores"]["gasoil"], round(sum(f["valores"]["gasoil"] for f in ind["equipos"]), 2))
        self.assertNotIn("partes", g, "días y partes no se suman en la fila General")
        self.assertEqual(g["consumo_lh"], round(g["valores"]["gasoil"] / g["valores"]["total_hs"], 2))

    def test_03_columna_propia_por_equipo(self):
        datos = self._datos()
        datos["equipos"][0]["campo_cantidad"] = "gasoil"
        res = _ok(self.c.post(f"{API}/vista-previa", json=datos))
        self.assertEqual(self._eq(res, "M-25")["total"], 2361)
        self.assertEqual(self._eq(res, "C-16")["total"], 299)

    def test_04_validaciones(self):
        self.assertIn("no es de la OC", _err(self.c.post(f"{API}/actas", json=self._datos(equipos=[{"ci": "M-25", "item_oc_cliente_id": self.it_otra}])), 400))
        self.assertIn("columna", _err(self.c.post(f"{API}/actas", json=self._datos(campo_cantidad="no_existe")), 400))
        self.assertIn("dos veces", _err(self.c.post(f"{API}/actas", json=self._datos(equipos=[{"ci": "M-25"}, {"ci": "m-25"}])), 400))
        self.assertIn("período", _err(self.c.post(f"{API}/actas", json=self._datos(periodo_desde=HASTA, periodo_hasta=DESDE)), 400))

    def test_10_ciclo_completo(self):
        a = _ok(self.c.post(f"{API}/actas", json=self._datos()))
        self.assertEqual((a["estado"], a["numero"], len(a["equipos"])), ("BORRADOR", 23, 5))
        self.assertEqual(self._eq(a["resultado"], "M-25")["total"], 267)
        self.assertIn("Ya existe", _err(self.c.post(f"{API}/actas", json=self._datos()), 409).capitalize())
        # Lista y sugerencia de la siguiente: número, período y equipos copiados
        self.assertTrue(any(x["id"] == a["id"] for x in _ok(self.c.get(f"{API}/actas", params={"seguimiento": 1}))))
        sug = _ok(self.c.get(f"{API}/sugerencia", params={"oc": self.oc_id}))
        self.assertEqual((sug["numero"], sug["periodo_desde"], sug["periodo_hasta"]), (24, "2026-10-21", "2026-11-20"))
        self.assertEqual([e["ci"] for e in sug["equipos"]], ["M-25", "C-28", "C-16", "V-32", "M-24"])
        self.assertEqual(sug["equipos"][0]["item_oc_cliente_id"], self.it_moto)
        self.assertEqual(sug["columnas"], COLUMNAS)
        # Editar el borrador
        a = _ok(self.c.put(f"{API}/actas/{a['id']}", json=self._datos(equipos=self._datos()["equipos"][:4], observaciones="Foja 23")))
        self.assertEqual((len(a["equipos"]), a["observaciones"]), (4, "Foja 23"))
        # Cerrar: el resultado queda guardado aunque cambien los partes
        a = _ok(self.c.post(f"{API}/actas/{a['id']}/cerrar"))
        self.assertEqual(a["estado"], "CERRADA")
        with _db() as conn:
            conn.execute("INSERT INTO horas_maquinas (ci, fecha_parte, nro_parte, turno, total_hs) VALUES ('M-25', '2026-10-01', 'EXTRA', 'NOCHE', 100)")
        a = _ok(self.c.get(f"{API}/actas/{a['id']}"))
        self.assertEqual(self._eq(a["resultado"], "M-25")["total"], 267)
        _err(self.c.put(f"{API}/actas/{a['id']}", json=self._datos()), 409)
        _err(self.c.delete(f"{API}/actas/{a['id']}"), 409)
        # Precarga del certificado
        lista = _ok(self.c.get("/operaciones/seguimiento-cliente/1/actas-medicion", params={"oc": self.oc_id}))
        self.assertTrue(any(x["acta_id"] == a["id"] and x["certificado_vigente"] is None for x in lista))
        p = _ok(self.c.get(f"/operaciones/seguimiento-cliente/1/actas-medicion/{a['id']}/precarga"))
        lineas = {l["item_oc_cliente_id"]: l["cantidad_periodo"] for l in p["lineas"]}
        self.assertEqual(lineas, {self.it_moto: 267, self.it_regador: 899, self.it_camioneta: self._eq(a["resultado"], "V-32")["total"]})
        self.assertEqual((p["ordenes"], p["periodo_desde"]), ([self.oc_id], DESDE))
        _err(self.c.get(f"/operaciones/seguimiento-cliente/2/actas-medicion/{a['id']}/precarga"), 400)
        # Excel
        r = self.c.get(f"{API}/actas/{a['id']}/excel")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"PK"))
        # Reabrir: vuelve a calcular en vivo (ahora con el parte nuevo)
        a = _ok(self.c.post(f"{API}/actas/{a['id']}/reabrir"))
        self.assertEqual((a["estado"], self._eq(a["resultado"], "M-25")["total"]), ("BORRADOR", 367))
        # Anular exige motivo; anulada no se toca y libera el número
        _err(self.c.post(f"{API}/actas/{a['id']}/anular", json={"motivo": "corto"}), 400)
        a = _ok(self.c.post(f"{API}/actas/{a['id']}/anular", json={"motivo": "Período cargado por error"}))
        self.assertEqual(a["estado"], "ANULADA")
        _err(self.c.post(f"{API}/actas/{a['id']}/reabrir"), 400)
        nueva = _ok(self.c.post(f"{API}/actas", json=self._datos()))
        self.assertEqual(nueva["numero"], 23)
        _ok(self.c.delete(f"{API}/actas/{nueva['id']}"))

    def test_15_minimo_objetivo_y_graficos(self):
        base = {i["item_oc_cliente_id"]: i["cantidad_periodo"] for i in _ok(self.c.post(f"{API}/vista-previa", json=self._datos()))["items_oc"]}
        moto, regador = base[self.it_moto], base[self.it_regador]
        metas = [{"item_oc_cliente_id": self.it_moto, "minimo": moto + 30, "objetivo": moto + 130},
                 {"item_oc_cliente_id": self.it_regador, "minimo": 500, "objetivo": regador - 99},
                 {"item_oc_cliente_id": self.it_camioneta, "minimo": "", "objetivo": None}]
        res = _ok(self.c.post(f"{API}/vista-previa", json=self._datos(metas=metas)))
        items = {i["item_oc_cliente_id"]: i for i in res["items_oc"]}
        self.assertEqual((items[self.it_moto]["estado_meta"], items[self.it_moto]["pct_objetivo"]), ("BAJO_MINIMO", round(moto / (moto + 130) * 100, 1)))
        self.assertEqual(items[self.it_regador]["estado_meta"], "OBJETIVO")
        self.assertIsNone(items[self.it_camioneta]["estado_meta"], "sin valores no hay meta")
        self.assertEqual(items[self.it_moto]["cantidad_periodo"], moto, "la meta no cambia lo que se certifica")
        self.assertEqual((res["kpi"]["items_con_meta"], res["kpi"]["bajo_minimo"], res["kpi"]["en_objetivo"]), (2, 1, 1))
        self.assertEqual(res["kpi"]["pct_objetivo"], round((moto + regador) / (moto + 130 + regador - 99) * 100, 1))
        self.assertTrue(any("bajo el mínimo" in a for a in res["avisos"]), res["avisos"])
        self.assertIn("mínimo no puede superar", _err(self.c.post(f"{API}/vista-previa", json=self._datos(
            metas=[{"item_oc_cliente_id": self.it_moto, "minimo": 5, "objetivo": 1}])), 400))
        self.assertIn("no pertenece", _err(self.c.post(f"{API}/vista-previa", json=self._datos(
            metas=[{"item_oc_cliente_id": self.it_otra, "minimo": 5}])), 400))
        # Se guardan con el acta, se copian a la siguiente y quedan congeladas al cerrar
        graficos = [{"tipo": "avance_item", "item_oc_cliente_id": self.it_moto, "ancho": "completo"}, {"tipo": "equipos", "indicador": "gasoil"}]
        a = _ok(self.c.post(f"{API}/actas", json=self._datos(numero=50, metas=metas, graficos=graficos)))
        self.assertEqual(len(a["metas"]), 2)
        self.assertEqual(a["graficos"][1], {"tipo": "equipos", "ancho": "medio", "indicador": "gasoil"})
        self.assertEqual(a["resultado"]["kpi"]["bajo_minimo"], 1)
        sug = _ok(self.c.get(f"{API}/sugerencia", params={"oc": self.oc_id}))
        self.assertEqual((len(sug["metas"]), len(sug["graficos"])), (2, 2))
        a = _ok(self.c.post(f"{API}/actas/{a['id']}/cerrar"))
        # En un acta cerrada se pueden acomodar los gráficos (no cambia el resultado)
        g = _ok(self.c.put(f"{API}/actas/{a['id']}/graficos", json={"graficos": [{"tipo": "tarjetas", "ancho": "completo"}]}))
        self.assertEqual(g["graficos"], [{"tipo": "tarjetas", "ancho": "completo"}])
        a2 = _ok(self.c.get(f"{API}/actas/{a['id']}"))
        self.assertEqual((a2["graficos"], a2["resultado"], len(a2["metas"])), (g["graficos"], a["resultado"], 2))
        self.assertIsNone(_ok(self.c.put(f"{API}/actas/{a['id']}/graficos", json={"graficos": None}))["graficos"], "None = los de siempre")
        self.assertIsNone(_ok(self.c.get(f"{API}/actas/{a['id']}"))["graficos"])
        self.assertIn("desconocido", _err(self.c.put(f"{API}/actas/{a['id']}/graficos", json={"graficos": [{"tipo": "torta"}]}), 400))
        r = self.c.get(f"{API}/actas/{a['id']}/excel")
        self.assertEqual(r.status_code, 200)
        _ok(self.c.post(f"{API}/actas/{a['id']}/anular", json={"motivo": "Prueba de metas y gráficos"}))
        _err(self.c.put(f"{API}/actas/{a['id']}/graficos", json={"graficos": []}), 409)

    def test_20_sin_items_no_cierra(self):
        a = _ok(self.c.post(f"{API}/actas", json=self._datos(numero=99, equipos=[{"ci": "M-24"}])))
        self.assertIn("ítem", _err(self.c.post(f"{API}/actas/{a['id']}/cerrar"), 400))
        _ok(self.c.delete(f"{API}/actas/{a['id']}"))

    def test_30_tablas_viejas_intactas(self):
        with _db() as conn:
            nombres = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'am\\_%' ESCAPE '\\'")}
        self.assertTrue({"am_mediciones", "am_medicion_equipos", "am_medicion_certificados", "am_eventos", "am_medicion_kpi"} <= nombres, nombres)


if __name__ == "__main__":
    unittest.main()
