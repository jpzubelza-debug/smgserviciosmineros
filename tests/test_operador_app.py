"""Pruebas de la app móvil de operadores (/operador): PIN, sesiones y carga de partes.

Se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal: DATA_DIR se
fija antes de importar main, porque importar main ejecuta init_sqlite().
Ejecutar este módulo en un proceso propio:

    .venv\\Scripts\\python.exe -m unittest tests.test_operador_app -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta

if "main" in sys.modules:
    raise unittest.SkipTest("main ya fue importado con otra DATA_DIR; ejecute este módulo por separado.")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_REAL = os.path.join(BASE_DIR, "dashboard.db")
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_operador_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")
MTIME_REAL = os.path.getmtime(DB_REAL)

_origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
_destino = sqlite3.connect(DB_COPIA)
_origen.backup(_destino)
_destino.close()
_origen.close()

os.environ["DATA_DIR"] = TMP_DIR
os.environ["APP_LOGIN_USER"] = "contingencia_test"
os.environ["APP_LOGIN_PASSWORD"] = "clave-contingencia-test"

import main  # noqa: E402
import httpx  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import uvicorn  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB_COPIA), "Las pruebas no apuntan a la copia"

_sock = socket.socket()
_sock.bind(("127.0.0.1", 0))
PUERTO = _sock.getsockname()[1]
_sock.close()
BASE_URL = f"http://127.0.0.1:{PUERTO}"
_SERVIDOR = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PUERTO, log_level="warning", lifespan="off"))
threading.Thread(target=_SERVIDOR.run, daemon=True).start()
for _ in range(100):
    if _SERVIDOR.started:
        break
    time.sleep(0.05)


def _db():
    conn = sqlite3.connect(DB_COPIA)
    conn.row_factory = sqlite3.Row
    return conn


def _cliente():
    return httpx.Client(base_url=BASE_URL, follow_redirects=False)


def _empleados(cantidad):
    with _db() as conn:
        filas = conn.execute(
            """
            SELECT TRIM(legajo) AS legajo, nombre, cuil FROM personal
            WHERE UPPER(COALESCE(activo, 'ACTIVO')) = 'ACTIVO' AND LENGTH(REPLACE(cuil, '-', '')) = 11
            ORDER BY CAST(legajo AS INTEGER) LIMIT ?
            """,
            (cantidad,),
        ).fetchall()
    return [dict(f) for f in filas]


def _dni(cuil):
    return cuil.replace("-", "")[2:10]


def _catalogos():
    with _db() as conn:
        ci = conn.execute(
            "SELECT codigo FROM vehiculos WHERE TRIM(COALESCE(codigo, '')) != '' AND UPPER(TRIM(codigo)) NOT LIKE 'V%' LIMIT 1"
        ).fetchone()[0]
        cc = conn.execute("SELECT id, nombre FROM centros_costos WHERE COALESCE(activo, 1) = 1 AND TRIM(COALESCE(nombre, '')) != '' LIMIT 1").fetchone()
        cli = conn.execute("SELECT codigo, razon_social FROM clientes WHERE TRIM(COALESCE(codigo, '')) != '' LIMIT 1").fetchone()
    return ci.strip(), f"{cc['id']} - {cc['nombre'].strip()}", f"{str(cli['codigo']).strip()} - {str(cli['razon_social']).strip()}"


def _registrar(cliente, empleado, pin="4826"):
    return cliente.post("/operador/api/registrar", json={
        "legajo": empleado["legajo"], "dni": _dni(empleado["cuil"]), "pin": pin, "pin_confirmacion": pin,
    })


JPEG_PRUEBA = b"\xff\xd8\xff\xe0" + b"FOTO-DE-PRUEBA" * 50 + b"\xff\xd9"


def _enviar_parte(cliente, datos, foto=JPEG_PRUEBA):
    """Alta desde el teléfono: multipart con `datos` (JSON) y `foto` (JPEG)."""
    archivos = {"foto": ("parte.jpg", foto, "image/jpeg")} if foto is not None else None
    return cliente.post("/operador/api/partes", data={"datos": main.json.dumps(datos)}, files=archivos)


def _admin():
    cliente = _cliente()
    r = cliente.post("/login", data={"username": "contingencia_test", "password": "clave-contingencia-test"})
    assert r.status_code == 302, r.text
    return cliente


class OperadorAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.emp_a, cls.emp_b, cls.emp_c, cls.emp_d, cls.emp_e, cls.emp_f, cls.emp_g = _empleados(7)
        cls.ci, cls.obra, cls.cliente_cat = _catalogos()

    def _parte(self, **extra):
        datos = {
            "ci": self.ci, "fecha_parte": date.today().isoformat(), "nro_parte": f"T-{time.time_ns()}",
            "obra": self.obra, "cliente": self.cliente_cat, "turno": "Día",
            "hs_inicio": "100", "hs_final": "108", "descripcion_trabajos": "Prueba automatizada",
        }
        datos.update(extra)
        return datos

    def test_pagina_y_manifest(self):
        c = _cliente()
        r = c.get("/operador")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Partes Diarios", r.text)
        self.assertNotIn("/session/actividad", r.text, "No debe inyectarse el guard de sesión del escritorio")
        self.assertEqual(c.get("/operador/manifest.webmanifest").json()["start_url"], "/operador")

    def test_identificar(self):
        c = _cliente()
        self.assertEqual(c.post("/operador/api/identificar", json={"legajo": "99999999"}).status_code, 404)
        r = c.post("/operador/api/identificar", json={"legajo": self.emp_d["legajo"]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["tiene_pin"])
        # Acepta ceros a la izquierda.
        r = c.post("/operador/api/identificar", json={"legajo": "00" + self.emp_d["legajo"]})
        self.assertEqual(r.json()["legajo"], self.emp_d["legajo"])

    def test_registro_login_y_carga_de_parte(self):
        c = _cliente()
        emp = self.emp_a
        self.assertEqual(c.get("/operador/api/yo").status_code, 401)

        # DNI incorrecto, PIN débil o que no coincide.
        r = c.post("/operador/api/registrar", json={"legajo": emp["legajo"], "dni": "11111111", "pin": "4826", "pin_confirmacion": "4826"})
        self.assertEqual(r.status_code, 401)
        r = c.post("/operador/api/registrar", json={"legajo": emp["legajo"], "dni": _dni(emp["cuil"]), "pin": "1234", "pin_confirmacion": "1234"})
        self.assertEqual(r.status_code, 400)
        r = c.post("/operador/api/registrar", json={"legajo": emp["legajo"], "dni": _dni(emp["cuil"]), "pin": "4826", "pin_confirmacion": "4827"})
        self.assertEqual(r.status_code, 400)

        r = _registrar(c, emp)
        self.assertEqual(r.status_code, 200, r.text)
        cookie = r.headers.get("set-cookie", "")
        self.assertIn("op_session=", cookie)
        self.assertIn("Path=/operador", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertEqual(c.get("/operador/api/yo").json()["legajo"], emp["legajo"])
        # Por única vez: no se puede volver a crear.
        self.assertEqual(_registrar(_cliente(), emp, pin="5937").status_code, 409)

        # El PIN se guarda con hash PBKDF2 (nunca en texto plano).
        with _db() as conn:
            fila = conn.execute("SELECT pin_hash, pin_cifrado FROM operador_pines WHERE legajo = ?", (emp["legajo"],)).fetchone()
        self.assertTrue(fila["pin_hash"].startswith("pbkdf2_sha256$"))
        self.assertNotIn("4826", fila["pin_hash"])
        self.assertNotEqual(fila["pin_cifrado"], "4826")

        # La cookie del operador no da acceso al escritorio.
        self.assertFalse(c.get("/me").json()["autenticado"])
        self.assertEqual(c.get("/operaciones/metadata").status_code, 401)

        # Login con PIN desde otro teléfono.
        otro = _cliente()
        self.assertEqual(otro.post("/operador/api/login", json={"legajo": emp["legajo"], "pin": "0000"}).status_code, 401)
        self.assertEqual(otro.post("/operador/api/login", json={"legajo": emp["legajo"], "pin": "4826"}).status_code, 200)

        meta = otro.get("/operador/api/metadata").json()
        self.assertIn(self.obra, meta["centros_costos"])
        self.assertNotIn("maquinistas", meta)

        # Carga: legajo y maquinista salen de la sesión; los campos de control se ignoran.
        datos = self._parte(legajo_empleado="1", maquinista="OTRA PERSONA", control_supervisor="X", control="admin", certificado="C-1")
        r = _enviar_parte(otro, datos)
        self.assertEqual(r.status_code, 200, r.text)
        with _db() as conn:
            fila = conn.execute("SELECT * FROM horas_maquinas WHERE id = ?", (r.json()["id"],)).fetchone()
        self.assertEqual(fila["legajo_empleado"], emp["legajo"])
        self.assertEqual(fila["maquinista"], emp["nombre"].strip())
        self.assertEqual(fila["origen_carga"], "APP_OPERADOR")
        self.assertEqual(fila["total_hs"], 8)
        self.assertIsNone(fila["control_supervisor"])
        self.assertIsNone(fila["control"])
        self.assertIsNone(fila["certificado"])

        # Duplicado, fecha futura, horómetro invertido, catálogo inválido.
        self.assertEqual(_enviar_parte(otro, datos).status_code, 400)
        manana = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(_enviar_parte(otro, self._parte(fecha_parte=manana)).status_code, 400)
        self.assertEqual(_enviar_parte(otro, self._parte(hs_inicio="200", hs_final="150")).status_code, 400)
        self.assertEqual(_enviar_parte(otro, self._parte(cliente="NO EXISTE")).status_code, 400)
        self.assertEqual(_enviar_parte(otro, self._parte(descripcion_trabajos="")).status_code, 400)

        # Último horómetro del equipo para precargar.
        self.assertEqual(otro.get("/operador/api/ultimos", params={"ci": self.ci}).json()["hs_final"], 108)

        # Solo ve sus propios partes.
        lista = otro.get("/operador/api/partes", params={"periodo": "todo", "limit": 50}).json()
        with _db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM horas_maquinas WHERE TRIM(legajo_empleado) = ?", (emp["legajo"],)).fetchone()[0]
            ajeno = conn.execute("SELECT id FROM horas_maquinas WHERE TRIM(COALESCE(legajo_empleado, '')) != ? LIMIT 1", (emp["legajo"],)).fetchone()[0]
            hace_un_anio = (date.today() - timedelta(days=400)).isoformat()
            conn.execute(
                "INSERT INTO horas_maquinas (ci, fecha_parte, nro_parte, legajo_empleado, maquinista, obra, total_hs) VALUES (?, ?, 'VIEJO-1', ?, 'x', 'OBRA VIEJA', 5)",
                (self.ci, hace_un_anio, emp["legajo"]),
            )
        self.assertEqual(lista["total"], total)
        self.assertTrue(all(p["id"] for p in lista["data"]))

        # Por defecto: últimos 30 días; el parte de hace más de un año solo aparece en "todo".
        defecto = otro.get("/operador/api/partes").json()
        self.assertEqual(defecto["periodo"], "30d")
        self.assertIn(r.json()["id"], [p["id"] for p in defecto["data"]])
        self.assertNotIn("VIEJO-1", [p["nro_parte"] for p in defecto["data"]])
        self.assertNotIn("OBRA VIEJA", defecto["opciones"]["proyectos"])
        todo = otro.get("/operador/api/partes", params={"periodo": "todo", "proyecto": "OBRA VIEJA"}).json()
        self.assertEqual([p["nro_parte"] for p in todo["data"]], ["VIEJO-1"])
        # Filtro por equipo y paginación.
        por_ci = otro.get("/operador/api/partes", params={"periodo": "todo", "ci": self.ci.lower()}).json()
        self.assertTrue(por_ci["total"] >= 2 and all(p["ci"].upper() == self.ci.upper() for p in por_ci["data"]))
        pag = otro.get("/operador/api/partes", params={"periodo": "todo", "limit": 1, "page": 2}).json()
        self.assertEqual((pag["page"], len(pag["data"]), pag["paginas"]), (2, 1, pag["total"]))
        fuera = otro.get("/operador/api/partes", params={"periodo": "todo", "limit": 1, "page": 9999}).json()
        self.assertEqual(fuera["page"], fuera["paginas"])
        self.assertEqual(otro.get(f"/operador/api/partes/{r.json()['id']}").status_code, 200)
        self.assertEqual(otro.get(f"/operador/api/partes/{ajeno}").status_code, 404)

        # Logout invalida la sesión.
        otro.post("/operador/api/logout")
        self.assertEqual(otro.get("/operador/api/yo").status_code, 401)

    def test_bloqueo_por_intentos_y_desbloqueo_admin(self):
        emp = self.emp_b
        self.assertEqual(_registrar(_cliente(), emp, pin="7391").status_code, 200)
        c = _cliente()
        codigos = [c.post("/operador/api/login", json={"legajo": emp["legajo"], "pin": "0000"}).status_code for _ in range(5)]
        self.assertEqual(codigos, [401, 401, 401, 401, 429])
        # Bloqueado: ni el PIN correcto entra.
        self.assertEqual(c.post("/operador/api/login", json={"legajo": emp["legajo"], "pin": "7391"}).status_code, 429)

        admin = _admin()
        self.assertEqual(admin.post(f"/admin/operadores-pin/{emp['legajo']}/desbloquear").status_code, 200)
        self.assertEqual(c.post("/operador/api/login", json={"legajo": emp["legajo"], "pin": "7391"}).status_code, 200)

    def test_admin_ver_y_resetear_pin(self):
        emp = self.emp_c
        telefono = _cliente()
        self.assertEqual(_registrar(telefono, emp, pin="6150").status_code, 200)

        # Sin sesión de administrador no hay acceso.
        self.assertEqual(_cliente().get("/admin/operadores-pin").status_code, 403)
        self.assertEqual(telefono.get(f"/admin/operadores-pin/{emp['legajo']}/pin").status_code, 403)

        admin = _admin()
        lista = admin.get("/admin/operadores-pin").json()
        fila = next(o for o in lista if o["legajo"] == emp["legajo"])
        self.assertTrue(fila["tiene_pin"])
        self.assertNotIn("pin_hash", fila)

        data = admin.get(f"/admin/operadores-pin/{emp['legajo']}/pin").json()
        if main._password_cipher is not None:
            self.assertEqual(data["pin"], "6150")
        else:
            self.assertIsNone(data["pin"])

        self.assertEqual(admin.post(f"/admin/operadores-pin/{emp['legajo']}/reset").status_code, 200)
        self.assertEqual(telefono.get("/operador/api/yo").status_code, 401, "El reset debe cerrar la sesión del teléfono")
        r = _cliente().post("/operador/api/identificar", json={"legajo": emp["legajo"]})
        self.assertFalse(r.json()["tiene_pin"])
        self.assertEqual(_registrar(_cliente(), emp, pin="2958").status_code, 200)
        with _db() as conn:
            eventos = {f[0] for f in conn.execute("SELECT evento FROM historial_accesos WHERE detalle = ?", (f"legajo={emp['legajo']}",))}
        self.assertTrue({"PIN_OPERADOR_VISUALIZADO", "PIN_OPERADOR_RESETEADO"} <= eventos)

    def test_resumen_indicadores_y_ranking(self):
        emp = self.emp_e
        c = _cliente()
        self.assertEqual(c.get("/operador/api/resumen").status_code, 401)
        self.assertEqual(_registrar(c, emp, pin="8164").status_code, 200)
        # Un parte normal (8 hs) y otro con un total absurdo que debe contar como máximo 24 hs.
        self.assertEqual(_enviar_parte(c, self._parte(hs_inicio="1000", hs_final="1008")).status_code, 200)
        self.assertEqual(_enviar_parte(c, self._parte(hs_inicio="", hs_final="", total_hs="500")).status_code, 200)
        # Un parte con equipo "V" (camioneta) se guarda pero no cuenta para los indicadores.
        with _db() as conn:
            fila_v = conn.execute("SELECT codigo FROM vehiculos WHERE UPPER(TRIM(codigo)) LIKE 'V%' LIMIT 1").fetchone()
        if fila_v:
            r_v = _enviar_parte(c, self._parte(ci=fila_v[0].strip(), hs_inicio="0", hs_final="10"))
            self.assertEqual(r_v.status_code, 200, r_v.text)

        r = c.get("/operador/api/resumen", params={"periodo": "mes"}).json()
        self.assertEqual(r["kpis"]["partes"], 2)
        self.assertEqual(r["kpis"]["hs"], 32)
        self.assertEqual(r["kpis"]["pendientes"], 2)
        self.assertIsNotNone(r["posicion"])
        yo = [x for x in r["ranking"] if x["yo"]]
        self.assertEqual(len(yo), 1)
        self.assertEqual(yo[0]["hs"], 32)
        self.assertEqual(r["serie"]["granularidad"], "dia")
        self.assertEqual(sum(d["hs"] for d in r["serie"]["datos"]), 32)
        self.assertEqual(r["por_equipo"][0]["ci"], self.ci.upper())
        # El ranking ordena por horas y no expone legajos.
        horas = [x["hs"] for x in r["ranking"] if not x["yo"] or x["posicion"] <= 10]
        self.assertEqual(horas, sorted(horas, reverse=True))
        self.assertNotIn("legajo", r["ranking"][0])
        # Ranking por proyecto: solo proyectos propios del período.
        self.assertEqual([p["proyecto"] for p in r["proyectos_disponibles"]], [self.obra])
        rp = c.get("/operador/api/ranking", params={"periodo": "mes", "proyecto": self.obra}).json()
        self.assertEqual(rp["proyecto"], self.obra)
        self.assertEqual(next(x for x in rp["ranking"] if x["yo"])["hs"], 32)
        self.assertLessEqual(rp["total_operadores"], r["total_operadores"])
        self.assertEqual(c.get("/operador/api/ranking", params={"periodo": "mes", "proyecto": "PROYECTO AJENO"}).status_code, 400)
        todos = c.get("/operador/api/ranking", params={"periodo": "mes"}).json()
        self.assertEqual(todos["total_operadores"], r["total_operadores"])
        for periodo, gran in (("mes_anterior", "dia"), ("trimestre", "semana"), ("anio", "mes")):
            datos = c.get("/operador/api/resumen", params={"periodo": periodo}).json()
            self.assertEqual(datos["serie"]["granularidad"], gran, periodo)

    def test_foto_obligatoria_y_archivado_al_controlar(self):
        emp = self.emp_f
        c = _cliente()
        self.assertEqual(_registrar(c, emp, pin="3719").status_code, 200)

        # Sin foto, con JSON o con un archivo que no es JPEG: rechazado.
        self.assertEqual(_enviar_parte(c, self._parte(), foto=None).status_code, 400)
        self.assertEqual(c.post("/operador/api/partes", json=self._parte()).status_code, 400)
        self.assertEqual(_enviar_parte(c, self._parte(), foto=b"\x89PNG\r\n\x1a\nno-es-jpeg").status_code, 400)
        demasiado = b"\xff\xd8\xff" + b"0" * (main.FOTO_PARTE_MAX_BYTES + 10)
        self.assertEqual(c.post("/operador/api/partes", data={"datos": "{}"}, files={"foto": ("p.jpg", demasiado, "image/jpeg")}, timeout=60).status_code, 400)
        with _db() as conn:
            sin_foto = conn.execute("SELECT COUNT(*) FROM horas_maquinas WHERE legajo_empleado = ? AND origen_carga = 'APP_OPERADOR'", (emp["legajo"],)).fetchone()[0]
        self.assertEqual(sin_foto, 0, "Un parte rechazado no debe quedar guardado")

        datos = self._parte(nro_parte="F/77")
        r = _enviar_parte(c, datos)
        self.assertEqual(r.status_code, 200, r.text)
        id_parte = r.json()["id"]
        with _db() as conn:
            fila = conn.execute("SELECT * FROM partes_fotos WHERE parte_id = ?", (id_parte,)).fetchone()
        ruta = os.path.join(main.DOC_PARTES_DIR, fila["archivo"])
        self.assertTrue(os.path.exists(ruta))
        self.assertEqual(open(ruta, "rb").read(), JPEG_PRUEBA)

        # El operador ve su foto; otro operador no.
        self.assertEqual(c.get(f"/operador/api/partes/{id_parte}/foto").content, JPEG_PRUEBA)
        self.assertTrue(c.get(f"/operador/api/partes/{id_parte}").json()["data"]["foto"]["en_servidor"])
        otro = _cliente()
        _registrar(otro, self.emp_g, pin="5281")
        self.assertEqual(otro.get(f"/operador/api/partes/{id_parte}/foto").status_code, 404)

        admin = _admin()
        nombre = f"Parte_F-77_{self.ci}_{datos['fecha_parte']}.jpg"
        detalle = admin.get(f"/operaciones/horas-maquinas/{id_parte}").json()["data"]
        self.assertEqual(detalle["foto"]["nombre_archivo"], nombre)
        # No se puede archivar antes de controlar.
        self.assertEqual(admin.post(f"/operaciones/horas-maquinas/{id_parte}/foto/archivar").status_code, 400)

        control = admin.post(f"/operaciones/horas-maquinas/{id_parte}/controlar").json()
        self.assertTrue(control["foto"]["en_servidor"])
        self.assertEqual(control["foto"]["nombre_archivo"], nombre)
        descarga = admin.get(f"/operaciones/horas-maquinas/{id_parte}/foto", params={"descargar": 1})
        self.assertEqual(descarga.content, JPEG_PRUEBA)
        self.assertIn("attachment", descarga.headers["content-disposition"])
        self.assertIn(nombre, descarga.headers["content-disposition"])

        archivado = admin.post(f"/operaciones/horas-maquinas/{id_parte}/foto/archivar").json()["foto"]
        self.assertFalse(archivado["en_servidor"])
        self.assertEqual(archivado["nombre_archivo"], nombre)
        self.assertTrue(archivado["archivada_en"])
        self.assertFalse(os.path.exists(ruta), "La foto debe borrarse del servidor")
        self.assertEqual(admin.get(f"/operaciones/horas-maquinas/{id_parte}/foto").status_code, 404)
        self.assertEqual(c.get(f"/operador/api/partes/{id_parte}/foto").status_code, 404)
        self.assertEqual(admin.post(f"/operaciones/horas-maquinas/{id_parte}/foto/archivar").status_code, 200)

        # Eliminar un parte borra también su foto.
        r2 = _enviar_parte(c, self._parte(nro_parte="F-78"))
        with _db() as conn:
            archivo2 = conn.execute("SELECT archivo FROM partes_fotos WHERE parte_id = ?", (r2.json()["id"],)).fetchone()[0]
        ruta2 = os.path.join(main.DOC_PARTES_DIR, archivo2)
        self.assertTrue(os.path.exists(ruta2))
        self.assertEqual(admin.delete(f"/operaciones/horas-maquinas/{r2.json()['id']}").status_code, 200)
        self.assertFalse(os.path.exists(ruta2))
        with _db() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM partes_fotos WHERE parte_id = ?", (r2.json()["id"],)).fetchone())

    def test_alta_de_escritorio_sigue_funcionando(self):
        admin = _admin()
        datos = self._parte(maquinista=self.emp_d["nombre"], legajo_empleado=self.emp_d["legajo"], nro_parte=f"E-{time.time_ns()}")
        r = admin.post("/operaciones/horas-maquinas", json=datos)
        self.assertEqual(r.status_code, 200, r.text)
        with _db() as conn:
            fila = conn.execute("SELECT origen_carga, total_hs, legajo_empleado FROM horas_maquinas WHERE id = ?", (r.json()["id"],)).fetchone()
        self.assertIsNone(fila["origen_carga"])
        self.assertEqual(fila["total_hs"], 8)
        self.assertEqual(fila["legajo_empleado"], self.emp_d["legajo"])
        self.assertIn("equipos", admin.get("/operaciones/metadata").json())


def tearDownModule():
    _SERVIDOR.should_exit = True
    time.sleep(0.3)
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    assert os.path.getmtime(DB_REAL) == MTIME_REAL, "La base real fue modificada"


if __name__ == "__main__":
    unittest.main()
