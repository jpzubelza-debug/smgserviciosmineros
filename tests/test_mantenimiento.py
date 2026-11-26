"""Pruebas de Mantenimiento › Etapa 1 (novedades, OT, app del mecánico, estado y ubicación de equipos).

Se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal (DATA_DIR se fija antes de importar main).

    .venv-1\\Scripts\\python.exe -m unittest tests.test_mantenimiento -v
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
import unittest

if "main" in sys.modules:
    raise unittest.SkipTest("main ya fue importado con otra DATA_DIR; ejecute este módulo por separado.")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_REAL = os.getenv("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db")
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_mt_")
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
import mantenimiento as mt  # noqa: E402
import mantenimiento_preventivo as mpv  # noqa: E402

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

API = "/api/mantenimiento"
VEH = "/api/vehiculos/tipos-equipo"
CONSULTA = ("consulta.mt@test.local", "clave-consulta-1")
OPER, MEC, MEC2 = "M9001", "M9002", "M9003"
PIN = "4826"
CI = "M-33"


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


def _app(legajo):
    c = httpx.Client(base_url=f"http://127.0.0.1:{PUERTO}", follow_redirects=False, timeout=60)
    r = c.post("/operador/api/login", json={"legajo": legajo, "pin": PIN})
    assert r.status_code == 200, r.text
    return c


def _ok(r, codigo=200):
    assert r.status_code == codigo, f"{r.status_code}: {r.text[:800]}"
    return r.json()


def _err(r, codigo):
    assert r.status_code == codigo, f"esperaba {codigo}, vino {r.status_code}: {r.text[:500]}"
    return r.json()["detail"]


class MantenimientoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with _db() as conn:
            for lg, nombre in ((OPER, "PRUEBA OPERADOR"), (MEC, "PRUEBA MECANICO"), (MEC2, "PRUEBA MECANICO DOS")):
                conn.execute("INSERT INTO personal (legajo, nombre, activo) VALUES (?, ?, 'ACTIVO')", (lg, nombre))
                conn.execute("INSERT INTO operador_pines (legajo, pin_hash, actualizado_en) VALUES (?, ?, '2026-01-01')",
                             (lg, main._op_pin_hash(PIN)))
            conn.execute("""INSERT INTO usuarios (nombre_apellido, dni, legajo, correo, password_hash, estado, tipo_usuario, modulos_json,
                paneles_json, acciones_json, bloqueado, intentos_fallidos, password_temporal) VALUES
                ('Consulta Mto', '99999998', '', ?, ?, 'ACTIVO', 'USUARIO', '["mantenimiento"]', ?, ?, 0, 0, 0)""",
                         (CONSULTA[0], hashlib.sha256(CONSULTA[1].encode()).hexdigest(),
                          json.dumps({"mantenimiento": ["novedades", "ordenes", "equipos"]}),
                          json.dumps({"mantenimiento": {"novedades": ["consultar"], "ordenes": ["consultar"], "equipos": ["consultar"]}})))
            try:  # los planes de preventivo que haya en la base copiada no participan de las pruebas
                conn.execute("UPDATE mt_prev_equipos SET activo = 0")
            except sqlite3.OperationalError:
                pass
        cls.c = _cliente()
        cls.op = _app(OPER)
        cls.mec = _app(MEC)

    @classmethod
    def tearDownClass(cls):
        assert os.path.getmtime(DB_REAL) == MTIME_REAL, "¡La base real fue modificada!"

    def test_01_migracion_idempotente(self):
        with _db() as conn:
            mt.migrar(conn)
            mt.migrar(conn)
            tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'mt_%'")}
            self.assertTrue({"mt_novedades", "mt_ordenes", "mt_ot_mecanicos", "mt_ot_eventos", "mt_ot_tiempos",
                             "mt_equipos", "mt_equipo_historial", "mt_config"} <= tablas)
            with self.assertRaises(sqlite3.IntegrityError):  # novedad de equipo sin equipo
                conn.execute("INSERT INTO mt_novedades (tipo, descripcion, reportada_por, creada_en) VALUES ('EQUIPO', 'x', 't', 't')")
        self.assertIn("mantenimiento", main.CONTINGENCY_PANELS)
        self.assertEqual(set(main.CONTINGENCY_PANELS["mantenimiento"]), set(mt.PANELES))

    def test_02_pagina_reemplaza_fleetcare(self):
        r = self.c.get("/mantenimiento")
        self.assertEqual(r.status_code, 200)
        self.assertIn("mt-root", r.text)
        self.assertNotIn("FleetCare", r.text)

    def test_03_circuito_completo(self):
        # 1. El operador ve el estado del equipo y reporta una novedad desde la app
        info = _ok(self.op.get("/operador/api/mt/novedades", params={"ci": CI}))
        self.assertEqual(info["equipo"]["ci"], CI)
        nov = _ok(self.op.post("/operador/api/mt/novedades", json={"tipo": "EQUIPO", "ci": CI, "descripcion": "Pérdida hidráulica en el brazo"}))
        self.assertEqual((nov["estado"], nov["origen"], nov["legajo_reporta"], nov["dias"]), ("ABIERTA", "APP", OPER, 0))
        # 2. Otro día la vuelve a ver: confirma en lugar de duplicar (una vez por día por persona)
        info = _ok(self.op.get("/operador/api/mt/novedades", params={"ci": CI}))
        self.assertIn(nov["id"], [n["id"] for n in info["novedades"]])
        c1 = _ok(self.op.post(f"/operador/api/mt/novedades/{nov['id']}/confirmar", json={}))
        c2 = _ok(self.op.post(f"/operador/api/mt/novedades/{nov['id']}/confirmar", json={}))
        self.assertEqual((c1["confirmaciones"], c2["confirmaciones"]), (1, 1))
        # 3. El aprobador la pasa a OT, asigna mecánico y deja el equipo Funcional
        res = _ok(self.c.post(f"{API}/novedades/{nov['id']}/resolver",
                              json={"decision": "OT", "mecanicos": [MEC], "estado_equipo": "FUNCIONAL"}))
        ot = res["ot"]
        self.assertEqual((res["novedad"]["estado"], ot["estado"], [m["legajo"] for m in ot["mecanicos"]]), ("EN_OT", "ASIGNADA", [MEC]))
        self.assertTrue(ot["numero"].startswith("OT-"))
        self.assertEqual(_ok(self.c.get(f"{API}/equipos/{CI}"))["equipo"]["estado"], "FUNCIONAL")
        _err(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "CERRAR", "resolucion": "x"}), 422)
        # 4. El mecánico la ve en la app, inicia, carga avance y manda la corrección final
        self.assertTrue(_ok(self.mec.get("/operador/api/mt/yo"))["es_mecanico"])
        self.assertFalse(_ok(self.op.get("/operador/api/mt/yo"))["es_mecanico"])
        self.assertEqual([o["id"] for o in _ok(self.mec.get("/operador/api/mt/ordenes"))], [ot["id"]])
        _err(self.op.get(f"/operador/api/mt/ordenes/{ot['id']}"), 403)
        d = _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/iniciar", json={}))
        self.assertEqual((d["estado"], d["cronometro"]), ("EN_CURSO", True))
        d = _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/avance", json={"texto": "Desarmé el cilindro"}))
        _err(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/avance", json={"texto": " "}), 422)
        d = _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/final", json={"texto": "Cambio de retenes"}))
        self.assertEqual((d["estado"], d["cronometro"]), ("EN_REVISION", False))
        # El mecánico ya no puede tocarla ni finalizarla
        _err(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/avance", json={"texto": "otra"}), 422)
        # 5. El administrador la devuelve con observación; el mecánico corrige y vuelve a mandar
        _err(self.c.post(f"{API}/ordenes/{ot['id']}/devolver", json={"observacion": ""}), 422)
        d = _ok(self.c.post(f"{API}/ordenes/{ot['id']}/devolver", json={"observacion": "Falta probar con carga"}))
        self.assertEqual(d["estado"], "EN_CURSO")
        _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/final", json={"texto": "Probado con carga"}))
        # 6. Finaliza: OT cerrada, novedad cerrada, equipo Operativo, historial
        d = _ok(self.c.post(f"{API}/ordenes/{ot['id']}/finalizar", json={}))
        self.assertEqual(d["estado"], "FINALIZADA")
        self.assertEqual(d["novedad"]["estado"], "CERRADA")
        tipos = [e["tipo"] for e in d["eventos"]]
        for t in ("CREADA", "INICIO", "AVANCE", "CORRECCION_FINAL", "DEVOLUCION", "FINALIZADA"):
            self.assertIn(t, tipos)
        h = _ok(self.c.get(f"{API}/equipos/{CI}"))
        self.assertEqual(h["equipo"]["estado"], "OPERATIVO")
        self.assertEqual([c["nuevo"] for c in h["cambios"] if c["campo"] == "ESTADO"][:2], ["OPERATIVO", "FUNCIONAL"])
        self.assertEqual(_ok(self.mec.get("/operador/api/mt/ordenes")), [])

    def test_04_cerrar_sin_ot_y_solo_equipos(self):
        # Las novedades son solo de equipos: una de obra se rechaza (app y web)
        _err(self.op.post("/operador/api/mt/novedades", json={"tipo": "OBRA", "centro_costo_id": "106", "descripcion": "Camino cortado"}), 422)
        _err(self.c.post(f"{API}/novedades", json={"tipo": "OBRA", "centro_costo_id": 106, "descripcion": "Camino cortado"}), 422)
        nov = _ok(self.op.post("/operador/api/mt/novedades", json={"ci": "C-14", "descripcion": "Ruido en el diferencial"}))
        self.assertEqual((nov["tipo"], nov["ci"]), ("EQUIPO", "C-14"))
        _err(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "CERRAR"}), 422)
        res = _ok(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "CERRAR", "resolucion": "Normal en ese modelo"}))
        self.assertEqual((res["novedad"]["estado"], res["ot"]), ("CERRADA", None))
        _err(self.op.post(f"/operador/api/mt/novedades/{nov['id']}/confirmar", json={}), 422)
        mias = _ok(self.op.get("/operador/api/mt/mis-novedades"))
        self.assertIn(nov["id"], [n["id"] for n in mias])

    def test_05_validaciones_de_novedad(self):
        _err(self.op.post("/operador/api/mt/novedades", json={"tipo": "EQUIPO", "ci": "NO-EXISTE", "descripcion": "x"}), 404)
        _err(self.op.post("/operador/api/mt/novedades", json={"tipo": "EQUIPO", "ci": CI, "descripcion": "  "}), 422)
        _err(self.op.post("/operador/api/mt/novedades", json={"tipo": "OTRO", "ci": CI, "descripcion": "x"}), 422)
        r = httpx.get(f"http://127.0.0.1:{PUERTO}/operador/api/mt/novedades", params={"ci": CI})
        self.assertEqual(r.status_code, 401)

    def test_06_estado_y_ubicacion_manual(self):
        ci = "C-09"
        _err(self.c.put(f"{API}/equipos/{ci}/estado", json={"estado": "FUERA_DE_SERVICIO", "motivo": ""}), 422)
        _err(self.c.put(f"{API}/equipos/{ci}/estado", json={"estado": "ROTO", "motivo": "x"}), 422)
        e = _ok(self.c.put(f"{API}/equipos/{ci}/estado", json={"estado": "FUERA_DE_SERVICIO", "motivo": "Motor fundido"}))
        self.assertEqual((e["estado"], e["estado_motivo"]), ("FUERA_DE_SERVICIO", "Motor fundido"))
        # Fuera de servicio no bloquea: la app lo informa para mostrar el aviso
        self.assertEqual(_ok(self.op.get("/operador/api/mt/novedades", params={"ci": ci}))["equipo"]["estado"], "FUERA_DE_SERVICIO")
        u = _ok(self.c.put(f"{API}/equipos/{ci}/ubicacion", json={"centro_costo_id": 106, "motivo": "Trasladado en carretón"}))
        self.assertEqual((u["ubicacion_origen"], u["ubicacion"]), ("MANUAL", "106 - OBRA EXAR - MANT. CAMINO Y REPOS. COMB."))
        # Un parte posterior reemplaza la ubicación manual
        with _db() as conn:
            conn.execute("UPDATE mt_equipos SET ubic_desde = '2020-01-01 00:00:00' WHERE ci = ?", (ci,))
        u = _ok(self.c.get(f"{API}/equipos/{ci}"))["equipo"]
        self.assertIn(u["ubicacion_origen"], ("PARTE", "MANUAL"))
        with _db() as conn:
            hay_parte = conn.execute(
                """SELECT 1 FROM horas_maquinas WHERE UPPER(TRIM(ci)) = ? AND fecha_parte GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
                   AND substr(fecha_parte, 1, 10) BETWEEN '2020-01-02' AND date('now', '-3 hours') LIMIT 1""", (ci,)).fetchone()
        self.assertEqual(u["ubicacion_origen"], "PARTE" if hay_parte else "MANUAL")
        lista = _ok(self.c.get(f"{API}/equipos"))
        with _db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM vehiculos WHERE TRIM(COALESCE(codigo, '')) <> ''").fetchone()[0]
        self.assertEqual(len(lista), total)
        self.assertEqual(next(x for x in lista if x["ci"] == ci)["estado"], "FUERA_DE_SERVICIO")

    def test_07_cronometro_horas_y_costo(self):
        _ok(self.c.put(f"{API}/config", json={"valor_hora_mecanico": "1500"}))
        ot = _ok(self.c.post(f"{API}/ordenes", json={"ci": CI, "titulo": "Service de prueba", "mecanicos": [MEC, MEC2]}))
        self.assertEqual(len(ot["mecanicos"]), 2)
        _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/iniciar", json={}))
        otra = _ok(self.c.post(f"{API}/ordenes", json={"ci": CI, "titulo": "Otra", "mecanicos": [MEC]}))
        _err(self.mec.post(f"/operador/api/mt/ordenes/{otra['id']}/iniciar", json={}), 422)  # un cronómetro a la vez
        with _db() as conn:  # simula 2 horas de trabajo
            conn.execute("UPDATE mt_ot_tiempos SET inicio = datetime(inicio, '-120 minutes') WHERE ot_id = ? AND fin IS NULL", (ot["id"],))
        d = _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/pausar", json={}))
        self.assertFalse(d["cronometro"])
        self.assertAlmostEqual(d["mis_horas"], 2, delta=0.05)
        self.assertNotIn("costo_mano_obra", d, "el mecánico no ve costos")
        w = _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))
        self.assertAlmostEqual(w["costo_mano_obra"], 3000, delta=80)
        d = _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/espera", json={"texto": "Esperando repuesto"}))
        self.assertEqual(d["estado"], "EN_ESPERA")
        _ok(self.c.post(f"{API}/ordenes/{otra['id']}/anular", json={"motivo": "Duplicada"}))
        _err(self.mec.post(f"/operador/api/mt/ordenes/{otra['id']}/iniciar", json={}), 422)
        _err(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/volar", json={}), 422)

    def test_09_funcional_automatico(self):
        ci = "C-10"
        self.assertEqual(_ok(self.c.get(f"{API}/equipos/{ci}"))["equipo"]["estado"], "OPERATIVO")
        n1 = _ok(self.op.post("/operador/api/mt/novedades", json={"tipo": "EQUIPO", "ci": ci, "descripcion": "Luz de freno quemada"}))
        self.assertEqual(_ok(self.c.get(f"{API}/equipos/{ci}"))["equipo"]["estado"], "FUNCIONAL", "una novedad pasa el equipo a Funcional")
        n2 = _ok(self.op.post("/operador/api/mt/novedades", json={"tipo": "EQUIPO", "ci": ci, "descripcion": "Espejo roto"}))
        _ok(self.c.post(f"{API}/novedades/{n1['id']}/resolver", json={"decision": "CERRAR", "resolucion": "Cambiada en obra"}))
        self.assertEqual(_ok(self.c.get(f"{API}/equipos/{ci}"))["equipo"]["estado"], "FUNCIONAL", "queda otra pendiente")
        res = _ok(self.c.post(f"{API}/novedades/{n2['id']}/resolver", json={"decision": "OT", "mecanicos": [MEC]}))
        _ok(self.c.post(f"{API}/ordenes/{res['ot']['id']}/finalizar", json={}))
        self.assertEqual(_ok(self.c.get(f"{API}/equipos/{ci}"))["equipo"]["estado"], "OPERATIVO", "sin pendientes vuelve a Operativo")
        # Fuera de servicio no se toca solo
        _ok(self.c.put(f"{API}/equipos/{ci}/estado", json={"estado": "FUERA_DE_SERVICIO", "motivo": "Motor"}))
        n3 = _ok(self.op.post("/operador/api/mt/novedades", json={"tipo": "EQUIPO", "ci": ci, "descripcion": "Otra"}))
        _ok(self.c.post(f"{API}/novedades/{n3['id']}/resolver", json={"decision": "CERRAR", "resolucion": "x"}))
        self.assertEqual(_ok(self.c.get(f"{API}/equipos/{ci}"))["equipo"]["estado"], "FUERA_DE_SERVICIO")

    def test_10_fotos(self):
        jpg = b"\xff\xd8\xff\xe0" + b"0" * 2000
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 2000
        nov = _ok(self.op.post("/operador/api/mt/novedades", json={"tipo": "EQUIPO", "ci": "C-11", "descripcion": "Rueda pinchada"}))
        r = _ok(self.op.post(f"/operador/api/mt/novedades/{nov['id']}/fotos", files={"foto": ("f.jpg", jpg, "image/jpeg")}))
        self.assertEqual(len(r["fotos"]), 1)
        self.assertEqual(self.c.get(f"{API}/fotos/{r['id']}").content, jpg)
        self.assertEqual(self.op.get(f"/operador/api/mt/fotos/{r['id']}").content, jpg)
        _err(self.op.post(f"/operador/api/mt/novedades/{nov['id']}/fotos", files={"foto": ("f.txt", b"hola", "text/plain")}), 400)
        det = _ok(self.c.get(f"{API}/novedades/{nov['id']}"))
        self.assertEqual([f["id"] for f in det["fotos"]], [r["id"]])
        res = _ok(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "OT", "mecanicos": [MEC]}))
        ot = res["ot"]
        # La ruta de fotos del mecánico no queda tapada por /ordenes/{id}/{accion}
        r = _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/fotos", files={"foto": ("f.jpg", jpg, "image/jpeg")}))
        _err(self.op.post(f"/operador/api/mt/ordenes/{ot['id']}/fotos", files={"foto": ("f.jpg", jpg, "image/jpeg")}), 403)
        r = _ok(self.c.post(f"{API}/ordenes/{ot['id']}/fotos", files={"foto": ("f.png", png, "image/png")}))
        self.assertEqual(len(r["fotos"]), 2)
        d = _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))
        self.assertEqual((len(d["fotos"]), len(d["novedad"]["fotos"])), (2, 1))
        self.assertIn("FOTO", [e["tipo"] for e in d["eventos"]])
        for _ in range(mt.MAX_FOTOS - 2):
            _ok(self.c.post(f"{API}/ordenes/{ot['id']}/fotos", files={"foto": ("f.jpg", jpg, "image/jpeg")}))
        _err(self.c.post(f"{API}/ordenes/{ot['id']}/fotos", files={"foto": ("f.jpg", jpg, "image/jpeg")}), 422)
        with _db() as conn:
            archivos = [f[0] for f in conn.execute("SELECT archivo FROM mt_fotos WHERE ot_id = ?", (ot["id"],))]
        self.assertTrue(all(os.path.exists(os.path.join(TMP_DIR, "Doc_Mantenimiento", a)) for a in archivos))

    def test_11_control_contra_tarja(self):
        ot = _ok(self.c.post(f"{API}/ordenes", json={"ci": "C-12", "titulo": "Control tarja", "mecanicos": [MEC2]}))
        mec2 = _app(MEC2)
        _ok(mec2.post(f"/operador/api/mt/ordenes/{ot['id']}/iniciar", json={}))
        with _db() as conn:
            conn.execute("UPDATE mt_ot_tiempos SET inicio = datetime(inicio, '-60 minutes') WHERE ot_id = ?", (ot["id"],))
        _ok(mec2.post(f"/operador/api/mt/ordenes/{ot['id']}/pausar", json={}))
        c = _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))["control_tarja"]
        self.assertEqual((len(c), c[0]["legajo"], c[0]["alerta"]), (1, MEC2, "SIN_TARJA"))
        self.assertAlmostEqual(c[0]["horas_cronometro"], 1, delta=0.05)
        with _db() as conn:
            fecha = conn.execute("SELECT substr(inicio, 1, 10) FROM mt_ot_tiempos WHERE ot_id = ?", (ot["id"],)).fetchone()[0]
            desc = conn.execute("SELECT id FROM tarja_codigos_estado WHERE codigo = 'D'").fetchone()[0]
            conn.execute("INSERT INTO tarja_registros (legajo, fecha, codigo_estado_id, creado_por, creado_en) VALUES (?, ?, ?, 't', 't')",
                         (MEC2, fecha, desc))
        c = _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))["control_tarja"]
        self.assertEqual((c[0]["alerta"], c[0]["tarja"]), ("NO_TRABAJADO", "D - Descanso"))
        with _db() as conn:
            conn.execute("UPDATE tarja_registros SET codigo_estado_id = NULL, centro_costo_id = 106, horas_efectivas = 10, horas_break = 2 "
                         "WHERE legajo = ? AND fecha = ?", (MEC2, fecha))
        c = _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))["control_tarja"]
        self.assertEqual((c[0]["alerta"], c[0]["horas_tarja"]), (None, 12))
        self.assertTrue(c[0]["tarja"].startswith("106 - "))
        self.assertNotIn("control_tarja", _ok(mec2.get(f"/operador/api/mt/ordenes/{ot['id']}")), "el mecánico no ve el control")

    def test_12_avisos_por_email(self):
        enviados = []

        class SMTPFalso:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, *a):
                pass

            def send_message(self, m):
                enviados.append(m)
        with _db() as conn:
            conn.execute("""INSERT INTO usuarios (nombre_apellido, dni, legajo, correo, password_hash, estado, tipo_usuario, modulos_json,
                paneles_json, acciones_json, bloqueado, intentos_fallidos, password_temporal) VALUES
                ('Aprobador Mto', '99999997', '', 'aprobador.mt@test.local', 'x', 'ACTIVO', 'USUARIO', '["mantenimiento"]', ?, ?, 0, 0, 0)""",
                         (json.dumps({"mantenimiento": ["novedades", "ordenes"]}),
                          json.dumps({"mantenimiento": {"novedades": ["consultar", "aprobar"], "ordenes": ["finalizar"]}})))
        orig_cfg, orig_ssl = main._smtp_config, main.smtplib.SMTP_SSL
        main._smtp_config = lambda: ("smtp@test.local", "clave", "smtp.test.local", 465, "smtp@test.local")
        main.smtplib.SMTP_SSL = SMTPFalso
        try:
            nov = _ok(self.op.post("/operador/api/mt/novedades", json={"tipo": "EQUIPO", "ci": "C-08", "descripcion": "Frenos largos", "prioridad": "URGENTE"}))
            for _ in range(50):
                if enviados:
                    break
                time.sleep(0.1)
            self.assertEqual(len(enviados), 1)
            self.assertIn("aprobador.mt@test.local", enviados[0]["To"])
            self.assertIn(f"#{nov['id']}", enviados[0]["Subject"])
            self.assertIn("URGENTE", enviados[0]["Subject"])
            self.assertIn("Frenos largos", enviados[0].get_body().get_content())
            res = _ok(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "OT", "mecanicos": [MEC]}))
            _ok(self.mec.post(f"/operador/api/mt/ordenes/{res['ot']['id']}/final", json={"texto": "Regulados"}))
            for _ in range(50):
                if len(enviados) > 1:
                    break
                time.sleep(0.1)
            self.assertEqual(len(enviados), 2)
            self.assertIn(res["ot"]["numero"], enviados[1]["Subject"])
            self.assertIn("Regulados", enviados[1].get_body().get_content())
            # Un reporte con varios ítems manda UN solo mail con la lista agrupada por equipo
            rep = _ok(self.op.post("/operador/api/mt/novedades/reporte", json={"items": [
                {"ci": "C-09", "descripcion": "Bocina no suena"}, {"ci": "C-10", "descripcion": "Parabrisas rajado", "prioridad": "URGENTE"}]}))
            for _ in range(50):
                if len(enviados) > 2:
                    break
                time.sleep(0.1)
            time.sleep(0.3)
            self.assertEqual(len(enviados), 3)
            self.assertIn(f"Reporte de novedades #{rep['reporte_id']}: 2 ítems en 2 equipo(s)", enviados[2]["Subject"])
            cuerpo = enviados[2].get_body().get_content()
            self.assertIn("Bocina no suena", cuerpo)
            self.assertIn("Parabrisas rajado (URGENTE)", cuerpo)
        finally:
            main._smtp_config, main.smtplib.SMTP_SSL = orig_cfg, orig_ssl
        with _db() as conn:
            codigos = {f[0] for f in conn.execute("SELECT codigo FROM email_templates WHERE modulo = 'mantenimiento'")}
        self.assertEqual(codigos, {"mt_novedad", "mt_ot_revision", "mt_reporte_novedades", "mt_preventivo"})

    def test_13_ot_exige_al_menos_un_mecanico(self):
        with _db() as conn:
            antes = conn.execute("SELECT COUNT(*) FROM mt_ordenes").fetchone()[0]
        self.assertIn("mecánico", _err(self.c.post(f"{API}/ordenes", json={"ci": "C-15", "titulo": "Sin mecánico"}), 422))
        # La OT es siempre de un equipo: ya no se crea por centro de costo
        self.assertIn("equipo", _err(self.c.post(f"{API}/ordenes", json={"centro_costo_id": 106, "titulo": "Obra", "mecanicos": [MEC]}), 422))
        self.assertIn("equipo", _err(self.c.post(f"{API}/ordenes", json={"titulo": "Sin equipo", "mecanicos": [MEC]}), 422))
        _err(self.c.post(f"{API}/ordenes", json={"ci": "C-15", "titulo": "Sin mecánico", "mecanicos": ["", " "]}), 422)
        nov = _ok(self.op.post("/operador/api/mt/novedades", json={"ci": "C-15", "descripcion": "Prueba sin mecánico"}))
        _err(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "OT", "mecanicos": [], "estado_equipo": "FUERA_DE_SERVICIO"}), 422)
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mt_ordenes").fetchone()[0], antes, "no se creó ninguna OT")
        self.assertEqual(_ok(self.c.get(f"{API}/novedades/{nov['id']}"))["estado"], "ABIERTA")
        self.assertEqual(_ok(self.c.get(f"{API}/equipos/C-15"))["equipo"]["estado"], "FUNCIONAL", "el rechazo deshace también el cambio de estado")
        ot = _ok(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "OT", "mecanicos": [MEC]}))["ot"]
        _err(self.c.put(f"{API}/ordenes/{ot['id']}/mecanicos", json={"mecanicos": []}), 422)
        self.assertEqual([m["legajo"] for m in _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))["mecanicos"]], [MEC])

    # ------------------------------------------------------------------ check list (etapa 2)
    def _ck_respuestas(self, armado, especiales=None):
        """Bueno en todo, salvo lo indicado en especiales {texto_item: (valor, comentario)}."""
        especiales = especiales or {}
        resp = {}
        for s in armado["secciones"]:
            for it in s["items"]:
                valor, com = especiales.get(it["texto"], ("VIGENTE" if it["tipo"] == "VIGENCIA" else "BUENO", ""))
                resp[it["clave"]] = {"valor": valor, "comentario": com}
        return resp

    def _ck_enviar(self, ci, armado, especiales=None, fotos=None, extra=None):
        jpg = b"\xff\xd8\xff\xe0" + b"1" * 1500
        datos = {"ci": ci, "horometro": "8450", "operador": "PRUEBA", "respuestas": self._ck_respuestas(armado, especiales), **(extra or {})}
        archivos = [("datos", (None, json.dumps(datos)))]
        for s in armado["secciones"]:
            for it in s["items"]:
                if it["texto"] in (fotos or []):
                    archivos.append((f"foto__{it['clave']}", ("f.jpg", jpg, "image/jpeg")))
        return self.c.post(f"{API}/checklist", files=archivos)

    def test_14_checklist_plantilla_y_tipos(self):
        p = _ok(self.c.get(f"{API}/plantilla"))
        self.assertEqual(len(p["modulos"]), 27)
        self.assertEqual(len(p["tipos"]), 19)
        codigos = {m["codigo"]: m for m in p["modulos"]}
        self.assertEqual(codigos["motor"]["capa"], "BASE")
        self.assertEqual((codigos["kit_vial"]["capa"], codigos["kit_vial"]["familia"]), ("FAMILIA", "VIAL"))
        self.assertTrue(codigos["neumaticos"]["es_neumaticos"])
        with _db() as conn:
            mt.migrar(conn)  # idempotente: no vuelve a sembrar
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mt_ck_modulos").fetchone()[0], 27)
        eqs = {e["ci"]: e for e in _ok(self.c.get(VEH))["equipos"]}
        nombres = {t["id"]: t["nombre"] for t in p["tipos"]}
        self.assertEqual(nombres[eqs["M-33"]["tipo_equipo_id"]], "Excavadora")
        self.assertEqual(nombres[eqs["C-09"]["tipo_equipo_id"]], "Regador")
        self.assertTrue(eqs["M-33"]["estandarizado"], "«Excavadora» ya coincide con el catálogo")
        self.assertFalse(eqs["C-08"]["estandarizado"])
        # Sin tipo estándar en vehiculos.tipo no se arma: se corrige en Logística › Vehículos
        self.assertIn("Logística › Vehículos", _err(self.c.get(f"{API}/checklist/armar/C-08"), 422))
        r = _ok(self.c.post(VEH + "/estandarizar", json={}))
        self.assertGreater(r["estandarizados"], 50)
        pendientes = [e for e in _ok(self.c.get(VEH))["equipos"] if not e["estandarizado"]]
        self.assertEqual([e["tipo"] for e in pendientes], ["CHASIS C/CABINA DORMITORIO"], "solo queda el que no tiene propuesta")
        m33 = next(e for e in _ok(self.c.get(VEH))["equipos"] if e["ci"] == "M-33")
        self.assertEqual((m33["estandarizado"], m33["tipo"], m33["tipo_original"]), (True, "Excavadora", None))
        with _db() as conn:
            c08 = conn.execute("SELECT tipo, tipo_original, raw_json FROM vehiculos WHERE codigo = 'C-08'").fetchone()
        self.assertEqual((c08["tipo"], c08["tipo_original"]), ("Tolva", "camion con tolva 6x4 17 m3"))
        self.assertEqual(json.loads(c08["raw_json"])["tipo"], "Tolva")

    def test_15_checklist_armado_por_capas(self):
        _ok(self.c.post(VEH + "/estandarizar", json={}))
        tipos = {t["nombre"]: t for t in _ok(self.c.get(f"{API}/plantilla"))["tipos"]}
        a = _ok(self.c.get(f"{API}/checklist/armar/M-33"))
        mods = [s["modulo"] for s in a["secciones"]]
        self.assertEqual((a["tipo_equipo"], a["familia"]), ("Excavadora", "MAQUINARIA"))
        self.assertIn("Motor", mods)
        self.assertIn("Maquinaria vial", mods)
        self.assertEqual(mods[-3:], ["Tren rodante", "Aguilón", "Balde"])
        self.assertNotIn("Neumáticos", mods)
        self.assertNotIn("Kit vial", mods)
        # Camioneta: base + vial (documentación, kit) + neumáticos 4 + 2 auxilios + tracción; override por equipo
        _ok(self.c.put(f"{VEH}/equipos/C-16", json={"tipo_equipo_id": tipos["Camioneta"]["id"]}))
        a = _ok(self.c.get(f"{API}/checklist/armar/C-16"))
        neu = next(s for s in a["secciones"] if s["modulo"] == "Neumáticos")["items"]
        self.assertEqual([i["texto"] for i in neu], ["Posición 1", "Posición 2", "Posición 3", "Posición 4", "Auxilio 1", "Auxilio 2"])
        self.assertIn("Documentación", [s["modulo"] for s in a["secciones"]])
        modulo_semi = next(m for m in _ok(self.c.get(f"{API}/plantilla"))["modulos"] if m["codigo"] == "semirremolque")
        _ok(self.c.put(f"{VEH}/equipos/C-16", json={"tipo_equipo_id": tipos["Camioneta"]["id"], "neumaticos": 6, "auxilios": 1,
                                                                   "modulos": {str(modulo_semi["id"]): "SUMAR"}}))
        a = _ok(self.c.get(f"{API}/checklist/armar/C-16"))
        self.assertEqual(len(next(s for s in a["secciones"] if s["modulo"] == "Neumáticos")["items"]), 7)
        self.assertIn("Semirremolque", [s["modulo"] for s in a["secciones"]])
        # Remolque: sin la Base
        _ok(self.c.put(f"{VEH}/equipos/C-17", json={"tipo_equipo_id": tipos["Chulengo"]["id"]}))
        mods = [s["modulo"] for s in _ok(self.c.get(f"{API}/checklist/armar/C-17"))["secciones"]]
        self.assertEqual(mods[:3], ["Remolque", "Neumáticos", "Tanque de combustible"])  # la plantilla real puede sumar módulos
        self.assertNotIn("Motor", mods, "un remolque no lleva la Base")

    def test_16_checklist_carga_reglas_y_novedades(self):
        _ok(self.c.post(VEH + "/estandarizar", json={}))
        ci = "M-33"
        a = _ok(self.c.get(f"{API}/checklist/armar/{ci}"))
        # Faltan respuestas
        datos = {"ci": ci, "respuestas": {}}
        self.assertIn("Faltan", _err(self.c.post(f"{API}/checklist", files=[("datos", (None, json.dumps(datos)))]), 422))
        # Malo sin comentario ni foto; N/A en un ítem que no es opcional; nivel de aceite pide foto siempre
        msg = _err(self._ck_enviar(ci, a, {"Pérdida hidráulica": ("MALO", ""), "Pintura": ("NA", "")}), 422)
        self.assertIn("falta el comentario", msg)
        self.assertIn("no admite N/A", msg)
        self.assertIn("Nivel de aceite: falta la foto", msg)
        with _db() as conn:
            antes = conn.execute("SELECT COUNT(*) FROM mt_checklists").fetchone()[0]
        self.assertEqual(antes, 0, "un check list rechazado no se guarda")
        especiales = {"Pérdida hidráulica": ("MALO", "Pierde aceite el cilindro"), "Cadena": ("REGULAR", "Algo floja"),
                      "Aire acondicionado": ("NA", "")}
        r = _ok(self._ck_enviar(ci, a, especiales, fotos=["Pérdida hidráulica", "Cadena", "Nivel de aceite", "Nivel de refrigerante"]))
        self.assertEqual((r["resultado"], r["malos"], r["regulares"], r["novedades"]), ("NO_APTO", 1, 1, 1))
        self.assertEqual(r["total_items"], sum(len(s["items"]) for s in a["secciones"]))
        malo = next(x for x in r["respuestas"] if x["item"] == "Pérdida hidráulica")
        self.assertTrue(malo["tiene_foto"] and malo["novedad_id"])
        self.assertEqual(self.c.get(f"{API}/checklist/respuestas/{malo['id']}/foto").status_code, 200)
        nov = _ok(self.c.get(f"{API}/novedades/{malo['novedad_id']}"))
        self.assertEqual((nov["prioridad"], nov["ci"], len(nov["fotos"])), ("URGENTE", ci, 1))
        self.assertTrue(nov["descripcion"].startswith("Check list · Aguilón · Pérdida hidráulica"))
        self.assertEqual(_ok(self.c.get(f"{API}/equipos/{ci}"))["equipo"]["estado"], "FUNCIONAL")
        regular = next(x for x in r["respuestas"] if x["item"] == "Cadena")
        self.assertIsNone(regular["novedad_id"], "Regular no abre novedad")
        # Segundo check list con el mismo Malo: no repite la novedad, la confirma
        r2 = _ok(self._ck_enviar(ci, a, {"Pérdida hidráulica": ("MALO", "Sigue perdiendo")}, fotos=["Pérdida hidráulica", "Nivel de aceite", "Nivel de refrigerante"]))
        self.assertEqual(next(x for x in r2["respuestas"] if x["item"] == "Pérdida hidráulica")["novedad_id"], malo["novedad_id"])
        nov = _ok(self.c.get(f"{API}/novedades/{malo['novedad_id']}"))
        self.assertEqual(nov["confirmaciones"], 1)
        self.assertEqual(len(nov["fotos"]), 2)
        lista = _ok(self.c.get(f"{API}/checklist", params={"resultado": "NO_APTO"}))
        self.assertEqual({c["id"] for c in lista}, {r["id"], r2["id"]})

    def test_17_checklist_editar_plantilla_no_cambia_historicos(self):
        _ok(self.c.post(VEH + "/estandarizar", json={}))
        p = _ok(self.c.get(f"{API}/plantilla"))
        balde = next(m for m in p["modulos"] if m["codigo"] == "balde")
        unas = next(i for i in balde["items"] if i["texto"] == "Uñas")
        a = _ok(self.c.get(f"{API}/checklist/armar/M-33"))
        ck = _ok(self._ck_enviar("M-33", a, fotos=["Nivel de aceite", "Nivel de refrigerante"]))
        self.assertEqual(ck["resultado"], "APTO")
        _ok(self.c.put(f"{API}/plantilla/items/{unas['id']}", json={"texto": "Uñas y dientes", "tipo": "ESTADO", "critico": True, "activo": True}))
        _ok(self.c.post(f"{API}/plantilla/items", json={"modulo_id": balde["id"], "texto": "Pasador de uñas", "tipo": "ESTADO"}))
        textos = [i["texto"] for i in next(s for s in _ok(self.c.get(f"{API}/checklist/armar/M-33"))["secciones"] if s["modulo"] == "Balde")["items"]]
        self.assertIn("Uñas y dientes", textos)
        self.assertIn("Pasador de uñas", textos)
        _ok(self.c.put(f"{API}/plantilla/items/{unas['id']}", json={"texto": "Uñas y dientes", "tipo": "ESTADO", "activo": False}))
        textos = [i["texto"] for i in next(s for s in _ok(self.c.get(f"{API}/checklist/armar/M-33"))["secciones"] if s["modulo"] == "Balde")["items"]]
        self.assertNotIn("Uñas y dientes", textos)
        viejo = _ok(self.c.get(f"{API}/checklist/{ck['id']}"))
        self.assertIn("Uñas", [x["item"] for x in viejo["respuestas"]], "el check list viejo conserva el texto de ese día")
        neu = next(m for m in p["modulos"] if m["codigo"] == "neumaticos")
        _err(self.c.post(f"{API}/plantilla/items", json={"modulo_id": neu["id"], "texto": "x"}), 422)
        _ok(self.c.post(f"{API}/plantilla/modulos", json={"nombre": "Grúa", "capa": "MODULO"}))
        self.assertIn("Grúa", [m["nombre"] for m in _ok(self.c.get(f"{API}/plantilla"))["modulos"]])

    def test_19_vehiculos_tipo_estandar(self):
        _ok(self.c.post(VEH + "/estandarizar", json={}))
        # Alta / edición desde Vehículos: solo tipos del catálogo (se normaliza la escritura)
        r = _ok(self.c.put("/vehiculos/C-18", json={"tipo": "Cualquier cosa"}))
        self.assertIn("no está en el catálogo", r["error"])
        _ok(self.c.put("/vehiculos/C-18", json={"tipo": "tolva"}))
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT tipo FROM vehiculos WHERE codigo = 'C-18'").fetchone()[0], "Tolva")
        r = _ok(self.c.post("/vehiculos", json={"codigo": "ZZ-99", "tipo": "Nave espacial"}))
        self.assertIn("no está en el catálogo", r["error"])
        # Un vehículo con texto viejo se puede editar en otros campos sin tocar el tipo
        with _db() as conn:
            conn.execute("UPDATE vehiculos SET tipo = 'algo viejo' WHERE codigo = 'C-19'")
        self.assertNotIn("error", _ok(self.c.put("/vehiculos/C-19", json={"sector": "TALLER", "tipo": "algo viejo"})))
        self.assertIn("Logística › Vehículos", _err(self.c.get(f"{API}/checklist/armar/C-19"), 422))
        # Renombrar un tipo del catálogo actualiza los vehículos que lo tienen
        cat = _ok(self.c.get(VEH))
        tolva = next(t for t in cat["tipos"] if t["nombre"] == "Tolva")
        _ok(self.c.put(f"{VEH}/tipos/{tolva['id']}", json={**{k: tolva[k] for k in ("familia", "neumaticos", "auxilios", "palabras_clave")},
                                                           "nombre": "Camión tolva", "modulos": tolva["modulos"]}))
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vehiculos WHERE tipo = 'Tolva'").fetchone()[0], 0)
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM vehiculos WHERE tipo = 'Camión tolva'").fetchone()[0], 0)
        self.assertEqual(_ok(self.c.get(f"{API}/checklist/armar/C-18"))["tipo_equipo"], "Camión tolva")
        _err(self.c.post(VEH + "/tipos", json={"nombre": "camión TOLVA", "familia": "VIAL"}), 422)
        _ok(self.c.put(f"{VEH}/tipos/{tolva['id']}", json={**{k: tolva[k] for k in ("familia", "neumaticos", "auxilios", "palabras_clave")},
                                                           "nombre": "Tolva", "modulos": tolva["modulos"]}))

    def test_18_checklist_permisos(self):
        c = _cliente(*CONSULTA)  # sin panel checklist ni configuración
        self.assertEqual(c.get(f"{API}/checklist").status_code, 403)
        self.assertEqual(c.get(f"{API}/checklist/armar/M-33").status_code, 403)
        self.assertEqual(c.get(f"{API}/plantilla").status_code, 403)
        self.assertEqual(c.post(f"{API}/plantilla/items", json={"modulo_id": 1, "texto": "x"}).status_code, 403)
        self.assertEqual(c.get(VEH).status_code, 403, "sin panel Logística › Vehículos")
        self.assertEqual(c.post(VEH + "/estandarizar", json={}).status_code, 403)
        self.assertEqual(c.post(VEH + "/tipos", json={"nombre": "x", "familia": "VIAL"}).status_code, 403)

    def test_20_reporte_con_varios_items_y_parecidas(self):
        items = [{"ci": "C-28", "descripcion": "Problemas de embrague"},
                 {"ci": "C-28", "descripcion": "Pinchadura de cubierta 1", "prioridad": "URGENTE"},
                 {"ci": "M-40", "descripcion": "Cilindro del balde con vástago lastimado, pierde aceite"}]
        r = _ok(self.op.post("/operador/api/mt/novedades/reporte", json={"items": items}))
        ids = [n["id"] for n in r["novedades"]]
        self.assertEqual(len(ids), 3)
        self.assertEqual(r["reporte_id"], ids[0])
        self.assertTrue(all(n["reporte_id"] == ids[0] and n["origen"] == "APP" for n in r["novedades"]))
        self.assertEqual(r["novedades"][1]["prioridad"], "URGENTE")
        det = _ok(self.c.get(f"{API}/novedades/{ids[1]}"))
        self.assertEqual({n["id"] for n in det["reporte"]}, {ids[0], ids[2]})
        self.assertEqual(_ok(self.op.get("/operador/api/mt/novedades", params={"ci": "M-40"}))["equipo"]["estado"], "FUNCIONAL")
        # Todo o nada: si un ítem falla no se carga ninguno y el mensaje dice cuál
        with _db() as conn:
            antes = conn.execute("SELECT COUNT(*) FROM mt_novedades").fetchone()[0]
        msg = _err(self.c.post(f"{API}/novedades/reporte", json={"items": [{"ci": "C-28", "descripcion": "Algo"}, {"ci": "C-28", "descripcion": "  "}]}), 422)
        self.assertIn("Ítem 2", msg)
        self.assertIn("Ítem 1", _err(self.c.post(f"{API}/novedades/reporte", json={"items": [{"ci": "NO-EXISTE", "descripcion": "x"}]}), 404))
        _err(self.c.post(f"{API}/novedades/reporte", json={"items": []}), 422)
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mt_novedades").fetchone()[0], antes)
        # Parecidas: avisa con otra cubierta del mismo equipo; no con otra falla ni con otro equipo
        par = _ok(self.op.get("/operador/api/mt/novedades/parecidas", params={"ci": "C-28", "descripcion": "pinchadura de la cubierta 2"}))
        self.assertEqual([p["id"] for p in par], [ids[1]])
        self.assertEqual([p["id"] for p in _ok(self.c.get(f"{API}/novedades/parecidas", params={"ci": "c-28", "descripcion": "Cubiertas pinchadas"}))], [ids[1]])
        self.assertEqual(_ok(self.c.get(f"{API}/novedades/parecidas", params={"ci": "C-28", "descripcion": "Luces reglamentarias no encienden"})), [])
        self.assertEqual(_ok(self.c.get(f"{API}/novedades/parecidas", params={"ci": "M-40", "descripcion": "Pinchadura de cubierta"})), [])
        self.assertEqual(_ok(self.c.get(f"{API}/novedades/parecidas", params={"ci": "C-28", "descripcion": ""})), [])
        # No bloquea: la similar se carga igual (un ítem solo no forma reporte)
        sola = _ok(self.c.post(f"{API}/novedades/reporte", json={"items": [{"ci": "C-28", "descripcion": "Pinchadura de cubierta 2"}]}))
        self.assertIsNone(sola["reporte_id"])
        self.assertEqual(sola["novedades"][0]["origen"], "WEB")
        # Las cerradas hace poco también avisan (puede ser la misma falla que volvió)
        _ok(self.c.post(f"{API}/novedades/{ids[0]}/resolver", json={"decision": "CERRAR", "resolucion": "Regulado"}))
        par = _ok(self.c.get(f"{API}/novedades/parecidas", params={"ci": "C-28", "descripcion": "Volvió el problema de embrague"}))
        self.assertEqual([(p["id"], p["estado"]) for p in par], [(ids[0], "CERRADA")])

    def test_21_preventivo_vencimientos_y_ot(self):
        P = f"{API}/preventivo"
        r = _ok(self.c.post(f"{P}/planes", json={"nombre": "Programado simple", "intervalo_hs": "250", "intervalo_dias": 180,
                                                 "tareas": ["Aceite de motor", "Filtro de aceite", "  "]}))
        plan = next(p for p in r["planes"] if p["id"] == r["plan_id"])
        self.assertEqual((plan["tareas"], plan["aviso_pct"], plan["equipos"]), (["Aceite de motor", "Filtro de aceite"], 10, 0))
        self.assertIn("Ya existe", _err(self.c.post(f"{P}/planes", json={"nombre": "programado SIMPLE", "intervalo_hs": 100}), 422))
        self.assertIn("al menos un intervalo", _err(self.c.post(f"{P}/planes", json={"nombre": "Sin intervalo"}), 422))
        with _db() as conn:
            lec = mpv.lecturas(conn)
        hs_c28 = lec["C-28"]["hs"]
        # C-28: último service hace 272 h (intervalo 250) → vencido por horas
        _ok(self.c.post(f"{P}/planes/{plan['id']}/equipos", json={"cis": ["C-28"], "base": "MANUAL", "fecha": "2026-07-01", "hs": hs_c28 - 272}))
        _ok(self.c.post(f"{P}/planes/{plan['id']}/equipos", json={"cis": ["M-26"]}))  # desde hoy con la lectura actual
        _err(self.c.post(f"{P}/planes/{plan['id']}/equipos", json={"cis": ["C-29"], "base": "MANUAL", "fecha": "2999-01-01", "hs": 1}), 422)
        _err(self.c.post(f"{P}/planes/{plan['id']}/equipos", json={"cis": []}), 422)
        est = {x["ci"]: x for x in _ok(self.c.get(P))}
        self.assertEqual((est["C-28"]["estado"], est["C-28"]["vence_por"], est["C-28"]["restante"]), ("VENCIDO", "hs", -22))
        self.assertEqual(est["M-26"]["estado"], "AL_DIA")
        self.assertEqual(_ok(self.c.get(f"{API}/resumen"))["preventivo"]["vencidos"], 1)
        # OT preventiva: exige mecánico, lleva las tareas y la lectura; una sola abierta por equipo y plan
        asig = est["C-28"]["id"]
        _err(self.c.post(f"{P}/asignaciones/{asig}/ot", json={"mecanicos": []}), 422)
        ot = _ok(self.c.post(f"{P}/asignaciones/{asig}/ot", json={"mecanicos": [MEC]}))
        self.assertEqual((ot["tipo"], ot["tareas"], ot["lectura_hs"]), ("PREVENTIVA", ["Aceite de motor", "Filtro de aceite"], hs_c28))
        self.assertIn(ot["numero"], _err(self.c.post(f"{P}/asignaciones/{asig}/ot", json={"mecanicos": [MEC]}), 422))
        self.assertEqual(_ok(self.c.get(P, params={"ci": "C-28"}))[0]["ot"]["numero"], ot["numero"])
        _err(self.c.delete(f"{P}/asignaciones/{asig}"), 422)  # con OT abierta no se quita
        mec = _ok(self.mec.get(f"/operador/api/mt/ordenes/{ot['id']}"))
        self.assertEqual(mec["tareas"], ["Aceite de motor", "Filtro de aceite"])
        self.assertNotIn("costo_total", mec)
        # Al finalizar, el plan vuelve a contar desde la lectura del service
        _ok(self.c.post(f"{API}/ordenes/{ot['id']}/finalizar", json={"lectura_hs": hs_c28 - 2}))
        det = _ok(self.c.get(f"{P}/asignaciones/{asig}"))
        self.assertEqual((det["estado"], det["base_hs"], det["base_origen"], det["ot"]), ("AL_DIA", hs_c28 - 2, ot["numero"], None))
        self.assertEqual(det["historial"][0]["ot_numero"], ot["numero"])
        # Service hecho fuera del sistema; una lectura menor que la base pide revisar
        det = _ok(self.c.post(f"{P}/asignaciones/{asig}/realizado", json={"fecha": "2026-08-01", "hs": hs_c28 + 500, "nota": "Taller externo"}))
        self.assertEqual((det["estado"], det["base_origen"], det["historial"][0]["nota"]), ("REVISAR", "MANUAL", "Taller externo"))
        _ok(self.c.delete(f"{P}/asignaciones/{asig}"))
        self.assertNotIn("C-28", {x["ci"] for x in _ok(self.c.get(P))})
        # Plan inactivo: sus equipos salen de los vencimientos
        _ok(self.c.put(f"{P}/planes/{plan['id']}", json={**plan, "activo": False}))
        self.assertNotIn("M-26", {x["ci"] for x in _ok(self.c.get(P))})

    def test_22_repuestos_de_la_ot(self):
        nov = _ok(self.op.post("/operador/api/mt/novedades", json={"ci": "C-29", "descripcion": "Cambio de cubiertas traseras"}))
        ot = _ok(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "OT", "mecanicos": [MEC]}))["ot"]
        with _db() as conn:
            det_id = conn.execute("SELECT id FROM solicitud_compra_detalle ORDER BY id LIMIT 1").fetchone()[0]
            conn.execute("UPDATE solicitud_compra_detalle SET id_vehiculo = 'C-29' WHERE id = ?", (det_id,))
        sug = _ok(self.c.get(f"{API}/ordenes/{ot['id']}/repuestos/sugerencias"))
        self.assertEqual([c["solicitud_detalle_id"] for c in sug["compras"]], [det_id])
        _ok(self.c.post(f"{API}/ordenes/{ot['id']}/repuestos", json={"origen": "COMPRA", "descripcion": "Cubierta 1100x20", "cantidad": "2",
                                                                    "costo_unitario": "150000", "solicitud_detalle_id": det_id, "referencia": "Solicitud 1"}))
        d = _ok(self.c.post(f"{API}/ordenes/{ot['id']}/repuestos", json={"origen": "OTRO", "descripcion": "Válvula de aire", "cantidad": 1}))
        self.assertEqual((d["costo_repuestos"], d["repuestos_sin_costo"], len(d["repuestos"])), (300000, 1, 2))
        self.assertEqual(d["costo_total"], round(d["costo_mano_obra"] + 300000, 2))
        self.assertEqual(d["eventos"][0]["tipo"], "REPUESTO")
        self.assertTrue(_ok(self.c.get(f"{API}/ordenes/{ot['id']}/repuestos/sugerencias"))["compras"][0]["vinculado"])
        _err(self.c.post(f"{API}/ordenes/{ot['id']}/repuestos", json={"descripcion": "x", "cantidad": 0}), 422)
        _err(self.c.post(f"{API}/ordenes/{ot['id']}/repuestos", json={"origen": "REGALO", "descripcion": "x", "cantidad": 1}), 422)
        _err(self.c.post(f"{API}/ordenes/{ot['id']}/repuestos", json={"origen": "ALMACEN", "descripcion": "x", "cantidad": 1, "producto_id": 999999}), 404)
        lista = {o["id"]: o for o in _ok(self.c.get(f"{API}/ordenes", params={"estado": "TODAS"}))}
        self.assertEqual(lista[ot["id"]]["costo_repuestos"], 300000)
        mec = _ok(self.mec.get(f"/operador/api/mt/ordenes/{ot['id']}"))
        self.assertNotIn("costo_repuestos", mec)
        self.assertEqual(mec["repuestos"][0], {"descripcion": "Cubierta 1100x20", "cantidad": 2, "unidad": None})
        sin_costo = next(r for r in d["repuestos"] if r["costo_unitario"] is None)
        d = _ok(self.c.delete(f"{API}/ordenes/{ot['id']}/repuestos/{sin_costo['id']}"))
        self.assertEqual((len(d["repuestos"]), d["repuestos_sin_costo"]), (1, 0))
        _ok(self.c.post(f"{API}/ordenes/{ot['id']}/finalizar", json={}))
        _err(self.c.post(f"{API}/ordenes/{ot['id']}/repuestos", json={"descripcion": "Tarde", "cantidad": 1}), 422)

    def test_23_control_del_horometro(self):
        ci = "C-16"
        with _db() as conn:
            anterior = mpv._lectura(conn, ci)["hs"]
            cc = conn.execute("SELECT id, nombre FROM centros_costos WHERE COALESCE(activo, 1) = 1 ORDER BY id LIMIT 1").fetchone()
            cli = conn.execute("SELECT codigo, razon_social FROM clientes WHERE TRIM(COALESCE(codigo, '')) <> '' ORDER BY codigo LIMIT 1").fetchone()
        self.assertIsNotNone(anterior)
        hoy = mt.hoy().isoformat()
        n = [0]

        def parte(hs_ini, hs_fin):
            n[0] += 1
            return self.c.post("/operaciones/horas-maquinas", json={
                "ci": ci, "fecha_parte": hoy, "nro_parte": f"HOR-{n[0]}", "legajo_empleado": OPER, "maquinista": "PRUEBA OPERADOR",
                "obra": f"{cc['id']} - {cc['nombre'].strip()}", "cliente": f"{cli['codigo'].strip()} - {cli['razon_social'].strip()}",
                "descripcion_trabajos": "Prueba de horómetro", "hs_inicio": str(int(hs_ini)), "hs_final": str(int(hs_fin))})
        # Jornada imposible: se bloquea
        self.assertIn("24", _err(parte(anterior, anterior + 30), 400))
        # Retroceso y salto: se cargan igual y quedan para revisar
        _ok(parte(anterior - 100, anterior - 90))
        _ok(parte(anterior + 200, anterior + 205))
        _ok(parte(anterior + 205, anterior + 210))  # normal: no se registra
        obs = [o for o in _ok(self.c.get(f"{API}/preventivo/lecturas", params={"ci": ci}))]
        self.assertEqual([o["tipo"] for o in obs], ["HOROMETRO_SALTO", "HOROMETRO_RETROCESO"])
        self.assertTrue(all(o["parte_id"] and o["origen"] == "ESCRITORIO" for o in obs))
        self.assertGreaterEqual(_ok(self.c.get(f"{API}/resumen"))["preventivo"]["lecturas_a_revisar"], 2)
        _ok(self.c.post(f"{API}/preventivo/lecturas/{obs[0]['id']}/revisada", json={"nota": "Horómetro cambiado"}))
        _err(self.c.post(f"{API}/preventivo/lecturas/{obs[0]['id']}/revisada", json={}), 422)
        self.assertEqual(len(_ok(self.c.get(f"{API}/preventivo/lecturas", params={"ci": ci}))), 1)
        todas = _ok(self.c.get(f"{API}/preventivo/lecturas", params={"ci": ci, "todas": "true"}))
        self.assertEqual(next(o for o in todas if o["id"] == obs[0]["id"])["nota"], "Horómetro cambiado")
        # La lectura actual del equipo es la del último parte
        with _db() as conn:
            self.assertEqual(mpv._lectura(conn, ci)["hs"], anterior + 210)

    def _plan_con_ot(self, nombre, ci, tareas):
        P = f"{API}/preventivo"
        r = _ok(self.c.post(f"{P}/planes", json={"nombre": nombre, "intervalo_dias": 30, "tareas": tareas}))
        _ok(self.c.post(f"{P}/planes/{r['plan_id']}/equipos", json={"cis": [ci]}))
        asig = next(x for x in _ok(self.c.get(P, params={"ci": ci})) if x["plan_id"] == r["plan_id"])
        return _ok(self.c.post(f"{P}/asignaciones/{asig['id']}/ot", json={"mecanicos": [MEC]}))

    def test_24_tareas_tildables(self):
        ot = self._plan_con_ot("Tareas (test)", "C-30", ["Aceite", "Filtro de aceite", "Filtro de aire"])
        self.assertEqual([(t["orden"], t["hecha"]) for t in ot["tareas_estado"]], [(1, 0), (2, 0), (3, 0)])
        url = f"/operador/api/mt/ordenes/{ot['id']}/tareas"
        tareas = _ok(self.mec.post(f"{url}/1", json={"hecha": True}))
        self.assertEqual((tareas[0]["hecha"], tareas[0]["hecha_por"]), (1, "PRUEBA MECANICO"))
        _err(_app(MEC2).post(f"{url}/2", json={"hecha": True}), 403)  # no es su OT
        _err(self.mec.post(f"{url}/9", json={"hecha": True}), 404)
        _ok(self.c.put(f"{API}/ordenes/{ot['id']}/tareas/2", json={"hecha": True}))
        _ok(self.c.put(f"{API}/ordenes/{ot['id']}/tareas/2", json={"hecha": False}))
        _ok(self.c.put(f"{API}/ordenes/{ot['id']}/tareas/3", json={"hecha": True}))
        # La corrección final no se bloquea, pero deja constancia de cuántas se hicieron
        _ok(self.mec.post(f"/operador/api/mt/ordenes/{ot['id']}/final", json={"texto": "Service hecho"}))
        det = _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))
        self.assertIn("(tareas hechas: 2 de 3)", next(e["texto"] for e in det["eventos"] if e["tipo"] == "CORRECCION_FINAL"))
        _err(self.mec.post(f"{url}/2", json={"hecha": True}), 422)  # en revisión ya no se toca desde la app
        _ok(self.c.post(f"{API}/ordenes/{ot['id']}/finalizar", json={"observacion": "Todo ok"}))
        _err(self.c.put(f"{API}/ordenes/{ot['id']}/tareas/2", json={"hecha": True}), 422)
        # El mecánico la sigue viendo en su historial (solo lectura, sin costos) y ya no en las activas
        self.assertNotIn(ot["id"], [x["id"] for x in _ok(self.mec.get("/operador/api/mt/ordenes"))])
        hist = {x["id"]: x for x in _ok(self.mec.get("/operador/api/mt/historial"))}
        self.assertEqual(hist[ot["id"]]["estado"], "FINALIZADA")
        self.assertNotIn("costo_total", hist[ot["id"]])
        self.assertIn("mis_horas", hist[ot["id"]])
        det = _ok(self.mec.get(f"/operador/api/mt/ordenes/{ot['id']}"))
        self.assertEqual((det["estado"], next(e["texto"] for e in det["eventos"] if e["tipo"] == "FINALIZADA")), ("FINALIZADA", "Todo ok"))
        self.assertNotIn(ot["id"], [x["id"] for x in _ok(_app(MEC2).get("/operador/api/mt/historial"))])

    def test_25_pedido_a_almacen_descuenta_stock(self):
        with _db() as conn:
            pid = conn.execute("INSERT INTO productos (codigo, descripcion, tipo_control) VALUES ('MT-FIL-01', 'Filtro de aceite (test)', 'CONSUMIBLE')").lastrowid
            sid = conn.execute("INSERT INTO productos (codigo, descripcion, tipo_control) VALUES ('MT-BAT-01', 'Batería (test)', 'SERIALIZADO')").lastrowid
            for producto in (pid, sid):
                conn.execute("INSERT INTO stock (producto_id, producto_codigo, stock_actual) VALUES (?, 'x', 10)", (producto,))
        nov = _ok(self.op.post("/operador/api/mt/novedades", json={"ci": "C-31", "descripcion": "Pierde aceite el filtro"}))
        ot = _ok(self.c.post(f"{API}/novedades/{nov['id']}/resolver", json={"decision": "OT", "mecanicos": [MEC]}))["ot"]
        R = f"{API}/ordenes/{ot['id']}/repuestos"
        art = _ok(self.c.get(f"{R}/sugerencias", params={"q": "MT-FIL"}))["articulos"]
        self.assertEqual((art[0]["producto_id"], art[0]["disponible"], art[0]["serializado"]), (pid, 10, False))
        self.assertIn("insuficiente", _err(self.c.post(f"{R}/almacen", json={"producto_id": pid, "cantidad": 11}), 422))
        self.assertIn("serie", _err(self.c.post(f"{R}/almacen", json={"producto_id": sid, "cantidad": 1}), 422))
        d = _ok(self.c.post(f"{R}/almacen", json={"producto_id": pid, "cantidad": 3, "costo_unitario": 1000}))
        rep = d["repuestos"][0]
        self.assertEqual((rep["origen"], rep["remito_estado"], rep["cantidad"]), ("ALMACEN", "PENDIENTE", 3))
        self.assertTrue(rep["remito_numero"].startswith("RE"))
        with _db() as conn:
            remito = conn.execute("SELECT r.id, r.destinatario, d.vehiculo_codigo FROM remitos_entrega r JOIN remitos_entrega_detalle d ON d.remito_id = r.id WHERE r.numero = ?",
                                  (rep["remito_numero"],)).fetchone()
        self.assertEqual((remito["destinatario"], remito["vehiculo_codigo"]), (f"Mantenimiento · {ot['numero']}", "C-31"))
        self.assertEqual(_ok(self.c.get(f"{R}/sugerencias", params={"q": "MT-FIL"}))["articulos"][0]["disponible"], 7)  # reservado
        self.assertIn("rechazar", _err(self.c.delete(f"{R}/{rep['id']}"), 422))
        # Almacén lo autoriza: el stock se descuenta
        self.assertNotIn("error", _ok(self.c.post(f"/almacen/remitos_entrega/{remito['id']}/autorizar", json={"autorizado_por": "almacen"})))
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT stock_actual FROM stock WHERE producto_id = ?", (pid,)).fetchone()[0], 7)
        d = _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))
        self.assertEqual((d["repuestos"][0]["remito_estado"], d["costo_repuestos"]), ("AUTORIZADO", 3000))
        # Serializado con su N.º de serie; Almacén lo rechaza: no suma al costo y se puede quitar de la OT
        d = _ok(self.c.post(f"{R}/almacen", json={"producto_id": sid, "cantidad": 1, "series": ["BAT-123"], "costo_unitario": 50000}))
        bat = next(r for r in d["repuestos"] if r["producto_id"] == sid)
        self.assertEqual(d["costo_repuestos"], 53000)
        with _db() as conn:
            rid = conn.execute("SELECT id FROM remitos_entrega WHERE numero = ?", (bat["remito_numero"],)).fetchone()[0]
        _ok(self.c.post(f"/almacen/remitos_entrega/{rid}/no_autorizar", json={"autorizado_por": "almacen", "observaciones": "Sin stock real"}))
        d = _ok(self.c.get(f"{API}/ordenes/{ot['id']}"))
        self.assertEqual(d["costo_repuestos"], 3000)
        _ok(self.c.delete(f"{R}/{bat['id']}"))
        _ok(self.c.post(f"{API}/ordenes/{ot['id']}/finalizar", json={}))

    def test_26_aviso_preventivo_por_mail(self):
        enviados = []

        class SMTPFalso:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, *a):
                pass

            def send_message(self, m):
                enviados.append(m)
        with _db() as conn:
            conn.execute("""INSERT INTO usuarios (nombre_apellido, dni, legajo, correo, password_hash, estado, tipo_usuario, modulos_json,
                paneles_json, acciones_json, bloqueado, intentos_fallidos, password_temporal) VALUES
                ('Jefe Taller', '99999996', '', 'jefe.taller@test.local', 'x', 'ACTIVO', 'USUARIO', '["mantenimiento"]', ?, ?, 0, 0, 0)""",
                         (json.dumps({"mantenimiento": ["preventivo"]}), json.dumps({"mantenimiento": {"preventivo": ["consultar", "gestionar"]}})))
            actual = mpv._lectura(conn, "M-33")["hs"]
        P = f"{API}/preventivo"
        r = _ok(self.c.post(f"{P}/planes", json={"nombre": "Aviso (test)", "intervalo_hs": 100}))
        _ok(self.c.post(f"{P}/planes/{r['plan_id']}/equipos", json={"cis": ["M-33"], "base": "MANUAL", "fecha": "2026-08-01", "hs": actual - 95}))
        asig = next(x for x in _ok(self.c.get(P, params={"ci": "M-33"})) if x["plan_id"] == r["plan_id"])
        self.assertEqual(asig["estado"], "PROXIMO")

        def esperar(n):
            for _ in range(50):
                if len(enviados) >= n:
                    break
                time.sleep(0.1)
            time.sleep(0.3)
        orig_cfg, orig_ssl = main._smtp_config, main.smtplib.SMTP_SSL
        main._smtp_config = lambda: ("smtp@test.local", "clave", "smtp.test.local", 465, "smtp@test.local")
        main.smtplib.SMTP_SSL = SMTPFalso
        try:
            _ok(self.c.get(f"{API}/resumen"))  # al abrir Mantenimiento se revisan los vencimientos
            esperar(1)
            self.assertEqual(len(enviados), 1)
            self.assertIn("jefe.taller@test.local", enviados[0]["To"])
            self.assertIn("1 próximo", enviados[0]["Subject"])
            self.assertIn("M-33", enviados[0].get_body().get_content())
            _ok(self.c.get(f"{API}/resumen"))  # mismo estado: no se repite
            esperar(2)
            self.assertEqual(len(enviados), 1)
            _ok(self.c.post(f"{P}/asignaciones/{asig['id']}/realizado", json={"fecha": "2026-08-01", "hs": actual - 150}))
            _ok(self.c.get(f"{API}/resumen"))
            esperar(2)
            self.assertEqual(len(enviados), 2)
            self.assertIn("1 vencido", enviados[1]["Subject"])
            self.assertIn("VENCIDO (pasado 50 h)", enviados[1].get_body().get_content())
            # Al quedar al día se rearma el aviso
            _ok(self.c.post(f"{P}/asignaciones/{asig['id']}/realizado", json={"fecha": "2026-08-01", "hs": actual}))
            _ok(self.c.get(f"{API}/resumen"))
            esperar(3)
            self.assertEqual(len(enviados), 2)
            with _db() as conn:
                self.assertIsNone(conn.execute("SELECT aviso_estado FROM mt_prev_equipos WHERE id = ?", (asig["id"],)).fetchone()[0])
        finally:
            main._smtp_config, main.smtplib.SMTP_SSL = orig_cfg, orig_ssl
            _ok(self.c.delete(f"{P}/asignaciones/{asig['id']}"))

    def test_27_indicadores(self):
        r = _ok(self.c.get(f"{API}/indicadores", params={"desde": "2026-01-01"}))
        s = r["resumen"]
        self.assertGreaterEqual(s["ot_finalizadas"], 3)
        self.assertEqual(s["ot_finalizadas"], s["ot_correctivas"] + s["ot_preventivas"])
        self.assertGreaterEqual(s["ot_preventivas"], 2)
        self.assertAlmostEqual(s["costo_total"], s["costo_correctivo"] + s["costo_preventivo"], places=1)
        self.assertGreaterEqual(s["costo_repuestos"], 3000)
        self.assertEqual(len(r["costo_mensual"]), 11)
        self.assertTrue(any(e["ci"] == "C-31" and e["repuestos"] >= 3000 for e in r["costo_por_equipo"]))
        self.assertTrue(all(0 <= e["disponibilidad"] <= 100 for e in r["disponibilidad"]))
        self.assertTrue(any(m["legajo"] == MEC for m in r["mecanicos"]))
        self.assertTrue(r["equipos_fallas"])
        self.assertIsNotNone(s["disponibilidad_flota"])
        self.assertTrue(any(v["origen"].startswith("OT-") for v in r["services"]))
        _err(self.c.get(f"{API}/indicadores", params={"desde": "2026-12-01", "hasta": "2026-01-01"}), 422)
        _err(self.c.get(f"{API}/indicadores", params={"desde": "nada"}), 422)

    def test_08_permisos(self):
        c = _cliente(*CONSULTA)
        self.assertIsInstance(_ok(c.get(f"{API}/novedades")), list)
        self.assertIsInstance(_ok(c.get(f"{API}/equipos")), list)
        self.assertEqual(c.post(f"{API}/novedades", json={"tipo": "EQUIPO", "ci": CI, "descripcion": "x"}).status_code, 403)
        self.assertEqual(c.post(f"{API}/novedades/reporte", json={"items": [{"ci": CI, "descripcion": "x"}]}).status_code, 403)
        self.assertEqual(c.get(f"{API}/novedades/parecidas", params={"ci": CI, "descripcion": "x"}).status_code, 403)
        self.assertEqual(c.post(f"{API}/novedades/1/resolver", json={"decision": "CERRAR", "resolucion": "x"}).status_code, 403)
        self.assertEqual(c.post(f"{API}/ordenes/1/finalizar", json={}).status_code, 403)
        self.assertEqual(c.put(f"{API}/equipos/{CI}/estado", json={"estado": "FUNCIONAL", "motivo": "x"}).status_code, 403)
        self.assertEqual(c.put(f"{API}/config", json={"valor_hora_mecanico": 1}).status_code, 403)
        self.assertEqual(c.get(f"{API}/preventivo").status_code, 403, "sin panel Preventivo")
        self.assertEqual(c.get(f"{API}/indicadores").status_code, 403, "sin panel Indicadores")
        self.assertEqual(c.post(f"{API}/ordenes/1/repuestos/almacen", json={"producto_id": 1, "cantidad": 1}).status_code, 403)
        self.assertEqual(c.put(f"{API}/ordenes/1/tareas/1", json={"hecha": True}).status_code, 403)
        self.assertEqual(c.post(f"{API}/preventivo/planes", json={"nombre": "x", "intervalo_hs": 1}).status_code, 403)
        self.assertEqual(c.post(f"{API}/ordenes/1/repuestos", json={"descripcion": "x", "cantidad": 1}).status_code, 403)
        self.assertEqual(c.get(f"{API}/ordenes/1/repuestos/sugerencias").status_code, 403)


if __name__ == "__main__":
    unittest.main()
