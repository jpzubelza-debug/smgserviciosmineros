"""Verificación pública de certificados: QR del PDF, página /verificar, migración de la columna indexada y límites.

Igual que test_certificados: se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal (DATA_DIR se fija
antes de importar main). Ejecutar en un proceso propio:

    .venv\\Scripts\\python.exe -m unittest tests.test_verificacion -v

La base de origen se elige con SMG_TEST_DB_ORIGEN (por defecto, dashboard.db, abierta en solo lectura).
"""
import hashlib
import io
import json
import os
import re
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
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_verificacion_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")
_origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
_destino = sqlite3.connect(DB_COPIA)
_origen.backup(_destino)
_destino.close()
_origen.close()


def _huellas(ruta):
    """Columnas visibles (table_info no lista las generadas) y filas de cada tabla."""
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        resultado = {}
        for (tabla,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            columnas = [r[1] for r in conn.execute(f'PRAGMA table_info("{tabla}")')]
            filas = conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in columnas)} FROM "{tabla}" ORDER BY rowid').fetchall()
            resultado[tabla] = (columnas, filas)
        return resultado
    finally:
        conn.close()


HUELLAS_ANTES = _huellas(DB_COPIA)
with sqlite3.connect(f"file:{DB_COPIA}?mode=ro", uri=True) as _conn:
    # Certificados emitidos antes del cambio: sus PDF no llevan QR, pero el código debe poder consultarse.
    PREVIOS = _conn.execute("SELECT id, snapshot_json FROM certificados_cliente WHERE estado <> 'BORRADOR'").fetchall() \
        if _conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'certificados_cliente'").fetchone() else []
URL_BASE = "https://verificacion.prueba.local/verificar"
os.environ.update({"DATA_DIR": TMP_DIR, "APP_LOGIN_USER": "contingencia_test", "APP_LOGIN_PASSWORD": "clave-contingencia-test",
                   "CERT_URL_VERIFICACION": URL_BASE + "/", "CERT_VERIFICACION_PROXY": "1"})

import main  # noqa: E402
import certificado_pdf  # noqa: E402
import verificacion_certificados as verificacion  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB_COPIA), "Las pruebas no apuntan a la copia"
HUELLAS_DESPUES = _huellas(DB_COPIA)

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

CLAVE = "Clave-Prueba-1"
CERT = "/operaciones/seguimiento-cliente/{sid}/certificados"
ACCIONES = ["consultar", "certificar", "emitir_certificado", "anular_certificado", "cambiar_modalidad"]
FORMATO = re.compile(r"^[0-9A-F]{12}$")


