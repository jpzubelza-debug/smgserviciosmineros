"""Importación masiva de horas_maquinas (/api/admin/importar-horas-maquinas) y exportar_horas_maquinas.py.

Se ejecuta sobre una COPIA de dashboard.db en una carpeta temporal (DATA_DIR se fija antes de importar main); la
base de origen (SMG_TEST_DB_ORIGEN, por defecto dashboard.db) se abre en solo lectura. En la copia se vacía
horas_maquinas para simular la base de Render. Ejecutar en un proceso propio:

    .venv\\Scripts\\python.exe -m unittest tests.test_importar_horas_maquinas -v
"""
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
DB_REAL = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_import_horas_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")
EXPORT = os.path.join(TMP_DIR, "export", "horas_maquinas_export.db")
os.makedirs(os.path.dirname(EXPORT))

_origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
_destino = sqlite3.connect(DB_COPIA)
_origen.backup(_destino)
_destino.close()
_origen.close()

os.environ.update({"DATA_DIR": TMP_DIR, "APP_LOGIN_USER": "contingencia_test", "APP_LOGIN_PASSWORD": "clave-contingencia-test"})

import main  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from exportar_horas_maquinas import exportar  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB_COPIA), "Las pruebas no apuntan a la copia"

_sock = socket.socket()
_sock.bind(("127.0.0.1", 0))
PUERTO = _sock.getsockname()[1]
_sock.close()
BASE_URL = f"http://127.0.0.1:{PUERTO}"
_SERVIDOR = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PUERTO, log_level="warning", lifespan="off"))
threading.Thread(target=_SERVIDOR.run, daemon=True).start()
for _ in range(200):
    if _SERVIDOR.started:
        break
    time.sleep(0.05)


def _cliente():
    return httpx.Client(base_url=BASE_URL, follow_redirects=False, trust_env=False, timeout=120)

URL = "/api/admin/importar-horas-maquinas"


def _filas(ruta, sin_id=False):
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        columnas = [c[1] for c in conn.execute("PRAGMA table_info(horas_maquinas)") if not (sin_id and c[1] in ("id", "created_at"))]
        return conn.execute(f"SELECT {', '.join(columnas)} FROM horas_maquinas ORDER BY id").fetchall()
    finally:
        conn.close()


class ImportarHorasMaquinas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.total = exportar(DB_COPIA, EXPORT)
        with sqlite3.connect(DB_COPIA) as conn:
            conn.execute("DELETE FROM horas_maquinas")
        cls.cliente = _cliente()
        r = cls.cliente.post("/login", data={"username": "contingencia_test", "password": "clave-contingencia-test"})
        assert r.headers.get("location") == "/", r.text

    def _subir(self, modo, simular, ruta=EXPORT, cliente=None):
        with open(ruta, "rb") as f:
            return (cliente or self.cliente).post(URL, files={"archivo": ("export.db", f)},
                                                  data={"modo": modo, "simular": "true" if simular else "false"})

    def test_flujo_completo(self):
        self.assertGreater(self.total, 0)
        self.assertEqual(len(_filas(EXPORT)), self.total)

        r = self._subir("agregar", simular=True)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["insertadas"], r.json()["filas_despues"], r.json()["respaldo"]), (self.total, self.total, None))
        self.assertEqual(len(_filas(DB_COPIA)), 0, "la simulación no debe guardar nada")

        r = self._subir("agregar", simular=False)
        self.assertEqual(r.status_code, 200, r.text)
        datos = r.json()
        self.assertEqual((datos["insertadas"], datos["omitidas_existentes"], datos["filas_despues"]), (self.total, 0, self.total))
        self.assertTrue(os.path.exists(os.path.join(TMP_DIR, datos["respaldo"])))
        self.assertEqual(_filas(DB_COPIA, sin_id=True), _filas(EXPORT, sin_id=True))

        r = self._subir("agregar", simular=False)
        self.assertEqual((r.json()["insertadas"], r.json()["omitidas_existentes"], r.json()["filas_despues"]), (0, self.total, self.total))

        with sqlite3.connect(DB_COPIA) as conn:
            conn.execute("DELETE FROM horas_maquinas WHERE id IN (SELECT id FROM horas_maquinas ORDER BY id LIMIT 10)")
        r = self._subir("agregar", simular=False)
        self.assertEqual((r.json()["insertadas"], r.json()["filas_despues"]), (10, self.total))

        r = self._subir("reemplazar", simular=False)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["filas_antes"], r.json()["filas_despues"]), (self.total, self.total))
        self.assertEqual(_filas(DB_COPIA), _filas(EXPORT), "reemplazar conserva id y contenido")
        self.assertFalse([n for n in os.listdir(TMP_DIR) if n.startswith("import_horas_")], "quedó un temporal")

    def test_archivo_invalido(self):
        texto = os.path.join(TMP_DIR, "export", "no_es_db.db")
        with open(texto, "w") as f:
            f.write("hola")
        self.assertEqual(self._subir("agregar", True, ruta=texto).status_code, 400)

        sin_tabla = os.path.join(TMP_DIR, "export", "sin_tabla.db")
        sqlite3.connect(sin_tabla).execute("CREATE TABLE otra (x)").connection.close()
        r = self._subir("agregar", True, ruta=sin_tabla)
        self.assertEqual(r.status_code, 400)
        self.assertIn("horas_maquinas", r.json()["detail"])

        self.assertEqual(self._subir("pisar", True).status_code, 400)

    def test_requiere_administrador(self):
        anonimo = _cliente()
        self.assertEqual(self._subir("agregar", True, cliente=anonimo).status_code, 403)
        self.assertEqual(anonimo.get("/admin/importar-horas-maquinas").status_code, 302)
        self.assertEqual(self.cliente.get("/admin/importar-horas-maquinas").status_code, 200)


if __name__ == "__main__":
    unittest.main()
