"""Pruebas de integración de Facturación y Cobranzas: API real (uvicorn) sobre una COPIA de dashboard.db.

Cubren permisos, relaciones entre documentos, saldos por moneda, notas de crédito, cobranzas parciales,
anulaciones, auditoría, adjuntos opcionales, idempotencia de la migración y que las tablas existentes no cambien.
DATA_DIR se fija antes de importar main (importarlo ejecuta init_sqlite). Ejecutar en un proceso propio:

    .venv-1\\Scripts\\python.exe -m unittest tests.test_facturacion_api -v   (entorno con requirements.txt: incluye pypdf)

La fecha de corte 2025-12-31 es SOLO un dato de prueba: la real está pendiente de Contabilidad.
"""
import hashlib
import json
import os
import shutil
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
DB_ORIGEN = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_facturacion_api_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")
SHA_ORIGEN_ANTES = hashlib.sha256(open(DB_ORIGEN, "rb").read()).hexdigest()

origen = sqlite3.connect(f"file:{DB_ORIGEN}?mode=ro", uri=True)
destino = sqlite3.connect(DB_COPIA)
origen.backup(destino)
destino.close()
origen.close()

# init_sqlite() ya reimportaba Clientes.xlsx en cada arranque y renovaba clientes.updated_at (comportamiento previo).
COLUMNAS_IGNORADAS = {"clientes": {"updated_at"}}
# Tablas que las propias pruebas modifican al crear usuarios e iniciar sesión.
TABLAS_DE_LAS_PRUEBAS = {"usuarios", "sesiones", "historial_accesos", "responsables_modulo"}


def _huellas(ruta):
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        res = {}
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")') if r[1] not in COLUMNAS_IGNORADAS.get(t, set())]
            filas = conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}" ORDER BY rowid').fetchall()
            res[t] = (len(filas), hashlib.sha256(repr(filas).encode()).hexdigest(), cols)
        return res
    finally:
        conn.close()


def _esquema(ruta):
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()


HUELLAS_ANTES, ESQUEMA_ANTES = _huellas(DB_COPIA), _esquema(DB_COPIA)
DB_ANTES = os.path.join(TMP_DIR, "antes_de_iniciar.db")  # foto de la base de origen, para comparar filas
shutil.copyfile(DB_COPIA, DB_ANTES)
# Las pruebas operan el módulo: cambian el corte (fecha, confirmación) y la configuración. En el resto de las tablas fac_
# que ya traía la base de origen (pedido emitido, eventos...) sus filas deben quedar intactas.
FAC_MODIFICADAS_POR_LAS_PRUEBAS = {"fac_cortes", "fac_config"}


def _filas_previas(ruta_antes, ruta_despues, tabla, cols):
    """Filas de origen (por rowid) y las mismas filas después, solo en las columnas que ya existían."""
    q = f'SELECT rowid, {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{tabla}" ORDER BY rowid'
    with sqlite3.connect(f"file:{ruta_antes}?mode=ro", uri=True) as a, sqlite3.connect(f"file:{ruta_despues}?mode=ro", uri=True) as d:
        antes = a.execute(q).fetchall()
        ids = {f[0] for f in antes}
        return antes, [f for f in d.execute(q).fetchall() if f[0] in ids]
os.environ["DATA_DIR"] = TMP_DIR
os.environ["APP_LOGIN_USER"] = "contingencia_test"
os.environ["APP_LOGIN_PASSWORD"] = "clave-contingencia-test"

import main  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB_COPIA), "Las pruebas no apuntan a la copia"
HUELLAS_INIT, ESQUEMA_INIT = _huellas(DB_COPIA), _esquema(DB_COPIA)
DB_INIT = os.path.join(TMP_DIR, "despues_de_iniciar.db")
with sqlite3.connect(f"file:{DB_COPIA}?mode=ro", uri=True) as _o, sqlite3.connect(DB_INIT) as _d:
    _o.backup(_d)


def _puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PUERTO = _puerto_libre()
BASE_URL = f"http://127.0.0.1:{PUERTO}"
_SERVIDOR = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PUERTO, log_level="warning", lifespan="off"))
threading.Thread(target=_SERVIDOR.run, daemon=True).start()
for _ in range(200):
    if _SERVIDOR.started:
        break
    time.sleep(0.05)

API = "/api/facturacion"
CLAVE = "Clave-Prueba-1"
CORTE_PRUEBA = "2025-12-31"
EXAR, VIAP, MODULAR, PIRQ = "000070", "000125", "000142", "000069"
U_CARGA, U_RESP, U_CONS, U_SIN = "carga.fac@test", "resp.fac@test", "consulta.fac@test", "sinmodulo.fac@test"