def _db():
    conn = sqlite3.connect(DB_COPIA, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def _crear_usuario(correo):
    with _db() as conn:
        conn.execute("DELETE FROM usuarios WHERE correo = ?", (correo,))
        conn.execute(
            "INSERT INTO usuarios (nombre_apellido, correo, password_hash, estado, tipo_usuario, modulos_json, paneles_json, "
            "acciones_json, bloqueado, intentos_fallidos) VALUES (?, ?, ?, 'ACTIVO', 'OPERADOR', ?, ?, ?, 0, 0)",
            ("Usuario Interno Verificación", correo, hashlib.sha256(CLAVE.encode()).hexdigest(), json.dumps(["operaciones"]),
             json.dumps({"operaciones": ["seguimiento_cliente"]}), json.dumps({"operaciones": {"seguimiento_cliente": ACCIONES}})),
        )


def _cliente_logueado(correo):
    cliente = httpx.Client(base_url=BASE_URL, follow_redirects=False, trust_env=False, timeout=60)
    assert cliente.post("/login", data={"username": correo, "password": CLAVE}).headers.get("location") == "/"
    return cliente


def _publico(ip="203.0.113.10"):
    """Cliente anónimo; la IP se simula con X-Forwarded-For (el servidor toma la última, como detrás de Render)."""
    return httpx.Client(base_url=BASE_URL, follow_redirects=False, trust_env=False, timeout=60, headers={"X-Forwarded-For": ip})


_SECUENCIA = iter(range(1, 10**6))


def _crear_seg(cliente_nombre="CLIENTE VERIFICACIÓN SA", obra=None, oc="OC-VERIF-1"):
    n = next(_SECUENCIA)
    obra = obra or f"OBRA DE PRUEBA QR {n}"  # centros_costos.nombre es único
    with _db() as conn:
        codigo = f"VQR{n:05d}"
        conn.execute("INSERT INTO clientes (codigo, razon_social) VALUES (?, ?)", (codigo, cliente_nombre))
        cc = conn.execute("INSERT INTO centros_costos (nombre, activo) VALUES (?, 1)", (obra,)).lastrowid
        sid = conn.execute(
            "INSERT INTO seguimiento_cc (centro_costo_id, estado_seguimiento, cotizacion, cliente_codigo, fecha_ultima_actualizacion) "
            "VALUES (?, 'En curso', 'COT-VQR', ?, CURRENT_TIMESTAMP)", (cc, codigo)).lastrowid
        oid = conn.execute("INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, revision, moneda, monto_oc) "
                           "VALUES (?, 1, ?, 'R1', 'ARS', 500000)", (sid, oc)).lastrowid
    return sid, oid


def _payload(oid, importe, **extra):
    return {"fecha_certificado": "2026-10-31", "periodo_desde": "2026-10-01", "periodo_hasta": "2026-10-31", "moneda": "ARS",
            "tipo": "PARCIAL", "modalidad": "GLOBAL", "alcance": "OC", "orden_compra_cliente_id": oid,
            "lineas": [{"orden_compra_cliente_id": oid, "importe_periodo": importe}], **extra}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _crear_usuario("verif.admin@test.local")
        cls.admin = _cliente_logueado("verif.admin@test.local")

    def setUp(self):
        main._LIMITADOR_VERIFICACION = verificacion.LimitadorConsultas()  # cada prueba empieza sin consultas previas

    def ok(self, respuesta, codigo=200):
        self.assertEqual(respuesta.status_code, codigo, respuesta.text[:500])
        return respuesta.json()

    def emitir(self, sid, oid, importe, **extra):
        datos = self.ok(self.admin.post(CERT.format(sid=sid), json=_payload(oid, importe, **extra)))
        cid = datos["certificado_id"]
        huella = datos["calculo"]["reajuste"]["huella"]
        self.ok(self.admin.post(CERT.format(sid=sid) + f"/{cid}/emitir", json={"huella_reajustes": huella}))
        return self.certificado(cid)

    @staticmethod
    def certificado(cid):
        with _db() as conn:
            fila = conn.execute("SELECT * FROM certificados_cliente WHERE id = ?", (cid,)).fetchone()
        return dict(fila)

    def pagina(self, codigo, ip="203.0.113.10"):
        with _publico(ip) as c:
            return c.get(f"/verificar/{codigo}")


# ---------------------------------------------------------------------------
class A_Migracion(Base):
    def test_a1_columna_generada_e_indice_unico(self):
        with _db() as conn:
            columna = [r for r in conn.execute("PRAGMA table_xinfo(certificados_cliente)") if r[1] == "codigo_verificacion"]
            self.assertEqual(len(columna), 1)
            self.assertEqual(columna[0][6], 2, "debe ser una columna generada VIRTUAL (hidden = 2)")
            indice = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'idx_cert_codigo_verificacion'").fetchone()[0]
            self.assertIn("CREATE UNIQUE INDEX", indice)
            self.assertIn("WHERE codigo_verificacion IS NOT NULL", indice)
            plan = " ".join(r[-1] for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM certificados_cliente WHERE codigo_verificacion = 'ABCDEF123456'"))
            self.assertIn("idx_cert_codigo_verificacion", plan)

    def test_a2_no_modifica_datos_y_es_idempotente(self):
        # La columna generada no aparece en table_info: todas las tablas conservan columnas y filas.
        # El arranque también crea las tablas del módulo Facturación (fac_*): son nuevas y no tocan las existentes.
        # Si la base ya trae el módulo, sus tablas fac_ conservan columnas y filas; solo pueden recibir al final las
        # columnas que Facturación declara en FAC_COLUMNAS (p. ej. el detalle de pagos previos de la v2).
        self.assertEqual({t: h for t, h in HUELLAS_DESPUES.items() if t in HUELLAS_ANTES and not t.startswith("fac_")},
                         {t: h for t, h in HUELLAS_ANTES.items() if not t.startswith("fac_")})
        for t, (columnas, filas) in HUELLAS_ANTES.items():
            if t.startswith("fac_"):
                cols_despues, filas_despues = HUELLAS_DESPUES[t]
                self.assertEqual(cols_despues[:len(columnas)], columnas, f"cambiaron las columnas previas de {t}")
                self.assertTrue(set(cols_despues[len(columnas):]) <= {c for c, _ in main.fac.FAC_COLUMNAS.get(t, ())}, t)
                self.assertEqual([f[:len(columnas)] for f in filas_despues], filas, f"cambiaron filas de {t}")
        self.assertTrue(all(t.startswith("fac_") for t in set(HUELLAS_DESPUES) - set(HUELLAS_ANTES)))
        with _db() as conn:
            esquema = sorted(map(tuple, conn.execute("SELECT type, name, sql FROM sqlite_master")))
            main.migrar_verificacion_certificados(conn)
            main.migrar_verificacion_certificados(conn)
            self.assertEqual(sorted(map(tuple, conn.execute("SELECT type, name, sql FROM sqlite_master"))), esquema)

    def test_a3_codigos_existentes_y_columna_no_escribible(self):
        with _db() as conn:
            filas = conn.execute("SELECT id, estado, snapshot_json, codigo_verificacion FROM certificados_cliente").fetchall()
            for fila in filas:
                if fila["snapshot_json"]:
                    self.assertEqual(fila["codigo_verificacion"], json.loads(fila["snapshot_json"])["codigo_verificacion"])
                else:
                    self.assertIsNone(fila["codigo_verificacion"])  # borradores: sin código
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE certificados_cliente SET codigo_verificacion = 'ABCDEF123456'")

    def test_a4_certificados_ya_emitidos_se_consultan(self):
        """Los certificados emitidos antes del cambio (p. ej. el N° 1, sin QR en su PDF) se verifican con el código."""
        for cid, snapshot_json in PREVIOS:
            snapshot = json.loads(snapshot_json)
            respuesta = self.pagina(snapshot["codigo_verificacion"].lower())  # también en minúsculas
            self.assertEqual(respuesta.status_code, 200)
            self.assertIn(snapshot["certificado"]["numero_texto"], respuesta.text)
            self.assertIsNone(certificado_pdf.url_verificacion(snapshot), "un PDF ya emitido no se regenera con QR")


# ---------------------------------------------------------------------------
class B_Pdf(Base):
    def test_b1_emision_congela_url_y_codigo_integro(self):
        sid, oid = _crear_seg()
        cert = self.emitir(sid, oid, 10000)
        snapshot = json.loads(cert["snapshot_json"])
        codigo = snapshot["codigo_verificacion"]
        self.assertRegex(codigo, FORMATO)
        self.assertEqual(snapshot["url_verificacion"], f"{URL_BASE}/{codigo}")  # sin barra doble aunque la variable la tenga
        self.assertEqual(cert["codigo_verificacion"], codigo)
        self.assertTrue(verificacion.codigo_integro(snapshot))
        # El QR lleva solo la URL: ni importes ni datos del cliente.
        self.assertNotIn("CLIENTE", snapshot["url_verificacion"])

    def test_b2_pdf_con_qr_y_codigo_impreso(self):
        sid, oid = _crear_seg()
        cert = self.emitir(sid, oid, 12000)
        snapshot = json.loads(cert["snapshot_json"])
        bloque = certificado_pdf._bloque_final(snapshot)
        qrs = [f for f in _recorrer(bloque) if isinstance(f, certificado_pdf._BloqueQR)]
        self.assertEqual([(q.url, q.codigo) for q in qrs], [(snapshot["url_verificacion"], snapshot["codigo_verificacion"])])
        pdf = self.admin.get(CERT.format(sid=sid) + f"/{cert['id']}/pdf").content
        texto = _texto_pdf(pdf)
        self.assertGreaterEqual(texto.count(snapshot["codigo_verificacion"]), 2)  # bajo el QR y en el pie

    def test_b3_sin_url_configurada_no_hay_qr(self):
        sid, oid = _crear_seg()
        snapshot = json.loads(self.emitir(sid, oid, 9000)["snapshot_json"])
        snapshot.pop("url_verificacion")
        self.assertIsNone(certificado_pdf.url_verificacion(snapshot))
        self.assertFalse([f for f in _recorrer(certificado_pdf._bloque_final(snapshot)) if isinstance(f, certificado_pdf._BloqueQR)])
        self.assertIn(snapshot["codigo_verificacion"], _texto_pdf(certificado_pdf.generar_pdf(snapshot)))  # el código sigue en el pie

    def test_b4_borrador_sin_qr(self):
        sid, oid = _crear_seg()
        cid = self.ok(self.admin.post(CERT.format(sid=sid), json=_payload(oid, 5000)))["certificado_id"]
        texto = _texto_pdf(self.admin.get(CERT.format(sid=sid) + f"/{cid}/pdf").content)
        self.assertIn("vista previa sin validez", texto)
        self.assertIsNone(self.certificado(cid)["codigo_verificacion"])


# ---------------------------------------------------------------------------
class C_Pagina(Base):
    def test_c1_vigente_muestra_solo_los_campos_aprobados(self):
        sid, oid = _crear_seg(cliente_nombre="MINERA VERIFICA SA", obra="CAMINO NORTE QR", oc="OC-7788")
        cert = self.emitir(sid, oid, 24000, observaciones="NOTA-INTERNA-SECRETA")
        codigo = cert["codigo_verificacion"]
        respuesta = self.pagina(codigo)
        self.assertEqual(respuesta.status_code, 200)
        texto = respuesta.text
        for valor in ("Registro encontrado: certificado vigente", cert["fecha_certificado"][:4], "Octubre 2026", "MINERA VERIFICA SA",
                      "CAMINO NORTE QR", "OC-7788 R1", "$ 24.000,00", codigo):
            self.assertIn(valor, texto)
        numero = json.loads(cert["snapshot_json"])["certificado"]["numero_texto"]
        self.assertIn(numero, texto)
        # Ningún dato interno: usuario emisor, observaciones, ids internos, rutas, hash del PDF.
        for interno in ("verif.admin@test.local", "Usuario Interno", "NOTA-INTERNA-SECRETA", cert["pdf_sha256"], cert["pdf_ruta"],
                        f"seguimiento_cc_id", "COT-VQR"):
            self.assertNotIn(interno, texto)
        self.assertIn("un código válido por sí solo no prueba la autenticidad", texto)

    def test_c2_publica_sin_sesion_y_cabeceras(self):
        sid, oid = _crear_seg()
        codigo = self.emitir(sid, oid, 1000)["codigo_verificacion"]
        respuesta = self.pagina(codigo)
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("set-cookie", respuesta.headers)
        self.assertEqual(respuesta.headers["cache-control"], "no-store")
        self.assertIn("noindex", respuesta.headers["x-robots-tag"])
        self.assertIn('content="noindex, nofollow"', respuesta.text)
        self.assertEqual(respuesta.headers["referrer-policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", respuesta.headers["content-security-policy"])

    def test_c3_anulado_sin_motivo_ni_usuario(self):
        sid, oid = _crear_seg()
        cert = self.emitir(sid, oid, 3000)
        self.ok(self.admin.post(CERT.format(sid=sid) + f"/{cert['id']}/anular", json={"motivo": "MOTIVO-INTERNO de anulación"}))
        texto = self.pagina(cert["codigo_verificacion"]).text
        self.assertIn("Certificado ANULADO", texto)
        self.assertNotIn("MOTIVO-INTERNO", texto)
        self.assertNotIn("verif.admin@test.local", texto)

    def test_c4_reemplazado_indica_el_reemplazo(self):
        sid, oid = _crear_seg()
        original = self.emitir(sid, oid, 4000)
        self.ok(self.admin.post(CERT.format(sid=sid) + f"/{original['id']}/anular", json={"motivo": "Error en la medición del mes"}))
        nuevo = self.emitir(sid, oid, 3800, reemplaza_a_id=original["id"])
        texto = self.pagina(original["codigo_verificacion"]).text
        self.assertIn("Certificado reemplazado", texto)
        self.assertIn(json.loads(nuevo["snapshot_json"])["certificado"]["numero_texto"], texto)
        self.assertNotIn(nuevo["codigo_verificacion"], texto)  # no se expone el código del reemplazo
        self.assertIn("Registro encontrado: certificado vigente", self.pagina(nuevo["codigo_verificacion"]).text)

    def test_c5_codigo_invalido_e_inexistente_responden_igual(self):
        respuestas = [self.pagina(c, ip=f"198.51.100.{i}") for i, c in enumerate(
            ("ABCDEF123456", "ZZZZZZZZZZZZ", "123", "C822C36BAAF5X", "%3Cscript%3E", "000000000000"))]
        self.assertEqual({r.status_code for r in respuestas}, {404})
        self.assertEqual(len({r.text for r in respuestas}), 1, "la respuesta no debe distinguir formato inválido de inexistente")
        self.assertNotIn("<script>", respuestas[0].text)
        # Un borrador no tiene código y no se encuentra.
        self.assertIn("Código no encontrado", respuestas[0].text)

    def test_c6_formulario_y_redireccion(self):
        with _publico() as c:
            self.assertIn('name="codigo"', c.get("/verificar").text)
            self.assertEqual(c.get("/verificar", params={"codigo": " c822c36baaf5 "}).headers["location"], "/verificar/C822C36BAAF5")
            self.assertEqual(c.get("/verificar", params={"codigo": "nada"}).headers["location"], "/verificar/invalido")

    def test_c7_escapa_html_de_los_datos(self):
        sid, oid = _crear_seg(cliente_nombre="Pérez & Hijos <script>alert(1)</script>")
        texto = self.pagina(self.emitir(sid, oid, 1500)["codigo_verificacion"]).text
        self.assertNotIn("<script>alert(1)</script>", texto)
        self.assertIn("Pérez &amp; Hijos &lt;script&gt;", texto)


# ---------------------------------------------------------------------------
class D_Autenticidad(Base):
    def test_d1_documento_alterado_se_detecta(self):
        """Un PDF adulterado que conserva un código válido muestra datos distintos de los del registro original."""
        sid, oid = _crear_seg(cliente_nombre="CLIENTE ORIGINAL SA")
        cert = self.emitir(sid, oid, 10000)
        original = _texto_pdf(self.admin.get(CERT.format(sid=sid) + f"/{cert['id']}/pdf").content)
        adulterado = original.replace("10.000,00", "90.000,00").replace("CLIENTE ORIGINAL SA", "OTRO CLIENTE SA")
        self.assertIn(cert["codigo_verificacion"], adulterado)  # el falsificador conserva el código
        pagina = self.pagina(cert["codigo_verificacion"]).text
        self.assertIn("$ 10.000,00", pagina)
        self.assertIn("CLIENTE ORIGINAL SA", pagina)
        self.assertNotIn("90.000,00", pagina)
        self.assertNotIn("OTRO CLIENTE SA", pagina)

    def test_d2_registro_alterado_no_es_verificable(self):
        """Si alguien modificara el registro saltando los controles, el código recalculado no coincide y no se valida."""
        sid, oid = _crear_seg()
        cert = self.emitir(sid, oid, 7000)
        snapshot = json.loads(cert["snapshot_json"])
        snapshot["certificado"]["total_a_facturar"] = 70000.0
        with _db() as conn:
            conn.execute("DROP TRIGGER trg_cert_emitido_inmutable")
            conn.execute("UPDATE certificados_cliente SET snapshot_json = ? WHERE id = ?",
                         (json.dumps(snapshot, ensure_ascii=False, sort_keys=True), cert["id"]))
            main.migrar_certificados_cliente(conn)  # restaura el trigger para el resto de las pruebas
            self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'trg_cert_emitido_inmutable'").fetchone())
        texto = self.pagina(cert["codigo_verificacion"]).text
        self.assertIn("No se puede verificar", texto)
        self.assertNotIn("70.000,00", texto)

    def test_d3_codigo_integro_cubre_los_datos_publicos(self):
        sid, oid = _crear_seg()
        snapshot = json.loads(self.emitir(sid, oid, 2500)["snapshot_json"])
        for ruta in (("certificado", "total_a_facturar"), ("certificado", "periodo_texto"), ("certificado", "numero_texto"),
                     ("cliente", "razon_social"), ("obra", "nombre"), ("emision", "fecha")):
            alterado = json.loads(json.dumps(snapshot))
            alterado[ruta[0]][ruta[1]] = "X"
            self.assertFalse(verificacion.codigo_integro(alterado), ruta)
        alterado = json.loads(json.dumps(snapshot))
        alterado["ordenes"][0]["orden_compra"] = "X"
        self.assertFalse(verificacion.codigo_integro(alterado))


# ---------------------------------------------------------------------------
class E_Limites(Base):
    def test_e1_intentos_fallidos_bloquean_la_ip(self):
        sid, oid = _crear_seg()
        codigo = self.emitir(sid, oid, 1000)["codigo_verificacion"]
        ip = "192.0.2.50"
        for i in range(verificacion.LIMITE_FALLIDAS_IP[0]):
            self.assertEqual(self.pagina(f"{i:012X}", ip=ip).status_code, 404)
        bloqueada = self.pagina(codigo, ip=ip)  # bloqueada aun con un código válido
        self.assertEqual(bloqueada.status_code, 429)
        self.assertNotIn(codigo, bloqueada.text)
        self.assertEqual(self.pagina(codigo, ip="192.0.2.51").status_code, 200)  # otra IP sigue consultando

    def test_e2_limite_de_consultas_por_ip(self):
        sid, oid = _crear_seg()
        codigo = self.emitir(sid, oid, 1000)["codigo_verificacion"]
        for _ in range(verificacion.LIMITE_IP[0]):
            self.assertEqual(self.pagina(codigo, ip="192.0.2.60").status_code, 200)
        self.assertEqual(self.pagina(codigo, ip="192.0.2.60").status_code, 429)

    def test_e3_ip_falsificada_en_x_forwarded_for_no_evade_el_limite(self):
        for i in range(verificacion.LIMITE_FALLIDAS_IP[0]):
            # El atacante cambia la primera IP del encabezado; el proxy agrega la real al final.
            self.pagina("ABCDEF123456", ip=f"10.0.0.{i}, 192.0.2.70")
        self.assertEqual(self.pagina("ABCDEF123456", ip="10.9.9.9, 192.0.2.70").status_code, 429)

    def test_e4_limite_global_frena_la_enumeracion_distribuida(self):
        total = verificacion.LIMITE_FALLIDAS_TOTAL[0]
        for i in range(total):
            main._LIMITADOR_VERIFICACION.registrar_fallida(f"172.16.{i // 250}.{i % 250}")
        self.assertEqual(self.pagina("ABCDEF123456", ip="192.0.2.80").status_code, 429)

    def test_e5_ventanas_vencen(self):
        reloj = [1000.0]
        limitador = verificacion.LimitadorConsultas(reloj=lambda: reloj[0])
        for _ in range(verificacion.LIMITE_FALLIDAS_IP[0]):
            limitador.registrar_fallida("1.1.1.1")
        self.assertFalse(limitador.permitir("1.1.1.1"))
        reloj[0] += verificacion.BLOQUEO_SEGUNDOS + verificacion.LIMITE_FALLIDAS_IP[1] + 1
        self.assertTrue(limitador.permitir("1.1.1.1"))

    def test_e6_sin_proxy_de_confianza_se_usa_la_conexion(self):
        class Pedido:
            headers = {"x-forwarded-for": "8.8.8.8"}
            client = type("C", (), {"host": "127.0.0.1"})()
        self.assertEqual(verificacion.ip_cliente(Pedido, False), "127.0.0.1")
        self.assertEqual(verificacion.ip_cliente(Pedido, True), "8.8.8.8")


def _recorrer(flowable):
    """Todos los flowables anidados (tablas, celdas e ImageAndFlowables)."""
    pendientes, vistos = [flowable], []
    while pendientes:
        f = pendientes.pop()
        vistos.append(f)
        if hasattr(f, "_cellvalues"):
            for fila in f._cellvalues:
                for celda in fila:
                    pendientes.extend(celda if isinstance(celda, (list, tuple)) else [celda])
        for atributo in ("_I", "_F"):
            valor = getattr(f, atributo, None)
            if valor is not None:
                pendientes.extend(valor if isinstance(valor, (list, tuple)) else [valor])
    return vistos


def _texto_pdf(contenido):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(contenido)).pages)


if __name__ == "__main__":
    unittest.main()
