"""Pruebas de sesiones del lado del servidor.

Se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal: DATA_DIR se
fija antes de importar main, porque importar main ejecuta init_sqlite().
Ejecutar este módulo en un proceso propio:

    .venv\\Scripts\\python.exe -m unittest tests.test_sesiones -v
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

if "main" in sys.modules:
    raise unittest.SkipTest("main ya fue importado con otra DATA_DIR; ejecute este módulo por separado.")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_REAL = os.path.join(BASE_DIR, "dashboard.db")
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_sesiones_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")


def _copiar_base():
    origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
    destino = sqlite3.connect(DB_COPIA)
    origen.backup(destino)
    destino.close()
    origen.close()


def _conteos(ruta):
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        tablas = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tablas}
    finally:
        conn.close()


_copiar_base()
CONTEOS_ANTES = _conteos(DB_COPIA)
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
CONTEOS_DESPUES_INIT = _conteos(DB_COPIA)

# Servidor HTTP real sobre la copia (el TestClient del entorno no es compatible con httpx 0.28).
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

CLAVE = "Clave-Prueba-1"
CORREO_OPER = "prueba.operaciones@test.local"
CORREO_SIN = "prueba.sinmodulo@test.local"


def _db():
    conn = sqlite3.connect(DB_COPIA)
    conn.row_factory = sqlite3.Row
    return conn


def _crear_usuario(correo, modulos, tipo="OPERADOR"):
    with _db() as conn:
        conn.execute("DELETE FROM usuarios WHERE correo = ?", (correo,))
        cur = conn.execute(
            """
            INSERT INTO usuarios (nombre_apellido, correo, password_hash, estado, tipo_usuario, modulos_json,
                                  paneles_json, acciones_json, bloqueado, intentos_fallidos)
            VALUES (?, ?, ?, 'ACTIVO', ?, ?, '{}', '{}', 0, 0)
            """,
            (correo, correo, hashlib.sha256(CLAVE.encode()).hexdigest(), tipo, main.json.dumps(modulos)),
        )
        return cur.lastrowid


def _cliente(headers=None):
    return httpx.Client(base_url=BASE_URL, follow_redirects=False, headers=headers or {})


def _login(usuario, clave, cliente=None):
    cliente = cliente or _cliente()
    respuesta = cliente.post("/login", data={"username": usuario, "password": clave})
    return cliente, respuesta


def _token_de(respuesta):
    for valor in respuesta.headers.get_list("set-cookie"):
        if valor.startswith(f"{main.AUTH_COOKIE_NAME}="):
            return valor.split(";", 1)[0].split("=", 1)[1]
    return None


def _cliente_con_cookie(nombre, valor):
    return _cliente({"Cookie": f"{nombre}={valor}"})


class SesionesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.id_oper = _crear_usuario(CORREO_OPER, ["operaciones"])
        cls.id_sin = _crear_usuario(CORREO_SIN, ["logistica"])

    # --- Suplantación ---------------------------------------------------
    def test_cookie_legada_con_usuario_no_autentica(self):
        for usuario in ("admin", "contingencia_test", CORREO_OPER):
            cliente = _cliente_con_cookie(main.LEGACY_AUTH_COOKIE_NAME, usuario)
            self.assertEqual(cliente.get("/operaciones/seguimientos-cliente").status_code, 401, usuario)
            self.assertFalse(cliente.get("/me").json()["autenticado"], usuario)

    def test_cookie_nueva_con_usuario_en_texto_no_autentica(self):
        for usuario in ("admin", "contingencia_test", CORREO_OPER):
            cliente = _cliente_con_cookie(main.AUTH_COOKIE_NAME, usuario)
            self.assertEqual(cliente.get("/operaciones/seguimientos-cliente").status_code, 401, usuario)

    def test_token_inventado_no_autentica(self):
        cliente = _cliente_con_cookie(main.AUTH_COOKIE_NAME, main.secrets.token_urlsafe(32))
        self.assertEqual(cliente.get("/operaciones/seguimientos-cliente").status_code, 401)

    def test_hash_del_token_como_cookie_no_autentica(self):
        _, respuesta = _login(CORREO_OPER, CLAVE)
        token = _token_de(respuesta)
        cliente = _cliente_con_cookie(main.AUTH_COOKIE_NAME, main._sesion_token_hash(token))
        self.assertEqual(cliente.get("/operaciones/seguimientos-cliente").status_code, 401)

    def test_paginas_html_redirigen_a_login_sin_sesion(self):
        cliente = _cliente_con_cookie(main.LEGACY_AUTH_COOKIE_NAME, "admin")
        respuesta = cliente.get("/")
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta.headers["location"], "/login")

    # --- Login y almacenamiento ------------------------------------------
    def test_login_crea_sesion_y_guarda_solo_el_hash(self):
        cliente, respuesta = _login(CORREO_OPER, CLAVE)
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta.headers["location"], "/")
        token = _token_de(respuesta)
        self.assertTrue(token and len(token) >= 40)
        cookie = next(v for v in respuesta.headers.get_list("set-cookie") if v.startswith(main.AUTH_COOKIE_NAME))
        self.assertIn("httponly", cookie.lower())
        self.assertIn("samesite=lax", cookie.lower())
        with _db() as conn:
            fila = conn.execute("SELECT * FROM sesiones WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            self.assertIsNotNone(fila)
            self.assertEqual(fila["origen"], "usuarios")
            self.assertEqual(fila["usuario_id"], self.id_oper)
            filas_con_token = [dict(r) for r in conn.execute("SELECT * FROM sesiones") if token in " ".join(str(v) for v in dict(r).values())]
            self.assertEqual(filas_con_token, [], "El token en claro no debe guardarse en la base")
        me = cliente.get("/me").json()
        self.assertTrue(me["autenticado"])
        self.assertEqual(me["usuario"], CORREO_OPER)
        self.assertEqual(me["origen"], "usuarios")
        self.assertNotIn("es_desarrollador", me)

    def test_login_incorrecto_no_crea_sesion(self):
        with _db() as conn:
            antes = conn.execute("SELECT COUNT(*) FROM sesiones").fetchone()[0]
            intentos = conn.execute("SELECT intentos_fallidos FROM usuarios WHERE id = ?", (self.id_oper,)).fetchone()[0]
        _, respuesta = _login(CORREO_OPER, "incorrecta")
        self.assertEqual(respuesta.headers["location"], "/login?error=1")
        self.assertIsNone(_token_de(respuesta))
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sesiones").fetchone()[0], antes)
            self.assertEqual(conn.execute("SELECT intentos_fallidos FROM usuarios WHERE id = ?", (self.id_oper,)).fetchone()[0], intentos + 1)
            conn.execute("UPDATE usuarios SET intentos_fallidos = 0 WHERE id = ?", (self.id_oper,))

    def test_cookie_secure_detras_de_https(self):
        cliente = _cliente()
        respuesta = cliente.post("/login", data={"username": CORREO_OPER, "password": CLAVE}, headers={"x-forwarded-proto": "https"})
        cookie = next(v for v in respuesta.headers.get_list("set-cookie") if v.startswith(main.AUTH_COOKIE_NAME))
        self.assertIn("secure", cookie.lower())

    def test_nuevo_login_revoca_la_sesion_anterior_del_navegador(self):
        cliente, respuesta = _login(CORREO_OPER, CLAVE)
        token_1 = _token_de(respuesta)
        _, respuesta_2 = _login(CORREO_OPER, CLAVE, cliente)
        token_2 = _token_de(respuesta_2)
        self.assertNotEqual(token_1, token_2)
        self.assertEqual(_cliente_con_cookie(main.AUTH_COOKIE_NAME, token_1).get("/me").json()["autenticado"], False)
        self.assertTrue(_cliente_con_cookie(main.AUTH_COOKIE_NAME, token_2).get("/me").json()["autenticado"])

    # --- Permisos existentes ---------------------------------------------
    def test_permisos_por_modulo_se_conservan(self):
        cliente_oper, _ = _login(CORREO_OPER, CLAVE)
        cliente_sin, _ = _login(CORREO_SIN, CLAVE)
        self.assertEqual(cliente_oper.get("/operaciones/seguimientos-cliente").status_code, 200)
        self.assertEqual(cliente_sin.get("/operaciones/seguimientos-cliente").status_code, 403)

    def test_usuario_no_administrador_no_accede_a_administracion(self):
        cliente_oper, _ = _login(CORREO_OPER, CLAVE)
        self.assertEqual(cliente_oper.post(f"/admin/usuarios/{self.id_sin}/bloqueo", json={"bloqueado": True}).status_code, 403)

    def test_contingencia(self):
        cliente, respuesta = _login("contingencia_test", "clave-contingencia-test")
        self.assertIsNotNone(_token_de(respuesta))
        me = cliente.get("/me").json()
        self.assertTrue(me["es_desarrollador"])
        self.assertEqual(me["origen"], "contingencia")
        _, malo = _login("contingencia_test", "otra")
        self.assertIsNone(_token_de(malo))
        # Si cambia el usuario de contingencia configurado, las sesiones previas dejan de valer.
        token = _token_de(respuesta)
        usuario_original = main.AUTH_USER
        try:
            main.AUTH_USER = "otro_usuario"
            self.assertFalse(_cliente_con_cookie(main.AUTH_COOKIE_NAME, token).get("/me").json()["autenticado"])
        finally:
            main.AUTH_USER = usuario_original

    # --- Revocación y vencimiento -----------------------------------------
    def test_logout_revoca_el_token(self):
        cliente, respuesta = _login(CORREO_OPER, CLAVE)
        token = _token_de(respuesta)
        salida = cliente.get("/logout")
        self.assertEqual(salida.status_code, 302)
        self.assertEqual(_cliente_con_cookie(main.AUTH_COOKIE_NAME, token).get("/operaciones/seguimientos-cliente").status_code, 401)
        with _db() as conn:
            fila = conn.execute("SELECT motivo_revocacion FROM sesiones WHERE token_hash = ?", (main._sesion_token_hash(token),)).fetchone()
        self.assertEqual(fila["motivo_revocacion"], "LOGOUT")

    def test_bloqueo_y_reset_de_password_revocan_sesiones(self):
        admin, _ = _login("contingencia_test", "clave-contingencia-test")
        objetivo = _crear_usuario("prueba.revocacion@test.local", ["operaciones"])
        _, respuesta = _login("prueba.revocacion@test.local", CLAVE)
        token = _token_de(respuesta)
        self.assertEqual(admin.post(f"/admin/usuarios/{objetivo}/bloqueo", json={"bloqueado": True}).status_code, 200)
        self.assertEqual(admin.post(f"/admin/usuarios/{objetivo}/bloqueo", json={"bloqueado": False}).status_code, 200)
        # Desbloquear no revive la sesión revocada.
        self.assertFalse(_cliente_con_cookie(main.AUTH_COOKIE_NAME, token).get("/me").json()["autenticado"])

        _, respuesta = _login("prueba.revocacion@test.local", CLAVE)
        token = _token_de(respuesta)
        self.assertEqual(admin.post(f"/admin/usuarios/{objetivo}/reset_password", json={"password": "Nueva-Clave-9"}).status_code, 200)
        self.assertFalse(_cliente_con_cookie(main.AUTH_COOKIE_NAME, token).get("/me").json()["autenticado"])

        _, respuesta = _login("prueba.revocacion@test.local", "Nueva-Clave-9")
        token = _token_de(respuesta)
        self.assertEqual(admin.post(f"/admin/usuarios/{objetivo}/estado", json={"estado": "INACTIVO"}).status_code, 200)
        self.assertFalse(_cliente_con_cookie(main.AUTH_COOKIE_NAME, token).get("/me").json()["autenticado"])

    def test_usuario_inactivado_por_sql_pierde_acceso(self):
        objetivo = _crear_usuario("prueba.inactivo@test.local", ["operaciones"])
        cliente, _ = _login("prueba.inactivo@test.local", CLAVE)
        with _db() as conn:
            conn.execute("UPDATE usuarios SET estado = 'INACTIVO' WHERE id = ?", (objetivo,))
        self.assertEqual(cliente.get("/operaciones/seguimientos-cliente").status_code, 401)

    def test_cambio_de_correo_no_rompe_la_sesion(self):
        objetivo = _crear_usuario("prueba.correo@test.local", ["operaciones"])
        cliente, _ = _login("prueba.correo@test.local", CLAVE)
        with _db() as conn:
            conn.execute("UPDATE usuarios SET correo = 'prueba.correo2@test.local' WHERE id = ?", (objetivo,))
        self.assertEqual(cliente.get("/me").json()["usuario"], "prueba.correo2@test.local")

    def test_vencimiento_por_inactividad_y_absoluto(self):
        pasado = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        for columna in ("expira_en", "expira_absoluta_en"):
            cliente, respuesta = _login(CORREO_OPER, CLAVE)
            token = _token_de(respuesta)
            with _db() as conn:
                conn.execute(f"UPDATE sesiones SET {columna} = ? WHERE token_hash = ?", (pasado, main._sesion_token_hash(token)))
            self.assertEqual(cliente.get("/operaciones/seguimientos-cliente").status_code, 401, columna)

    def test_actividad_renueva_el_vencimiento_sin_superar_el_maximo(self):
        cliente, respuesta = _login(CORREO_OPER, CLAVE)
        token_hash = main._sesion_token_hash(_token_de(respuesta))
        hace_5_min = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        with _db() as conn:
            conn.execute("UPDATE sesiones SET ultima_actividad = ?, expira_en = ? WHERE token_hash = ?",
                         (hace_5_min, (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"), token_hash))
        respuesta = cliente.get("/session/actividad")
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNotNone(_token_de(respuesta), "La respuesta debe renovar la cookie")
        with _db() as conn:
            fila = conn.execute("SELECT * FROM sesiones WHERE token_hash = ?", (token_hash,)).fetchone()
        self.assertGreater(fila["ultima_actividad"], hace_5_min)
        self.assertGreater(fila["expira_en"], (datetime.now(timezone.utc) + timedelta(minutes=55)).strftime("%Y-%m-%d %H:%M:%S"))
        self.assertLessEqual(fila["expira_en"], fila["expira_absoluta_en"])

    def test_la_cookie_legada_se_elimina_del_navegador(self):
        cliente = _cliente_con_cookie(main.LEGACY_AUTH_COOKIE_NAME, "admin")
        respuesta = cliente.get("/login")
        borrado = [v for v in respuesta.headers.get_list("set-cookie") if v.startswith(f"{main.LEGACY_AUTH_COOKIE_NAME}=")]
        self.assertTrue(borrado and "max-age=0" in borrado[0].lower())

    # --- Migración ----------------------------------------------------------
    def test_migracion_idempotente_y_sin_perdida_de_datos(self):
        with _db() as conn:
            main.migrar_sesiones(conn)
            main.migrar_sesiones(conn)
        self.assertIn("sesiones", CONTEOS_DESPUES_INIT)
        for tabla, cantidad in CONTEOS_ANTES.items():
            if tabla == "sesiones":
                continue
            self.assertEqual(CONTEOS_DESPUES_INIT.get(tabla), cantidad, f"init_sqlite cambió la cantidad de filas de {tabla}")


def tearDownModule():
    _SERVIDOR.should_exit = True
    time.sleep(0.3)
    shutil.rmtree(TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