def _db():
    conn = sqlite3.connect(DB_COPIA, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _crear_usuario(correo, modulos, tipo="OPERADOR"):
    with _db() as conn:
        conn.execute(
            "INSERT INTO usuarios (nombre_apellido, correo, password_hash, estado, tipo_usuario, modulos_json, paneles_json, "
            "acciones_json, bloqueado, intentos_fallidos) VALUES (?, ?, ?, 'ACTIVO', ?, ?, '{}', '{}', 0, 0)",
            (correo.split("@")[0].upper(), correo, hashlib.sha256(CLAVE.encode()).hexdigest(), tipo, json.dumps(modulos)))
        return conn.execute("SELECT id FROM usuarios WHERE correo = ?", (correo,)).fetchone()[0]


def _cliente(usuario, clave=CLAVE):
    c = httpx.Client(base_url=BASE_URL, follow_redirects=False, trust_env=False, timeout=60)
    r = c.post("/login", data={"username": usuario, "password": clave})
    assert r.headers.get("location") == "/", f"Login fallido para {usuario}"
    return c


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if getattr(Base, "_listo", False):
            return
        _crear_usuario(U_CARGA, ["facturacion"])
        rid = _crear_usuario(U_RESP, ["facturacion"])
        _crear_usuario(U_CONS, ["facturacion"], tipo="CONSULTOR")
        _crear_usuario(U_SIN, ["operaciones"])
        with _db() as conn:
            conn.execute("INSERT INTO responsables_modulo (modulo, usuario_id) VALUES ('facturacion', ?)", (rid,))
        Base.carga, Base.resp, Base.cons, Base.sin = _cliente(U_CARGA), _cliente(U_RESP), _cliente(U_CONS), _cliente(U_SIN)
        Base.cont = _cliente("contingencia_test", "clave-contingencia-test")
        Base._listo = True

    # ---------------------------------------------------------------- utilidades
    def ok(self, r, codigo=200):
        self.assertEqual(r.status_code, codigo, r.text)
        return r.json() if "json" in r.headers.get("content-type", "") else r

    def err(self, r, codigo_http, codigo=None):
        self.assertEqual(r.status_code, codigo_http, r.text)
        if codigo:
            self.assertEqual(r.json().get("codigo"), codigo, r.text)
        return r.json()

    def validar(self, cliente, situacion):
        """Puesta en marcha: valida el estado inicial del cliente (responsable) si todavía no lo está."""
        if not self.ok(self.carga.get(f"{API}/puesta-en-marcha/{cliente}"))["estado_inicial"]:
            self.ok(self.resp.post(f"{API}/puesta-en-marcha/{cliente}/validar", json={"situacion": situacion, "nota": "Prueba"}))

    def corte_confirmado(self):
        """Corte de PRUEBA confirmado con un saldo inicial de VIAP (cancelado estimado por antigüedad). Puesta en marcha:
        VIAP validado con antecedentes; EXAR, PIRQ y MODULAR, sin antecedentes (operan después del inicio)."""
        corte = self.ok(self.carga.get(API + "/corte"))["corte"]
        if corte and corte["estado"] == "CONFIRMADO":
            for cliente in (EXAR, PIRQ, MODULAR):
                self.validar(cliente, "SIN_ANTECEDENTES")
            return corte
        if not corte:
            self.ok(self.carga.post(API + "/corte", json={}))
        self.ok(self.carga.put(API + "/corte/fecha", json={"fecha_corte": CORTE_PRUEBA}))
        corte = self.ok(self.carga.get(API + "/corte"))["corte"]
        if not self.ok(self.carga.get(API + "/documentos", params={"cliente": VIAP}))[:1]:
            self.doc(VIAP, 6, 445, "2025-10-15", "ARS", "8348404.24", origen="SALDO_INICIAL", corte_id=corte["id"], vto="2025-11-14")
            self.ok(self.carga.post(API + "/corte/conciliacion", json={"cliente_codigo": VIAP, "moneda": "ARS", "saldo_tango": "7630941.23"}))
            self.ok(self.carga.post(API + "/corte/estimar", json={"cliente_codigo": VIAP, "moneda": "ARS", "saldo_tango": "7630941.23"}))
        self.validar(VIAP, "CON_ANTECEDENTES")
        self.ok(self.resp.post(API + "/corte/confirmar"))
        for cliente in (EXAR, PIRQ, MODULAR):
            self.validar(cliente, "SIN_ANTECEDENTES")
        return self.ok(self.carga.get(API + "/corte"))["corte"]

    def doc(self, cliente, pv, numero, fecha, moneda, neto, alic="21", tipo="FC", vto=None, usuario=None, esperado=200, **extra):
        datos = {"tipo": tipo, "letra": "A", "punto_venta": pv, "numero": numero, "cliente_codigo": cliente, "fecha_emision": fecha,
                 "fecha_vencimiento": None if tipo == "NC" else (vto or fecha), "moneda": moneda,
                 "lineas": [{"descripcion": "Servicio", "alicuota_iva": alic, "neto": neto}]}
        if tipo != "FC":
            datos["motivo_codigo"] = extra.pop("motivo_codigo", "OTRO")
        datos.update(extra)
        r = (usuario or self.carga).post(API + "/documentos", json=datos, headers=extra.pop("headers", None) or {})
        if esperado != 200:
            return r
        return self.ok(r)["id"]

    def detalle(self, doc_id):
        return self.ok(self.carga.get(f"{API}/documentos/{doc_id}"))

    def pedido(self, cliente, moneda, lineas, tipo="FACTURA", emitir=True, **extra):
        pid = self.ok(self.carga.post(API + "/pedidos", json={"tipo_solicitado": tipo, "fecha_emision": "2026-10-10", "cliente_codigo": cliente,
                                                              "moneda": moneda, "condicion_pago": "30 FF", "lineas": lineas, **extra}))["id"]
        if emitir:
            self.ok(self.carga.post(f"{API}/pedidos/{pid}/emitir", json={}))
        return pid

    def recibo(self, cliente, numero, fecha, moneda, valores, imputaciones=(), esperado=200):
        r = self.carga.post(API + "/recibos", json={"punto_venta": 1, "numero": numero, "cliente_codigo": cliente, "fecha_pago": fecha,
                                                    "moneda": moneda, "valores": valores, "imputaciones": list(imputaciones)})
        return r if esperado != 200 else self.ok(r)["id"]


class T01Migracion(Base):
    def test_solo_agrega_tablas_fac_y_no_cambia_lo_existente(self):
        # Crea solo las tablas fac_ que faltan (sobre la base real, la v1 ya trae la mayoría).
        nuevas = set(HUELLAS_INIT) - set(HUELLAS_ANTES)
        self.assertEqual(nuevas, set(main.fac.FAC_TABLAS) - set(HUELLAS_ANTES))
        for t, h in HUELLAS_ANTES.items():
            if not t.startswith("fac_"):
                self.assertEqual(HUELLAS_INIT[t], h, f"init_sqlite cambió la tabla existente {t}")
                continue
            # Tabla fac_ existente: mismas filas y columnas previas; solo se agregan al final las declaradas en FAC_COLUMNAS.
            cols, cols_init = h[2], HUELLAS_INIT[t][2]
            self.assertEqual(cols_init[:len(cols)], cols, f"cambiaron las columnas previas de {t}")
            permitidas = {c for c, _ in main.fac.FAC_COLUMNAS.get(t, ())}
            self.assertTrue(set(cols_init[len(cols):]) <= permitidas, f"{t}: columnas no declaradas {cols_init[len(cols):]}")
            antes, despues = _filas_previas(DB_ANTES, DB_INIT, t, cols)
            self.assertEqual((len(despues), despues), (h[0], antes), f"cambiaron filas de {t}")
        for nombre, sql in ESQUEMA_ANTES.items():
            if nombre.startswith("trg_fac_") or nombre in main.fac.FAC_COLUMNAS:
                continue  # los triggers se recrean por diseño; las tablas con columnas nuevas se controlan arriba
            self.assertEqual(ESQUEMA_INIT.get(nombre), sql, f"cambió la definición de {nombre}")
        agregados = set(ESQUEMA_INIT) - set(ESQUEMA_ANTES)
        self.assertTrue(all(n.startswith(("fac_", "ux_fac_", "ix_fac_", "trg_fac_", "sqlite_autoindex_fac_")) for n in agregados), agregados)

    def test_init_repetido_es_idempotente(self):
        antes_esquema, antes = _esquema(DB_COPIA), _huellas(DB_COPIA)
        main.init_sqlite()
        main.init_sqlite()
        self.assertEqual(_esquema(DB_COPIA), antes_esquema)
        despues = _huellas(DB_COPIA)
        for t in antes:
            if t != "fac_eventos":
                self.assertEqual(despues[t], antes[t], f"un init repetido cambió {t}")
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM fac_config").fetchone()[0], 4)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_modulo_registrado_en_menu_y_administracion(self):
        self.assertIn("facturacion", main.ALL_MODULES)
        self.ok(self.cont.put("/admin/responsables-modulo/facturacion",
                              json={"usuario_ids": [r["usuario_id"] for r in self.ok(self.cont.get("/admin/responsables-modulo"))
                                                    if r["modulo"] == "facturacion"]}))
        menu = open(os.path.join(BASE_DIR, "menu_principal.html"), encoding="utf-8").read()
        self.assertIn('key: "facturacion"', menu)
        self.assertIn('href: "/facturacion"', menu)
        self.assertIn('["facturacion", "Facturacion y Cobranzas"]', open(os.path.join(BASE_DIR, "administracion.html"), encoding="utf-8").read())


class T10Permisos(Base):
    def test_acceso_por_perfil(self):
        anonimo = httpx.Client(base_url=BASE_URL, follow_redirects=False, trust_env=False)
        self.assertEqual(anonimo.get(API + "/sesion").status_code, 401)
        self.assertEqual(anonimo.get("/facturacion").status_code, 302)
        self.assertEqual(self.sin.get(API + "/documentos").status_code, 403)
        self.assertEqual(self.sin.get("/facturacion").headers.get("location"), "/")
        pagina = self.carga.get("/facturacion")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("Facturación y Cobranzas", pagina.text)
        s = self.ok(self.cons.get(API + "/sesion"))
        self.assertEqual((s["puede_cargar"], s["es_responsable"]), (False, False))
        s = self.ok(self.carga.get(API + "/sesion"))
        self.assertEqual((s["puede_cargar"], s["es_responsable"]), (True, False))
        self.assertTrue(self.ok(self.resp.get(API + "/sesion"))["es_responsable"])
        self.assertFalse(self.ok(self.cont.get(API + "/sesion"))["es_responsable"], "la cuenta de contingencia no es responsable nominal")

    def test_consulta_no_registra_y_carga_no_anula(self):
        self.corte_confirmado()
        r = self.cons.post(API + "/pedidos", json={"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-10", "cliente_codigo": EXAR,
                                                   "moneda": "USD", "lineas": [{"cantidad": 1, "detalle": "x", "precio_unitario": 1, "alicuota_iva": "21"}]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.doc(EXAR, 6, 9001, "2026-10-01", "USD", "100", usuario=self.cons, esperado=403).status_code, 403)
        d = self.doc(EXAR, 6, 9002, "2026-10-01", "USD", "100")
        motivo = {"motivo": "Error de carga en la prueba de permisos"}
        self.err(self.carga.post(f"{API}/documentos/{d}/anular", json=motivo), 403, "PERMISO")
        self.err(self.cont.post(f"{API}/documentos/{d}/anular", json=motivo), 403, "PERMISO")
        self.err(self.carga.put(API + "/config/adjuntos_requeridos", json={"valor": {"FC": True}}), 403, "PERMISO")
        self.ok(self.resp.post(f"{API}/documentos/{d}/anular", json=motivo))


class T20CorteYSaldosIniciales(Base):
    def test_corte_pendiente_estimacion_marcada_y_revision(self):
        corte = self.ok(self.carga.get(API + "/corte"))["corte"]
        if corte and corte["estado"] == "CONFIRMADO":
            self.skipTest("el corte ya fue confirmado por otra prueba")
        if not corte:
            self.ok(self.carga.post(API + "/corte", json={}))
        corte = self.ok(self.carga.get(API + "/corte"))["corte"]
        self.assertIsNone(corte["fecha_corte"], "la fecha de corte queda pendiente")
        self.assertTrue(self.doc(EXAR, 6, 600, "2026-10-01", "USD", "1000"))  # sin fecha de corte global: se opera
        fc = self.doc(VIAP, 6, 445, "2025-10-15", "ARS", "8348404.24", origen="SALDO_INICIAL", corte_id=corte["id"], vto="2025-11-14")
        self.ok(self.carga.post(API + "/corte/conciliacion", json={"cliente_codigo": VIAP, "moneda": "ARS", "saldo_tango": "7630941.23"}))
        est = self.ok(self.carga.post(API + "/corte/estimar", json={"cliente_codigo": VIAP, "moneda": "ARS", "saldo_tango": "7630941.23"}))
        self.assertEqual(est["criterio"], "ESTIMADO_ANTIGUEDAD")
        self.assertEqual(est["filas"][0]["importe"], 2470627.90)
        d = self.detalle(fc)
        self.assertEqual((d["cancelado_estimado"], d["saldo"]), (2470627.90, 7630941.23))
        self.assertEqual([c["criterio"] for c in d["cancelaciones_corte"]], ["ESTIMADO_ANTIGUEDAD"])
        resumen = self.ok(self.carga.get(API + "/corte"))
        self.assertEqual(resumen["conciliacion"][0]["cancelado_estimado"], 2470627.90)
        self.assertTrue(resumen["conciliacion"][0]["conciliada"])
        cc = self.ok(self.carga.get(API + "/cuenta-corriente", params={"cliente": VIAP, "moneda": "ARS"}))
        self.assertTrue(cc["movimientos"][0]["estimado"])
        self.assertIn("estimado por antigüedad", cc["movimientos"][0]["detalle"])
        self.err(self.resp.post(API + "/corte/confirmar"), 400, "CORTE_FECHA")
        self.ok(self.carga.put(API + "/corte/fecha", json={"fecha_corte": CORTE_PRUEBA}))
        self.err(self.carga.post(API + "/corte/confirmar"), 403, "PERMISO")
        # Puesta en marcha: el cierre exige validar el estado inicial de cada cliente con carga inicial (solo responsable).
        self.err(self.resp.post(API + "/corte/confirmar"), 400, "SIN_VALIDAR")
        self.err(self.carga.post(f"{API}/puesta-en-marcha/{VIAP}/validar", json={"situacion": "CON_ANTECEDENTES"}), 403, "PERMISO")
        self.validar(VIAP, "CON_ANTECEDENTES")
        self.ok(self.resp.post(API + "/corte/confirmar"))
        # Revisión con el corte confirmado: solo responsable y sin cambiar el total.
        nuevas = {str(fc): "2470627.90"}
        self.err(self.carga.post(API + "/corte/revisar", json={"cliente_codigo": VIAP, "moneda": "ARS", "nuevas": nuevas, "criterio": "REVISADO",
                                                                "nota": "Contabilidad confirma la imputación"}), 403, "PERMISO")
        self.err(self.resp.post(API + "/corte/revisar", json={"cliente_codigo": VIAP, "moneda": "ARS", "nuevas": {str(fc): "1000"},
                                                               "criterio": "REVISADO", "nota": "Contabilidad confirma la imputación"}), 400, "TOTAL_CONCILIADO")
        self.ok(self.resp.post(API + "/corte/revisar", json={"cliente_codigo": VIAP, "moneda": "ARS", "nuevas": nuevas, "criterio": "REVISADO",
                                                              "nota": "Contabilidad confirma la imputación por antigüedad"}))
        d = self.detalle(fc)
        self.assertEqual([(c["criterio"], c["estado"]) for c in d["cancelaciones_corte"]], [("ESTIMADO_ANTIGUEDAD", "ANULADO"), ("REVISADO", "VIGENTE")])
        self.assertEqual(d["cancelado_estimado"], 0)
        # Cliente con carga inicial validada: fecha ≤ su carga inicial rechazada; saldo inicial omitido requiere motivo.
        self.err(self.doc(VIAP, 6, 601, CORTE_PRUEBA, "ARS", "1000", esperado=400), 400, "ANTERIOR_AL_CORTE")
        # Un cliente sin carga inicial no tiene ese límite: no hay fecha de corte global.
        self.assertTrue(self.doc(EXAR, 6, 602, CORTE_PRUEBA, "USD", "1000"))
        self.err(self.doc(MODULAR, 6, 484, "2025-09-29", "ARS", "56077075.54", origen="SALDO_INICIAL", corte_id=corte["id"], vto="2025-10-06",
                          esperado=409), 409, "INTEGRIDAD")


class T30PedidosYPdf(Base):
    def test_pedido_desde_certificado_pdf_y_controles(self):
        from pypdf import PdfReader
        import io
        certs = self.ok(self.carga.get(API + "/catalogos"))["certificados"]
        if not certs:
            self.skipTest("la base no tiene certificados emitidos")
        cert = certs[0]
        pre = self.ok(self.carga.get(f"{API}/certificados/{cert['id']}/precarga"))
        self.assertEqual((pre["cliente_codigo"], pre["moneda"]), (cert["cliente_codigo"], cert["moneda"]))
        linea = {**pre["linea"], "precio_unitario": pre["pendiente"]}
        # Borrador: PDF con marca de agua; descartable.
        borrador = self.ok(self.carga.post(API + "/pedidos", json={"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-19",
                                                                   "cliente_codigo": pre["cliente_codigo"], "moneda": pre["moneda"],
                                                                   "oc_texto": pre["oc_texto"], "lineas": [linea]}))["id"]
        texto = PdfReader(io.BytesIO(self.carga.get(f"{API}/pedidos/{borrador}/pdf").content)).pages[0].extract_text()
        self.assertIn("BORRADOR", texto)
        self.ok(self.carga.delete(f"{API}/pedidos/{borrador}"))
        # Parcial sin justificación: rechazado. Con justificación: emitido.
        parcial = {**linea, "precio_unitario": round(pre["pendiente"] / 2, 2)}
        pid = self.ok(self.carga.post(API + "/pedidos", json={"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-19",
                                                              "cliente_codigo": pre["cliente_codigo"], "moneda": pre["moneda"], "lineas": [parcial]}))["id"]
        self.err(self.carga.post(f"{API}/pedidos/{pid}/emitir", json={}), 400, "JUSTIFICACION")
        self.ok(self.carga.put(f"{API}/pedidos/{pid}", json={"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-19", "cliente_codigo": pre["cliente_codigo"],
                                                              "moneda": pre["moneda"], "lineas": [parcial],
                                                              "justificacion_diferencia": "Se factura la mitad por pedido del cliente"}))
        numero = self.ok(self.carga.post(f"{API}/pedidos/{pid}/emitir", json={}))["numero_solicitud"]
        self.assertTrue(numero.startswith("20261019-"))
        pdf = self.carga.get(f"{API}/pedidos/{pid}/pdf")
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        texto = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text()
        for esperado in ("SOLICITUD DE EMISION", "FACTURA", numero, "D.C. 1-9-14", "IVA 21%", "TOTAL"):
            self.assertIn(esperado, texto)
        with _db() as conn:
            sha = conn.execute("SELECT pdf_sha256 FROM fac_pedidos WHERE id = ?", (pid,)).fetchone()[0]
        self.assertEqual(hashlib.sha256(pdf.content).hexdigest(), sha, "el PDF regenerado debe tener el SHA-256 del emitido")
        # Emitido: inmutable; un segundo pedido que supere el certificado se rechaza.
        self.err(self.carga.put(f"{API}/pedidos/{pid}", json={"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-19",
                                                               "cliente_codigo": pre["cliente_codigo"], "moneda": pre["moneda"], "lineas": [parcial]}), 400, "PEDIDO_ESTADO")
        otro = self.ok(self.carga.post(API + "/pedidos", json={"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-19", "cliente_codigo": pre["cliente_codigo"],
                                                               "moneda": pre["moneda"], "lineas": [linea], "justificacion_diferencia": "Prueba de exceso"}))["id"]
        self.err(self.carga.post(f"{API}/pedidos/{otro}/emitir", json={}), 400, "CERTIFICADO_EXCEDE")
        self.ok(self.carga.delete(f"{API}/pedidos/{otro}"))
        # Anulación del pedido emitido: solo responsable, con motivo; el PDF queda marcado ANULADO.
        self.err(self.carga.post(f"{API}/pedidos/{pid}/anular", json={"motivo": "Pedido emitido por error en la prueba"}), 403, "PERMISO")
        self.ok(self.resp.post(f"{API}/pedidos/{pid}/anular", json={"motivo": "Pedido emitido por error en la prueba"}))
        self.assertIn("ANULADO", PdfReader(io.BytesIO(self.carga.get(f"{API}/pedidos/{pid}/pdf").content)).pages[0].extract_text())


class T40Relaciones(Base):
    def test_factura_con_varios_pedidos_y_certificado(self):
        self.corte_confirmado()
        p1 = self.pedido(EXAR, "USD", [{"cantidad": 1, "detalle": "Servicio A", "precio_unitario": 4000, "alicuota_iva": "21"}])
        p2 = self.pedido(EXAR, "USD", [{"cantidad": 1, "detalle": "Servicio B", "precio_unitario": 1500, "alicuota_iva": "21"}])
        fc = self.doc(EXAR, 6, 700, "2026-10-15", "USD", "5000", vto="2026-11-14",
                      vinculos=[{"pedido_id": p1, "neto_asignado": 4000}, {"pedido_id": p2, "neto_asignado": 1000}])
        peds = {p["id"]: p for p in self.ok(self.carga.get(API + "/pedidos", params={"cliente": EXAR}))}
        self.assertEqual((peds[p1]["estado_facturacion"], peds[p1]["facturado"]), ("FACTURADO", 4000))
        self.assertEqual((peds[p2]["estado_facturacion"], peds[p2]["pendiente"]), ("FACTURADO PARCIAL", 500))
        self.assertEqual(len(self.detalle(fc)["vinculos"]), 2)
        # Exceder el pendiente del pedido exige confirmación, que queda en el historial.
        self.err(self.doc(EXAR, 6, 701, "2026-10-16", "USD", "800", vto="2026-11-15", vinculos=[{"pedido_id": p2, "neto_asignado": 800}],
                          esperado=400), 400, "EXCEDE_PENDIENTE")
        fc2 = self.doc(EXAR, 6, 701, "2026-10-16", "USD", "800", vto="2026-11-15", vinculos=[{"pedido_id": p2, "neto_asignado": 800}],
                       confirmar_excedente=True)
        ev = [e for e in self.detalle(fc2)["eventos"] if e["evento"] == "REGISTRADO"][0]
        self.assertEqual(ev["detalle"]["excedidos_confirmados"][0]["pedido_id"], p2)
        # Vínculos que superan el neto del comprobante, tipo de pedido incorrecto y otra moneda: rechazados.
        self.err(self.doc(EXAR, 6, 702, "2026-10-16", "USD", "100", vto="2026-11-15", vinculos=[{"pedido_id": p1, "neto_asignado": 101}],
                          esperado=400), 400, "VINCULO_EXCEDE")
        self.err(self.doc(EXAR, 5, 702, "2026-10-16", "USD", "100", tipo="NC", vinculos=[{"pedido_id": p1, "neto_asignado": 50}],
                          esperado=400), 400, "TIPO_PEDIDO")
        self.err(self.doc(EXAR, 6, 703, "2026-10-16", "ARS", "100", vto="2026-11-15", vinculos=[{"pedido_id": p1, "neto_asignado": 50}],
                          esperado=400), 400, "MONEDA")
        # Pedido con comprobantes vigentes: no se anula.
        self.err(self.resp.post(f"{API}/pedidos/{p1}/anular", json={"motivo": "Intento de anular un pedido ya facturado"}), 400, "PEDIDO_FACTURADO")

    def test_certificado_facturado_y_nc(self):
        self.corte_confirmado()
        certs = [c for c in self.ok(self.carga.get(API + "/catalogos"))["certificados"] if c["pendiente"] > 100]
        if not certs:
            self.skipTest("sin certificados con pendiente")
        c = certs[-1]
        antes = c["pendiente"]
        fc = self.doc(c["cliente_codigo"], 6, 710, "2026-10-15", c["moneda"], "100", vto="2026-11-14",
                      vinculos=[{"certificado_id": c["id"], "neto_asignado": 100}])
        self.doc(c["cliente_codigo"], 5, 710, "2026-10-16", c["moneda"], "40", tipo="NC", documento_origen_id=fc,
                 vinculos=[{"certificado_id": c["id"], "neto_asignado": 40}])
        despues = [x for x in self.ok(self.carga.get(API + "/catalogos"))["certificados"] if x["id"] == c["id"]][0]
        self.assertAlmostEqual(despues["pendiente"], antes - 60, places=2)


class T50MonedasNcYCobros(Base):
    def test_fc_usd_nc_y_cobros_parciales_en_pesos(self):
        self.corte_confirmado()
        fc = self.doc(EXAR, 6, 514, "2026-02-28", "USD", "290000", vto="2026-03-30")
        self.assertEqual(self.detalle(fc)["total"], 350900.0)
        nc = self.doc(EXAR, 5, 185, "2026-05-14", "USD", "240268.24", tipo="NC", motivo_codigo="AJUSTE_PRECIO", documento_origen_id=fc,
                      aplicaciones=[{"documento_id": fc, "importe": "290724.57"}])
        self.assertEqual(self.detalle(fc)["saldo"], 60175.43)
        self.assertEqual(self.detalle(nc)["estado_saldo"], "APLICADA")
        nc_ars = self.doc(EXAR, 5, 186, "2026-05-15", "ARS", "100", tipo="NC")
        self.err(self.carga.post(f"{API}/documentos/{nc_ars}/aplicaciones", json={"aplicaciones": [{"documento_id": fc, "importe": 10}]}), 400, "MONEDA")
        # Cobro parcial en pesos al TC de la fecha de pago, con retención.
        rec = self.recibo(EXAR, 472, "2026-10-16", "ARS", [{"medio": "TRANSFERENCIA", "importe": "41500000"}, {"medio": "RETENCION_IIBB", "importe": "500000"}],
                          [{"documento_id": fc, "importe_recibo": "42000000", "tipo_cambio": "1383"}])
        d = self.detalle(fc)
        cobro = [x for x in d["movimientos"] if x["tipo"] == "COBRO"][0]
        self.assertEqual((cobro["importe_recibido"], cobro["moneda_recibida"], cobro["tipo_cambio"], cobro["importe"], cobro["saldo_restante"]),
                         (42000000.0, "ARS", 1383.0, 30368.76, 29806.67))
        self.assertEqual((d["saldo"], d["estado_saldo"]), (29806.67, "VENCIDA"))
        # Sin TC no se imputa entre monedas; un excedente se rechaza.
        self.err(self.recibo(EXAR, 473, "2026-10-17", "ARS", [{"medio": "TRANSFERENCIA", "importe": "1000"}],
                             [{"documento_id": fc, "importe_recibo": "1000"}], esperado=400), 400, "TC_REQUERIDO")
        self.err(self.recibo(EXAR, 473, "2026-10-17", "ARS", [{"medio": "TRANSFERENCIA", "importe": "99000000"}],
                             [{"documento_id": fc, "importe_recibo": "99000000", "tipo_cambio": "1383"}], esperado=400), 400, "EXCEDE_SALDO")
        # Segundo cobro parcial que deja parte a cuenta; luego se asigna lo que quedó a cuenta y cancela exacto.
        rec2 = self.recibo(EXAR, 474, "2026-10-18", "ARS", [{"medio": "TRANSFERENCIA", "importe": "41222620"}],
                           [{"documento_id": fc, "importe_recibo": "20000000", "tipo_cambio": "1383"}])
        recibos = {r["id"]: r for r in self.ok(self.carga.get(API + "/recibos", params={"cliente": EXAR}))}
        self.assertEqual(recibos[rec]["a_cuenta"], 0)
        self.assertEqual(recibos[rec2]["a_cuenta"], 21222620.0)
        # En ARS solo quedan la NC en pesos (121) y lo que el recibo 474 dejó a cuenta; lo aplicado a la FC en USD no figura acá.
        self.assertAlmostEqual(self.ok(self.carga.get(API + "/cuenta-corriente", params={"cliente": EXAR, "moneda": "ARS"}))["saldo"], -21222741.0, places=2)
        self.ok(self.carga.post(f"{API}/recibos/{rec2}/imputaciones", json={"imputaciones": [{"documento_id": fc, "importe_recibo": "21222620", "tipo_cambio": "1383"}]}))
        d = self.detalle(fc)
        self.assertEqual((d["saldo"], d["estado_saldo"]), (0, "CANCELADA"))
        # Saldos siempre por moneda; la cuenta corriente cuadra con el saldo calculado.
        for mon in ("USD", "ARS"):
            cc = self.ok(self.carga.get(API + "/cuenta-corriente", params={"cliente": EXAR, "moneda": mon}))
            self.assertAlmostEqual(cc["saldo"], cc["saldo_calculado"], places=2)
        cc_usd = self.ok(self.carga.get(API + "/cuenta-corriente", params={"cliente": EXAR, "moneda": "USD"}))
        self.assertTrue(any("Cobro en ARS" in x["detalle"] for x in cc_usd["movimientos"]))
        t = self.ok(self.carga.get(API + "/tablero", params={"cliente": EXAR}))
        self.assertEqual(set(t["saldos_por_moneda"]), {"USD", "ARS"})

    def test_nd_diferencia_de_cambio_se_carga_sin_calculo(self):
        self.corte_confirmado()
        fc = self.doc(PIRQ, 6, 505, "2026-08-12", "USD", "110000", vto="2026-09-11", tipo_cambio_emision="1340")
        usd_antes = self.ok(self.carga.get(API + "/cuenta-corriente", params={"cliente": PIRQ, "moneda": "USD"}))["saldo"]
        ars_antes = self.ok(self.carga.get(API + "/cuenta-corriente", params={"cliente": PIRQ, "moneda": "ARS"}))["saldo"]
        self.doc(PIRQ, 5, 25, "2026-09-20", "ARS", "3300000", tipo="ND", motivo_codigo="DIFERENCIA_CAMBIO", documento_origen_id=fc, vto="2026-10-20")
        self.assertEqual(self.ok(self.carga.get(API + "/cuenta-corriente", params={"cliente": PIRQ, "moneda": "USD"}))["saldo"], usd_antes)
        self.assertAlmostEqual(self.ok(self.carga.get(API + "/cuenta-corriente", params={"cliente": PIRQ, "moneda": "ARS"}))["saldo"], ars_antes + 3993000, places=2)


class T60Anulaciones(Base):
    def test_anulaciones_en_cascada_conservan_historial(self):
        self.corte_confirmado()
        fc = self.doc(MODULAR, 6, 800, "2026-10-01", "ARS", "1000", vto="2026-10-31")
        nc = self.doc(MODULAR, 5, 800, "2026-10-02", "ARS", "100", tipo="NC", aplicaciones=[{"documento_id": fc, "importe": 121}])
        rec = self.recibo(MODULAR, 800, "2026-10-05", "ARS", [{"medio": "TRANSFERENCIA", "importe": "500"}], [{"documento_id": fc, "importe_recibo": "500"}])
        self.assertEqual(self.detalle(fc)["saldo"], 589.0)
        imp_id = [x for x in self.detalle(fc)["movimientos"] if x["tipo"] == "COBRO"][0]["id"]
        self.err(self.resp.post(f"{API}/imputaciones/{imp_id}/anular", json={"motivo": "corto"}), 400, "MOTIVO")
        self.ok(self.resp.post(f"{API}/imputaciones/{imp_id}/anular", json={"motivo": "Imputación cargada al comprobante equivocado"}))
        self.assertEqual(self.detalle(fc)["saldo"], 1089.0)
        recs = {r["id"]: r for r in self.ok(self.carga.get(API + "/recibos", params={"cliente": MODULAR}))}
        self.assertEqual(recs[rec]["a_cuenta"], 500.0)
        self.ok(self.carga.post(f"{API}/recibos/{rec}/imputaciones", json={"imputaciones": [{"documento_id": fc, "importe_recibo": "500"}]}))
        # Anular la FC: cascada sobre aplicación de NC y cobro; la NC vuelve a disponible y el recibo a cuenta.
        self.ok(self.resp.post(f"{API}/documentos/{fc}/anular", json={"motivo": "Error de carga: número de comprobante equivocado"}))
        d = self.detalle(fc)
        self.assertEqual((d["estado"], d["estado_saldo"], d["saldo"]), ("ANULADO", "ANULADO", 0))
        self.assertTrue(all(x["estado"] == "ANULADO" for x in d["movimientos"]))
        self.assertEqual(self.detalle(nc)["saldo"], 121.0)
        recs = {r["id"]: r for r in self.ok(self.carga.get(API + "/recibos", params={"cliente": MODULAR}))}
        self.assertEqual(recs[rec]["a_cuenta"], 500.0)
        listado = self.ok(self.carga.get(API + "/documentos", params={"cliente": MODULAR}))
        self.assertIn(fc, [x["id"] for x in listado], "el anulado sigue visible")
        # Se recarga el número correcto vinculado al anulado.
        nuevo = self.doc(MODULAR, 6, 800, "2026-10-01", "ARS", "1000", vto="2026-10-31", reemplaza_a_id=fc)
        self.assertEqual(self.detalle(fc)["reemplazado_por"], [nuevo])
        # Anular el recibo: cascada de sus imputaciones.
        self.ok(self.carga.post(f"{API}/recibos/{rec}/imputaciones", json={"imputaciones": [{"documento_id": nuevo, "importe_recibo": "500"}]}))
        self.ok(self.resp.post(f"{API}/recibos/{rec}/anular", json={"motivo": "Recibo cargado dos veces por error de tipeo"}))
        self.assertEqual(self.detalle(nuevo)["saldo"], 1210.0)
        # Desaplicar la NC (responsable).
        self.ok(self.carga.post(f"{API}/documentos/{nc}/aplicaciones", json={"aplicaciones": [{"documento_id": nuevo, "importe": 121}]}))
        apl = [x for x in self.detalle(nc)["movimientos"] if x["estado"] == "VIGENTE"][0]["id"]
        self.ok(self.resp.post(f"{API}/aplicaciones/{apl}/anular", json={"motivo": "Aplicación de NC al comprobante equivocado"}))
        self.assertEqual(self.detalle(nc)["saldo"], 121.0)


class T70Auditoria(Base):
    def test_eventos_inmutables_e_idempotencia(self):
        self.corte_confirmado()
        datos = {"tipo": "FC", "letra": "A", "punto_venta": 6, "numero": 900, "cliente_codigo": EXAR, "fecha_emision": "2026-10-01",
                 "fecha_vencimiento": "2026-10-31", "moneda": "USD", "lineas": [{"alicuota_iva": "21", "neto": "100"}]}
        r1 = self.ok(self.carga.post(API + "/documentos", json=datos, headers={"Idempotency-Key": "prueba-idem-900"}))
        r2 = self.ok(self.carga.post(API + "/documentos", json=datos, headers={"Idempotency-Key": "prueba-idem-900"}))
        self.assertEqual((r1["id"], r1["repetido"], r2["id"], r2["repetido"]), (r1["id"], False, r1["id"], True))
        self.err(self.carga.post(API + "/documentos", json=datos), 409, "DUPLICADO")
        with _db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM fac_documentos WHERE numero = 900").fetchone()[0], 1)
            ev = conn.execute("SELECT usuario, evento, detalle_json FROM fac_eventos WHERE entidad = 'DOCUMENTO' AND entidad_id = ?", (r1["id"],)).fetchone()
            self.assertEqual((ev["usuario"], ev["evento"]), (U_CARGA, "REGISTRADO"))
            for sql in ("UPDATE fac_eventos SET usuario = 'otro'", "DELETE FROM fac_eventos", "DELETE FROM fac_documentos",
                        f"UPDATE fac_documentos SET total = 1 WHERE id = {r1['id']}"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)
        aviso = self.ok(self.carga.post(API + "/documentos/validar", json={**datos, "numero": 901}))
        self.assertEqual(aviso["avisos"][0]["codigo"], "POSIBLE_DUPLICADO")
        eventos = self.ok(self.carga.get(API + "/eventos", params={"entidad": "DOCUMENTO", "entidad_id": r1["id"]}))
        self.assertEqual(eventos[0]["evento"], "REGISTRADO")


class T80Adjuntos(Base):
    def test_adjuntos_opcionales_y_configurables(self):
        self.corte_confirmado()
        sesion = self.ok(self.carga.get(API + "/sesion"))
        self.assertFalse(any(sesion["config"]["adjuntos_requeridos"].values()))
        self.assertEqual(sesion["config"]["adjuntos_almacenamiento"], "ENLACE")
        viejo = self.doc(EXAR, 6, 950, "2026-10-01", "USD", "100", vto="2026-10-31")
        try:
            req = {k: (k == "FC") for k in ("PEDIDO", "FC", "ND", "NC", "RECIBO", "CORTE")}
            self.ok(self.resp.put(API + "/config/adjuntos_requeridos", json={"valor": req}))
            self.err(self.doc(EXAR, 6, 951, "2026-10-01", "USD", "100", vto="2026-10-31", esperado=400), 400, "ADJUNTO_REQUERIDO")
            enlace = [{"tipo": "COMPROBANTE", "nombre": "FC 6-951.pdf", "url_externa": "https://drive.google.com/file/d/ejemplo"}]
            nuevo = self.doc(EXAR, 6, 951, "2026-10-01", "USD", "100", vto="2026-10-31", adjuntos=enlace)
            self.assertFalse(self.detalle(nuevo)["sin_adjunto"])
            # La exigencia no es retroactiva: al viejo se le puede cobrar y adjuntar después.
            self.recibo(EXAR, 950, "2026-10-20", "USD", [{"medio": "TRANSFERENCIA", "importe": "121"}], [{"documento_id": viejo, "importe_recibo": "121"}])
            self.ok(self.carga.post(API + "/adjuntos", json={"entidad": "DOCUMENTO", "entidad_id": viejo, "url_externa": "https://drive.google.com/x"}))
            self.err(self.carga.post(API + "/adjuntos", json={"entidad": "DOCUMENTO", "entidad_id": viejo, "ruta": "Doc_Facturacion/x.pdf"}), 400,
                     "ADJUNTO_ALMACENAMIENTO")
        finally:
            self.ok(self.resp.put(API + "/config/adjuntos_requeridos", json={"valor": {k: False for k in ("PEDIDO", "FC", "ND", "NC", "RECIBO", "CORTE")}}))


class T90PuestaEnMarcha(Base):
    def test_control_por_cliente_permisos_y_listado(self):
        self.corte_confirmado()
        otro = next(c["codigo"] for c in self.ok(self.carga.get(API + "/catalogos"))["clientes"] if c["codigo"] not in (EXAR, VIAP, MODULAR, PIRQ))
        # Sin carga inicial, el cliente opera sin validación; con carga inicial sin validar (aquí, un saldo según Tango), espera.
        self.assertTrue(self.doc(otro, 7, 1, "2026-10-01", "ARS", "100"))
        con_carga = next(c["codigo"] for c in self.ok(self.carga.get(API + "/catalogos"))["clientes"] if c["codigo"] not in (EXAR, VIAP, MODULAR, PIRQ, otro))
        corte = self.ok(self.carga.get(API + "/corte"))["corte"]  # confirmado: un saldo inicial omitido entra con motivo
        self.doc(con_carga, 6, 7000, "2025-11-15", "ARS", "100", origen="SALDO_INICIAL", corte_id=corte["id"], vto="2025-12-15",
                 motivo_alta_post_corte="Omitido en la carga inicial; figura en el informe de Tango")
        self.err(self.doc(con_carga, 7, 2, "2026-10-01", "ARS", "100", esperado=400), 400, "CLIENTE_SIN_VALIDAR")
        self.err(self.recibo(con_carga, 7001, "2026-10-01", "ARS", [{"medio": "TRANSFERENCIA", "importe": "10"}], [], esperado=400), 400,
                 "CLIENTE_SIN_VALIDAR")
        # Permisos: consulta no valida ni carga antecedentes; carga no valida ni reabre.
        self.assertEqual(self.cons.post(f"{API}/puesta-en-marcha/{otro}/validar", json={"situacion": "SIN_ANTECEDENTES"}).status_code, 403)
        self.assertEqual(self.cons.post(API + "/antecedentes", json={"certificado_id": 1, "neto_facturado": "1"}).status_code, 403)
        self.err(self.carga.post(f"{API}/puesta-en-marcha/{otro}/validar", json={"situacion": "SIN_ANTECEDENTES"}), 403, "PERMISO")
        self.err(self.cont.post(f"{API}/puesta-en-marcha/{otro}/validar", json={"situacion": "SIN_ANTECEDENTES"}), 403, "PERMISO")
        self.err(self.carga.post(f"{API}/puesta-en-marcha/{PIRQ}/reabrir", json={"motivo": "Corrección de la carga inicial"}), 403, "PERMISO")
        # Cliente validado y con operaciones: no se reabre ni admite antecedentes.
        self.err(self.resp.post(f"{API}/puesta-en-marcha/{PIRQ}/reabrir", json={"motivo": "Corrección de la carga inicial"}), 400, "CON_OPERACIONES")
        cert = self.ok(self.carga.get(f"{API}/puesta-en-marcha/{PIRQ}"))["certificados"][0]
        self.err(self.carga.post(API + "/antecedentes", json={"certificado_id": cert["id"], "neto_facturado": "1",
                                                              "nota": "Antecedente omitido en la carga inicial"}), 400, "CLIENTE_VALIDADO")
        # Habilitado sin antecedentes: ahora opera.
        self.validar(otro, "SIN_ANTECEDENTES")
        self.doc(otro, 7, 3, "2026-10-01", "ARS", "100")
        lista = self.ok(self.carga.get(API + "/puesta-en-marcha"))
        estados = {c["codigo"]: c["estado_inicial"] for c in lista["clientes"]}
        self.assertEqual(estados[VIAP]["situacion"], "CON_ANTECEDENTES")
        self.assertEqual({estados[c]["situacion"] for c in (EXAR, PIRQ, MODULAR, otro)}, {"SIN_ANTECEDENTES"})
        self.assertEqual(lista["resumen"]["validados"], 5)
        self.assertEqual(self.ok(self.carga.get(API + "/sesion"))["clientes_validados"], 5)
        det = self.ok(self.cons.get(f"{API}/puesta-en-marcha/{VIAP}"))
        self.assertEqual(det["estado_inicial"]["resumen"]["con_estimados"], ["ARS"])
        self.assertEqual([e["evento"] for e in det["eventos"]], ["VALIDADO"])


class T95ResponsablesDesdeAdministracion(Base):
    def test_responsables_se_asignan_en_administracion(self):
        """Los responsables del módulo se administran en Administración › Responsables por módulo (solo ADMINISTRADOR)."""
        _crear_usuario("admin.fac@test", ["administracion", "facturacion"], tipo="ADMINISTRADOR")
        nuevo_id = _crear_usuario("nuevo.resp.fac@test", ["facturacion"])
        admin, nuevo = _cliente("admin.fac@test"), _cliente("nuevo.resp.fac@test")
        try:
            self.assertFalse(self.ok(nuevo.get(API + "/sesion"))["es_responsable"])
            actuales = [r["usuario_id"] for r in self.ok(admin.get("/admin/responsables-modulo")) if r["modulo"] == "facturacion"]
            # Un usuario que no es ADMINISTRADOR no puede asignarlos (ni siquiera un responsable del módulo).
            self.assertEqual(self.resp.put("/admin/responsables-modulo/facturacion", json={"usuario_ids": actuales + [nuevo_id]}).status_code, 403)
            self.ok(admin.put("/admin/responsables-modulo/facturacion", json={"usuario_ids": actuales + [nuevo_id]}))
            self.assertTrue(self.ok(nuevo.get(API + "/sesion"))["es_responsable"])
            self.assertIn(nuevo_id, [r["usuario_id"] for r in self.ok(admin.get("/admin/responsables-modulo")) if r["modulo"] == "facturacion"])
            self.ok(admin.put("/admin/responsables-modulo/facturacion", json={"usuario_ids": actuales}))
            self.assertFalse(self.ok(nuevo.get(API + "/sesion"))["es_responsable"])
            self.assertTrue(self.ok(self.resp.get(API + "/sesion"))["es_responsable"], "los responsables previos se conservan")
        finally:
            admin.close()
            nuevo.close()


class T99Final(Base):
    def test_integridad_y_tablas_existentes_intactas(self):
        with _db() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        final = _huellas(DB_COPIA)
        for t, h in HUELLAS_ANTES.items():
            if t in TABLAS_DE_LAS_PRUEBAS:
                continue
            if not t.startswith("fac_"):
                self.assertEqual(final[t], h, f"la tabla existente {t} cambió durante las pruebas")
            elif t not in FAC_MODIFICADAS_POR_LAS_PRUEBAS:
                antes, despues = _filas_previas(DB_ANTES, DB_COPIA, t, h[2])
                self.assertEqual(despues, antes, f"cambiaron filas que la base de origen ya tenía en {t}")
        # El corte de origen (si lo había) sigue siendo el mismo: las pruebas solo le fijan la fecha de PRUEBA y lo confirman.
        antes, despues = _filas_previas(DB_ANTES, DB_COPIA, "fac_cortes", ["id", "creado_por", "creado_en"]) if "fac_cortes" in HUELLAS_ANTES else ([], [])
        self.assertEqual(despues, antes)
        self.assertEqual(hashlib.sha256(open(DB_ORIGEN, "rb").read()).hexdigest(), SHA_ORIGEN_ANTES, "la base de origen cambió")


def load_tests(loader, tests, pattern):
    """Orden fijo: las clases comparten la base y el corte de prueba."""
    suite = unittest.TestSuite()
    for clase in (T01Migracion, T20CorteYSaldosIniciales, T10Permisos, T30PedidosYPdf, T40Relaciones, T50MonedasNcYCobros,
                  T60Anulaciones, T70Auditoria, T80Adjuntos, T90PuestaEnMarcha, T95ResponsablesDesdeAdministracion, T99Final):
        suite.addTests(loader.loadTestsFromTestCase(clase))
    return suite


if __name__ == "__main__":
    unittest.main(verbosity=2)
