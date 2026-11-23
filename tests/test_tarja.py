"""Pruebas de Operaciones › Tarja General (Registro Diario).

Se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal (DATA_DIR se fija antes de importar main).

    .venv-1\\Scripts\\python.exe -m unittest tests.test_tarja -v
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
import unittest

if "main" in sys.modules:
    raise unittest.SkipTest("main ya fue importado con otra DATA_DIR; ejecute este módulo por separado.")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_REAL = os.getenv("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db")
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_tarja_")
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
import tarja  # noqa: E402

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

API = "/api/tarja"
CONSULTA = ("consulta.tarja@test.local", "clave-consulta-1")
# Operadores de prueba (solo en la copia)
ROT, DIA, INACT = "T9001", "T9002", "T9003"


def _db():
    conn = sqlite3.connect(DB_COPIA)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _cliente(usuario="contingencia_test", clave="clave-contingencia-test"):
    c = httpx.Client(base_url=f"http://127.0.0.1:{PUERTO}", follow_redirects=False, timeout=60)
    r = c.post("/login", data={"username": usuario, "password": clave})
    assert r.status_code == 302, r.text
    return c


def _ok(r, codigo=200):
    assert r.status_code == codigo, f"{r.status_code}: {r.text[:800]}"
    return r.json()


def _err(r, codigo):
    assert r.status_code == codigo, f"esperaba {codigo}, vino {r.status_code}: {r.text[:500]}"
    return r.json()["detail"]


def _dias(legajo, anio, mes, valores, desde=1):
    return [{"legajo": legajo, "fecha": f"{anio}-{mes:02d}-{d:02d}", "codigo": v} for d, v in enumerate(valores, desde)]


class TarjaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = _cliente()
        with _db() as conn:
            for lg, nombre, activo, reg in ((ROT, "PRUEBA ROTATIVO", "ACTIVO", "Rotativo"), (DIA, "PRUEBA DIARIO", "ACTIVO", "Diario"),
                                            (INACT, "PRUEBA INACTIVO", "INACTIVO", "Rotativo")):
                conn.execute("INSERT INTO personal (legajo, nombre, activo, regimen_turno) VALUES (?, ?, ?, ?)", (lg, nombre, activo, reg))
            conn.execute("""INSERT INTO usuarios (nombre_apellido, dni, legajo, correo, password_hash, estado, tipo_usuario, modulos_json,
                paneles_json, acciones_json, bloqueado, intentos_fallidos, password_temporal) VALUES
                ('Consulta Tarja', '99999999', '', ?, ?, 'ACTIVO', 'USUARIO', '["operaciones"]', ?, ?, 0, 0, 0)""",
                         (CONSULTA[0], hashlib.sha256(CONSULTA[1].encode()).hexdigest(), json.dumps({"operaciones": ["tarja_general"]}),
                          json.dumps({"operaciones": {"tarja_general": ["consultar"]}})))

    @classmethod
    def tearDownClass(cls):
        assert os.path.getmtime(DB_REAL) == MTIME_REAL, "¡La base real fue modificada!"

    def _op(self, g, legajo):
        """Operador de la grilla; en grillas de un mes, dias queda por día del mes ("2") y fechas por fecha ISO."""
        o = next(o for o in g["operadores"] if o["legajo"] == legajo)
        return {**o, "fechas": o["dias"], "dias": {str(int(k[8:10])): v for k, v in o["dias"].items()}}

    def _cargar(self, cambios, anio=2026, mes=10):
        return _ok(self.c.post(f"{API}/celdas", json={"anio": anio, "mes": mes, "cambios": cambios}))

    # ------------------------------------------------------------------ estructura
    def test_01_migracion_y_codigos(self):
        with _db() as conn:
            cods = {r["codigo"]: dict(r) for r in conn.execute("SELECT * FROM tarja_codigos_estado")}
            self.assertEqual(set(cods), {"D", "V", "F", "A", "AJ", "LIC", "PER", "SAN", "VJ", "CAP", "COMP", "FER", "NUE", "BAJA"})
            cfg = dict(conn.execute("SELECT * FROM tarja_config").fetchone())
            self.assertEqual((cfg["horas_efectivas"], cfg["horas_break"], cfg["horas_efectivas_cambio_turno"], cfg["horas_break_cambio_turno"]), (10, 2, 5, 1))
            self.assertIn("regimen_turno", {f[1] for f in conn.execute("PRAGMA table_info(personal)")})
            tarja.migrar(conn)  # idempotente
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tarja_codigos_estado").fetchone()[0], 14)
            with self.assertRaises(sqlite3.IntegrityError):  # exactamente uno de centro de costo / estado
                conn.execute("INSERT INTO tarja_registros (legajo, fecha, centro_costo_id, codigo_estado_id, creado_por, creado_en) "
                             "VALUES (?, '2026-01-01', 106, 1, 't', 't')", (ROT,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO tarja_registros (legajo, fecha, creado_por, creado_en) VALUES (?, '2026-01-01', 't', 't')", (ROT,))

    def test_02_grilla_operadores_activos_y_centros_de_la_base(self):
        op = _ok(self.c.get(f"{API}/opciones"))
        ccs = {c["id"]: c["nombre"] for c in op["centros_costos"]}
        self.assertEqual(ccs[106], "OBRA EXAR - MANT. CAMINO Y REPOS. COMB.")
        self.assertEqual(ccs[209], "MANTENIMIENTO DE CAMINO SSR")
        g = _ok(self.c.get(f"{API}/grilla", params={"anio": 2026, "mes": 2}))
        legajos = {o["legajo"] for o in g["operadores"]}
        self.assertIn(ROT, legajos)
        self.assertNotIn(INACT, legajos, "solo operadores activos")
        with _db() as conn:
            activos = conn.execute("SELECT COUNT(*) FROM personal WHERE activo = 'ACTIVO'").fetchone()[0]
        self.assertEqual(len(g["operadores"]), activos)
        self.assertEqual(len(g["dias"]), 28)
        self.assertEqual(g["dias"][0]["dow"], 6, "01/02/2026 es domingo")

    # ------------------------------------------------------------------ turnos y horas
    def test_03_rotativo_subida_bajada_y_cambio_de_obra(self):
        #       1    2      3      4      5      6      7    8     9    10
        vals = ["D", "106", "106", "209", "210", "106", "D", "219", "D", "D"]
        res = self._cargar(_dias(ROT, 2026, 10, vals))
        o = self._op(res, ROT)
        h = {int(d): (c["c"], c["h"], c["b"], c["ct"]) for d, c in o["dias"].items()}
        self.assertEqual(h[2], ("106", 5, 1, 1), "subida = media jornada")
        self.assertEqual(h[3], ("106", 10, 2, 0))
        self.assertEqual(h[4], ("209", 10, 2, 0), "cambiar de obra dentro del turno no es cambio de turno")
        self.assertEqual(h[5], ("210", 10, 2, 0))
        self.assertEqual(h[6], ("106", 5, 1, 1), "bajada = media jornada")
        self.assertEqual(h[8], ("219", 5, 1, 1), "turno de un día: media jornada una sola vez")
        self.assertEqual(h[1], ("D", 0, 0, 0))
        t = o["totales"]
        self.assertEqual((t["trabajados"], t["cambios_turno"], t["descansos"], t["horas_efectivas"], t["horas_break"]), (6, 3, 4, 45, 9))

    def test_04_turno_abierto_si_el_dia_siguiente_no_esta_planificado(self):
        res = self._cargar(_dias(ROT, 2026, 10, ["209", "209", "209"], desde=20))
        o = self._op(res, ROT)
        self.assertEqual((o["dias"]["20"]["ct"], o["dias"]["22"]["ct"], o["dias"]["22"]["h"]), (1, 0, 10), "sin día siguiente: no hay bajada")
        res = self._cargar(_dias(ROT, 2026, 10, ["D"], desde=23))
        self.assertEqual((self._op(res, ROT)["dias"]["22"]["ct"], self._op(res, ROT)["dias"]["22"]["h"]), (1, 5), "al cargar D se recalcula la bajada")
        # Turno que cruza el fin de mes: el 31/10 no es bajada si el 01/11 sigue trabajado.
        self._cargar(_dias(ROT, 2026, 10, ["D", "106", "106"], desde=29) + _dias(ROT, 2026, 11, ["106", "D"]))
        g = _ok(self.c.get(f"{API}/grilla", params={"anio": 2026, "mes": 10}))
        self.assertEqual(self._op(g, ROT)["dias"]["31"]["h"], 10)
        g = _ok(self.c.get(f"{API}/grilla", params={"anio": 2026, "mes": 11}))
        self.assertEqual(self._op(g, ROT)["dias"]["1"]["h"], 5)

    def test_05_regimen_diario_siempre_jornada_completa(self):
        res = self._cargar(_dias(DIA, 2026, 10, ["D", "27", "27", "27", "D", "27", "D"]))
        o = self._op(res, DIA)
        self.assertEqual([o["dias"][str(d)]["h"] for d in (2, 3, 4, 6)], [10, 10, 10, 10])
        self.assertEqual(o["totales"]["cambios_turno"], 0)
        # Pasar a Rotativo recalcula subida/bajada
        _ok(self.c.put(f"{API}/regimen", json={"legajo": DIA, "regimen": "Rotativo"}))
        o = self._op(_ok(self.c.get(f"{API}/grilla", params={"anio": 2026, "mes": 10})), DIA)
        self.assertEqual([o["dias"][str(d)]["h"] for d in (2, 3, 4, 6)], [5, 10, 5, 5])
        _ok(self.c.put(f"{API}/regimen", json={"legajo": DIA, "regimen": "Diario"}))

    def test_06_estado_que_cuenta_como_trabajado(self):
        res = self._cargar(_dias(ROT, 2026, 10, ["D", "VJ", "211", "211", "VJ", "D"], desde=12))
        d = self._op(res, ROT)["dias"]
        self.assertEqual([d[str(x)]["h"] for x in (13, 14, 15, 16)], [5, 10, 10, 5], "Viaje continúa el turno y suma jornada")

    # ------------------------------------------------------------------ validación
    def test_07_validacion_codigos(self):
        det = _err(self.c.post(f"{API}/celdas", json={"cambios": _dias(ROT, 2026, 9, ["106", "107"])}), 422)
        self.assertIn("107", det)
        self.assertIn("no existe", det)
        _err(self.c.post(f"{API}/celdas", json={"cambios": _dias(ROT, 2026, 9, ["XYZ"])}), 422)
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tarja_registros WHERE legajo = ? AND fecha LIKE '2026-09-%'", (ROT,)).fetchone()[0], 0,
                             "una celda inválida rechaza toda la carga")
        r = self._cargar(_dias(ROT, 2026, 9, ["d", " 106 "]), mes=9)
        self.assertEqual(self._op(r, ROT)["dias"]["1"]["c"], "D", "minúsculas y espacios se normalizan")
        # Código inactivo no se acepta
        with _db() as conn:
            kid = conn.execute("SELECT id FROM tarja_codigos_estado WHERE codigo = 'NUE'").fetchone()[0]
        _ok(self.c.put(f"{API}/codigos/{kid}", json={"codigo": "NUE", "descripcion": "Ingreso nuevo", "color": "#7dd3fc", "categoria": "OTRO", "activo": False}))
        self.assertIn("inactivo", _err(self.c.post(f"{API}/celdas", json={"cambios": _dias(ROT, 2026, 9, ["NUE"], desde=5)}), 422))
        _ok(self.c.put(f"{API}/codigos/{kid}", json={"codigo": "NUE", "descripcion": "Ingreso nuevo", "color": "#7dd3fc", "categoria": "OTRO", "activo": True}))
        # Borrar
        r = self._cargar([{"legajo": ROT, "fecha": "2026-09-01", "codigo": ""}], mes=9)
        self.assertNotIn("1", self._op(r, ROT)["dias"])

    # ------------------------------------------------------------------ edición manual
    def test_08_horas_a_mano_no_se_pisan(self):
        self._cargar(_dias(ROT, 2026, 8, ["D", "106", "106", "106", "D"]), mes=8)
        r = _ok(self.c.put(f"{API}/registro", json={"legajo": ROT, "fecha": "2026-08-03", "horas_efectivas": 8, "horas_break": 1.5,
                                                     "observaciones": "Se retiró antes", "anio": 2026, "mes": 8}))
        self.assertEqual((r["registro"]["horas_efectivas"], r["registro"]["editado_manual"]), (8, 1))
        # Recalcular (cambio en el turno) no pisa el día manual
        self._cargar(_dias(ROT, 2026, 8, ["106"], desde=5), mes=8)
        with _db() as conn:
            f = dict(conn.execute("SELECT * FROM tarja_registros WHERE legajo = ? AND fecha = '2026-08-03'", (ROT,)).fetchone())
        self.assertEqual((f["horas_efectivas"], f["horas_break"], f["editado_manual"], f["observaciones"]), (8, 1.5, 1, "Se retiró antes"))
        # 106 → 209 conserva lo manual (sigue trabajado); 106 → D lo descarta
        self._cargar(_dias(ROT, 2026, 8, ["209"], desde=3), mes=8)
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT editado_manual FROM tarja_registros WHERE legajo = ? AND fecha = '2026-08-03'", (ROT,)).fetchone()[0], 1)
        r = _ok(self.c.put(f"{API}/registro", json={"legajo": ROT, "fecha": "2026-08-03", "restaurar": True, "anio": 2026, "mes": 8}))
        self.assertEqual((r["registro"]["horas_efectivas"], r["registro"]["editado_manual"]), (10, 0))
        _err(self.c.put(f"{API}/registro", json={"legajo": ROT, "fecha": "2026-08-20", "horas_efectivas": 3}), 404)
        _err(self.c.put(f"{API}/registro", json={"legajo": ROT, "fecha": "2026-08-02", "horas_efectivas": 30}), 400)

    # ------------------------------------------------------------------ auditoría
    def test_09_auditoria(self):
        self._cargar(_dias(ROT, 2026, 7, ["106"]), mes=7)
        self._cargar(_dias(ROT, 2026, 7, ["209"]), mes=7)
        h = _ok(self.c.get(f"{API}/historial", params={"legajo": ROT, "fecha": "2026-07-01"}))
        self.assertEqual([x["accion"] for x in h], ["MODIFICAR", "CREAR"])
        self.assertEqual(h[0]["usuario"], "contingencia_test")
        self.assertEqual(json.loads(h[0]["anterior_json"]), {"codigo": "106"})
        with _db() as conn:
            f = dict(conn.execute("SELECT * FROM tarja_registros WHERE legajo = ? AND fecha = '2026-07-01'", (ROT,)).fetchone())
        self.assertEqual((f["creado_por"], f["actualizado_por"]), ("contingencia_test", "contingencia_test"))
        self.assertTrue(f["creado_en"] and f["actualizado_en"])

    # ------------------------------------------------------------------ obra (hoy)
    def test_10_obra_de_hoy(self):
        hoy = tarja.hoy()
        ayer = hoy.fromordinal(hoy.toordinal() - 1)
        g = lambda: self._op(_ok(self.c.get(f"{API}/grilla", params={"anio": hoy.year, "mes": hoy.month})), DIA)  # noqa: E731
        with _db() as conn:
            conn.execute("DELETE FROM tarja_registros WHERE legajo = ? AND fecha IN (?, ?)", (DIA, hoy.isoformat(), ayer.isoformat()))
        self.assertEqual((g()["obra"]["pendiente"], g()["obra"]["texto"]), (True, "P"), "hoy sin cargar = P (pendiente)")
        _ok(self.c.post(f"{API}/celdas", json={"cambios": [{"legajo": DIA, "fecha": ayer.isoformat(), "codigo": "219"},
                                                            {"legajo": DIA, "fecha": hoy.isoformat(), "codigo": "106"}]}))
        self.assertEqual(g()["obra"]["texto"], "106 - OBRA EXAR - MANT. CAMINO Y REPOS. COMB.")
        _ok(self.c.post(f"{API}/celdas", json={"cambios": [{"legajo": DIA, "fecha": hoy.isoformat(), "codigo": "D"}]}))
        o = g()["obra"]
        self.assertEqual((o["cc_id"], o["estado"]), (219, "D"))
        self.assertTrue(o["texto"].startswith("219 - OBRA SALES DE JUJUY - COSECHA (en descanso"), o["texto"])

    # ------------------------------------------------------------------ configuración
    def test_11_config_jornada_recalcula(self):
        self._cargar(_dias(ROT, 2026, 6, ["D", "106", "106", "106", "D"]), mes=6)
        _ok(self.c.put(f"{API}/config", json={"horas_efectivas": 11, "horas_break": 1, "horas_efectivas_cambio_turno": 6, "horas_break_cambio_turno": 0.5}))
        o = self._op(_ok(self.c.get(f"{API}/grilla", params={"anio": 2026, "mes": 6})), ROT)
        self.assertEqual([(o["dias"][str(d)]["h"], o["dias"][str(d)]["b"]) for d in (2, 3, 4)], [(6, 0.5), (11, 1), (6, 0.5)])
        _ok(self.c.put(f"{API}/config", json={"horas_efectivas": 10, "horas_break": 2, "horas_efectivas_cambio_turno": 5, "horas_break_cambio_turno": 1}))
        _err(self.c.put(f"{API}/config", json={"horas_efectivas": -1}), 400)

    def test_12_codigos_y_feriados(self):
        k = _ok(self.c.post(f"{API}/codigos", json={"codigo": "guar", "descripcion": "Guardia", "color": "#112233", "categoria": "OTRO", "cuenta_como_trabajado": True}))
        self.assertEqual(k["codigo"], "GUAR")
        _err(self.c.post(f"{API}/codigos", json={"codigo": "GUAR", "descripcion": "x"}), 409)
        _err(self.c.post(f"{API}/codigos", json={"codigo": "12", "descripcion": "x"}), 400)
        r = self._cargar(_dias(ROT, 2026, 5, ["D", "GUAR", "GUAR", "D"]), mes=5)
        self.assertEqual(self._op(r, ROT)["dias"]["2"]["h"], 5)
        k2 = _ok(self.c.put(f"{API}/codigos/{k['id']}", json={**k, "cuenta_como_trabajado": False}))
        self.assertGreater(k2["recalculados"], 0)
        o = self._op(_ok(self.c.get(f"{API}/grilla", params={"anio": 2026, "mes": 5})), ROT)
        self.assertEqual(o["dias"]["2"]["h"], 0, "al dejar de contar como trabajado se recalcula")
        _ok(self.c.post(f"{API}/feriados", json={"fecha": "2026-05-25", "descripcion": "Revolución de Mayo"}))
        g = _ok(self.c.get(f"{API}/grilla", params={"anio": 2026, "mes": 5}))
        self.assertEqual(g["dias"][24]["feriado"], "Revolución de Mayo")
        _ok(self.c.delete(f"{API}/feriados/2026-05-25"))

    def test_13_preferencias_por_usuario(self):
        datos = {"filtros": {"d5": {"vals": ["106"]}}, "orden": {"key": "nombre", "dir": "desc"}, "rapidos": {"cc": "106", "regimen": "", "q": ""}}
        _ok(self.c.put(f"{API}/preferencias", json=datos))
        self.assertEqual(_ok(self.c.get(f"{API}/preferencias")), datos)
        c2 = _cliente(*CONSULTA)
        self.assertEqual(_ok(c2.get(f"{API}/preferencias")), {}, "las preferencias son de cada usuario")

    # ------------------------------------------------------------------ permisos
    def test_14_permisos(self):
        c2 = _cliente(*CONSULTA)
        _ok(c2.get(f"{API}/grilla", params={"anio": 2026, "mes": 10}))
        _err(c2.post(f"{API}/celdas", json={"cambios": _dias(ROT, 2026, 4, ["106"])}), 403)
        _err(c2.put(f"{API}/registro", json={"legajo": ROT, "fecha": "2026-10-02", "horas_efectivas": 1}), 403)
        _err(c2.put(f"{API}/config", json={}), 403)
        _err(c2.put(f"{API}/regimen", json={"legajo": ROT, "regimen": "Diario"}), 403)
        self.assertEqual(c2.get("/operaciones/tarja.js").status_code, 200)
        self.assertIn("tarja_general", main.CONTINGENCY_PANELS["operaciones"])

    # ------------------------------------------------------------------ exportación
    def test_15_exportar_filtrado_y_todo(self):
        from openpyxl import load_workbook
        r = self.c.post(f"{API}/exportar", json={"anio": 2026, "mes": 10, "legajos": [DIA, ROT]})
        self.assertEqual(r.status_code, 200, r.text[:300])
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Tarja", "Totales por operador", "Por centro de costo"])
        ws = wb["Tarja"]
        self.assertEqual([ws.cell(6, 1).value, ws.cell(7, 1).value], [DIA, ROT], "respeta el orden y el filtro de pantalla")
        self.assertEqual(ws.cell(8, 1).value, None)
        self.assertEqual(ws.cell(7, 4 + 2).value, 106)
        self.assertEqual(ws.cell(7, 4 + 1).value, "D")
        res = {f[0].value: [c.value for c in f] for f in wb["Por centro de costo"].iter_rows(min_row=4)}
        self.assertIn(106, res)
        r = self.c.post(f"{API}/exportar", json={"anio": 2026, "mes": 10})
        ws = load_workbook(io.BytesIO(r.content))["Tarja"]
        with _db() as conn:
            activos = conn.execute("SELECT COUNT(*) FROM personal WHERE activo = 'ACTIVO'").fetchone()[0]
        self.assertEqual(sum(1 for f in ws.iter_rows(min_row=6, max_col=1) if f[0].value not in (None, "Referencias:")) >= activos, True)

    # ------------------------------------------------------------------ cambio de legajo
    def test_16_cambio_de_legajo_arrastra_la_tarja(self):
        with _db() as conn:
            conn.execute("INSERT INTO personal (legajo, nombre, activo) VALUES ('T9010', 'PRUEBA LEGAJO', 'ACTIVO')")
        self._cargar(_dias("T9010", 2026, 3, ["106", "D"]), mes=3)
        with _db() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("UPDATE personal SET legajo = 'T9011' WHERE legajo = 'T9010'")
            for tabla, col in main._TABLAS_REFERENCIAN_LEGAJO:
                if tabla.startswith("tarja"):
                    conn.execute(f"UPDATE {tabla} SET {col} = 'T9011' WHERE {col} = 'T9010'")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check(tarja_registros)").fetchall(), [])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tarja_registros WHERE legajo = 'T9011'").fetchone()[0], 2)

    # ------------------------------------------------------------------ período desde / hasta
    def test_17_periodo_desde_hasta(self):
        cambios = _dias(ROT, 2026, 12, ["D", "211", "211", "211", "D"], desde=18) + _dias(ROT, 2026, 10, ["D", "210"], desde=19)
        res = _ok(self.c.post(f"{API}/celdas", json={"desde": "2026-10-20", "hasta": "2026-12-20", "cambios": cambios}))
        o = self._op(res, ROT)
        self.assertEqual(sorted(o["fechas"])[0], "2026-10-20", "devuelve solo los días del período")
        self.assertNotIn("2026-10-19", o["fechas"])
        self.assertEqual(o["fechas"]["2026-12-19"]["h"], 5)
        self.assertNotIn("2026-12-21", o["fechas"])
        g = _ok(self.c.get(f"{API}/grilla", params={"desde": "2026-10-20", "hasta": "2026-12-20"}))
        self.assertEqual((len(g["dias"]), g["dias"][0]["fecha"], g["dias"][-1]["fecha"], g["desde"], g["hasta"]),
                         (62, "2026-10-20", "2026-12-20", "2026-10-20", "2026-12-20"))
        o = self._op(g, ROT)
        with _db() as conn:
            filas = conn.execute("""SELECT r.centro_costo_id, e.cuenta_como_trabajado, r.horas_efectivas FROM tarja_registros r
                LEFT JOIN tarja_codigos_estado e ON e.id = r.codigo_estado_id WHERE legajo = ? AND fecha BETWEEN '2026-10-20' AND '2026-12-20'""",
                                 (ROT,)).fetchall()
        self.assertEqual(o["totales"]["trabajados"], sum(1 for f in filas if f[0] is not None or f[1]))
        self.assertEqual(o["totales"]["horas_efectivas"], sum(f[2] for f in filas), "los totales son del período elegido")
        _err(self.c.get(f"{API}/grilla", params={"desde": "2026-12-20", "hasta": "2026-10-20"}), 400)
        self.assertIn("186", _err(self.c.get(f"{API}/grilla", params={"desde": "2026-01-01", "hasta": "2026-12-31"}), 400))
        _err(self.c.get(f"{API}/grilla", params={"desde": "2026-13-01", "hasta": "2026-12-31"}), 400)
        from openpyxl import load_workbook
        r = self.c.post(f"{API}/exportar", json={"desde": "2026-10-20", "hasta": "2026-12-20", "legajos": [ROT]})
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("2026-10-20_a_2026-12-20", r.headers.get("content-disposition", ""))
        ws = load_workbook(io.BytesIO(r.content))["Tarja"]
        self.assertIn("20/10/2026 al 20/12/2026", ws["A1"].value)
        self.assertEqual((ws.cell(4, 5).value, ws.cell(4, 6).value, ws.cell(4, 5 + 12).value), ("20/10", 21, "1/11"))
        self.assertEqual(ws.cell(6, 5 + 60).value, 211, "19/12 = 211")


class MapeoExcelTests(unittest.TestCase):
    def test_mapeo(self):
        cc = {106, 209, 27}
        m = tarja.mapear_celda_excel
        self.assertEqual(m(106, cc), ("cc", 106, None))
        self.assertEqual(m(106.0, cc), ("cc", 106, None))
        self.assertEqual(m(107, cc)[0], "excepcion", "centro inexistente: queda vacío y se reporta")
        for n in (0, 32, 108, 109, 230):
            self.assertEqual(m(n, cc | {32})[0], "excepcion")
        for v, esperado in (("D", "D"), ("v", "V"), ("F", "F"), ("Ausente", "A"), ("AUSENTE", "A"), ("Aus", "A"), ("AU", "A"), ("Lic", "LIC"),
                            ("PERMISO", "PER"), ("Sanción", "SAN"), ("SANCION", "SAN"), ("sancion", "SAN"), ("VIAJE", "VJ"), ("Curso", "CAP"),
                            ("Capacitacion", "CAP"), ("Dev", "COMP"), ("Dev8", "COMP"), ("Dev9", "COMP"), ("Compensar", "COMP"), ("Fin", "BAJA")):
            self.assertEqual(m(v, cc), ("estado", esperado, None), v)
        for v in ("P210-RB", "SDJ-M", "RioTinto", "Norlab", "Arizaro", "ARLI", "Arli", "SMG", "GEMAR", "Gemar", "PM", "PM?", "EXAR"):
            self.assertEqual(m(v, cc)[0], "excepcion", v)
        for v in ("x", "c", "`", "?", "z"):
            self.assertEqual(m(v, cc)[0], "ignorar", v)
        for v in ("Ausente Justificado", "Ausente 1/2", "Rosario", "PERMISO, ESTUDIOS MEDICOS"):
            self.assertEqual(m(v, cc), ("estado", "PER", v), "otros textos: Permiso con justificación")
        self.assertEqual(m(None, cc), (None, None, None))
        self.assertEqual(m("  ", cc), (None, None, None))


if __name__ == "__main__":
    unittest.main()
