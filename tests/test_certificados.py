"""Pruebas de certificados de cliente (Etapa 2: migración, backend, cálculos y permisos).

Se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal: DATA_DIR se
fija antes de importar main, porque importar main ejecuta init_sqlite().
Ejecutar este módulo en un proceso propio:

    .venv\\Scripts\\python.exe -m unittest tests.test_certificados -v

La base de origen se elige con SMG_TEST_DB_ORIGEN (por defecto, dashboard.db en modo solo
lectura). Así se prueba la migración desde un respaldo previo y desde el estado actual.
"""
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from decimal import Decimal

if "main" in sys.modules:
    raise unittest.SkipTest("main ya fue importado con otra DATA_DIR; ejecute este módulo por separado.")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_REAL = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_certificados_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")


def _copiar_base():
    origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
    destino = sqlite3.connect(DB_COPIA)
    origen.backup(destino)
    destino.close()
    origen.close()


# init_sqlite() ya reimportaba Clientes.xlsx en cada arranque y renovaba clientes.updated_at
# (comportamiento previo, ajeno a certificados): esa columna no se compara.
COLUMNAS_IGNORADAS = {"clientes": {"updated_at"}}


def _huellas(ruta):
    """Cantidad de filas y hash del contenido de cada tabla."""
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        tablas = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        resultado = {}
        for tabla in tablas:
            columnas = [r[1] for r in conn.execute(f'PRAGMA table_info("{tabla}")')
                        if r[1] not in COLUMNAS_IGNORADAS.get(tabla, set())]
            filas = conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in columnas)} FROM "{tabla}" ORDER BY rowid').fetchall()
            resultado[tabla] = (len(filas), hashlib.sha256(repr(filas).encode()).hexdigest(), columnas)
        return resultado
    finally:
        conn.close()


def _historicos(ruta):
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        return {
            "items_9_10": conn.execute("SELECT * FROM items_oc_cliente WHERE id IN (9, 10) ORDER BY id").fetchall(),
            "seg_0002": conn.execute(
                "SELECT id, centro_costo_id, fecha_alta_servicio, cotizacion, fecha_inicio, fecha_final, estado_seguimiento, "
                "cliente_codigo, observaciones FROM seguimiento_cc WHERE id = 2"
            ).fetchall(),
            "oc_6": conn.execute("SELECT * FROM Orden_de_compra_cliente WHERE id = 6").fetchall(),
        }
    finally:
        conn.close()


_copiar_base()
HUELLAS_ANTES = _huellas(DB_COPIA)
HISTORICOS_ANTES = _historicos(DB_COPIA)
os.environ["DATA_DIR"] = TMP_DIR
os.environ["APP_LOGIN_USER"] = "contingencia_test"
os.environ["APP_LOGIN_PASSWORD"] = "clave-contingencia-test"

import main  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB_COPIA), "Las pruebas no apuntan a la copia"
HUELLAS_DESPUES_INIT = _huellas(DB_COPIA)


def _puerto_libre():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    puerto = sock.getsockname()[1]
    sock.close()
    return puerto


PUERTO = _puerto_libre()
BASE_URL = f"http://127.0.0.1:{PUERTO}"
_SERVIDOR = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PUERTO, log_level="warning", lifespan="off"))
threading.Thread(target=_SERVIDOR.run, daemon=True).start()
for _ in range(200):
    if _SERVIDOR.started:
        break
    time.sleep(0.05)

CLAVE = "Clave-Prueba-1"
SEG_RUTA = "/operaciones/seguimiento-cliente/{sid}"
CERT_RUTA = SEG_RUTA + "/certificados"
ACCIONES_BASE = ["consultar", "certificar", "emitir_certificado", "anular_certificado", "cambiar_modalidad"]


def _db():
    conn = sqlite3.connect(DB_COPIA, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _crear_usuario(correo, modulos, paneles=None, acciones=None):
    with _db() as conn:
        conn.execute("DELETE FROM usuarios WHERE correo = ?", (correo,))
        conn.execute(
            """
            INSERT INTO usuarios (nombre_apellido, correo, password_hash, estado, tipo_usuario, modulos_json,
                                  paneles_json, acciones_json, bloqueado, intentos_fallidos)
            VALUES (?, ?, ?, 'ACTIVO', 'OPERADOR', ?, ?, ?, 0, 0)
            """,
            (correo, correo, hashlib.sha256(CLAVE.encode()).hexdigest(), json.dumps(modulos),
             json.dumps(paneles or {}), json.dumps(acciones or {})),
        )


def _permisos(acciones):
    return {"operaciones": ["seguimiento_cliente"]}, {"operaciones": {"seguimiento_cliente": acciones}}


def _cliente(usuario, clave=CLAVE):
    cliente = httpx.Client(base_url=BASE_URL, follow_redirects=False, trust_env=False, timeout=60)
    respuesta = cliente.post("/login", data={"username": usuario, "password": clave})
    assert respuesta.headers.get("location") == "/", f"Login fallido para {usuario}"
    return cliente


def _total(cantidad, precio):
    return float((Decimal(str(cantidad)) * Decimal(str(precio))).quantize(Decimal("0.01")))


def _crear_seg(ordenes, centro_costo_id=1):
    """ordenes: lista de dicts {moneda, monto (opcional), items: [(descripcion, unidad, cantidad, precio, moneda?)]}."""
    with _db() as conn:
        sid = conn.execute(
            "INSERT INTO seguimiento_cc (centro_costo_id, estado_seguimiento, cotizacion, fecha_ultima_actualizacion) "
            "VALUES (?, 'En curso', 'COT-TEST', CURRENT_TIMESTAMP)",
            (centro_costo_id,),
        ).lastrowid
        ocs, items = [], {}
        for n, orden in enumerate(ordenes, start=1):
            filas = orden.get("items", [])
            monto = orden.get("monto")
            if monto is None:
                monto = float(sum(Decimal(str(_total(f[2], f[3]))) for f in filas))
            oid = conn.execute(
                "INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, revision, moneda, monto_oc) "
                "VALUES (?, ?, ?, 'R0', ?, ?)",
                (sid, n, f"T{sid}-{n}", orden["moneda"], monto),
            ).lastrowid
            ocs.append(oid)
            items[oid] = []
            for m, fila in enumerate(filas, start=1):
                moneda_item = fila[4] if len(fila) > 4 else orden["moneda"]
                items[oid].append(conn.execute(
                    "INSERT INTO items_oc_cliente (orden_compra_cliente_id, numero, descripcion, unidad, cantidad, moneda, precio, total) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (oid, m, fila[0], fila[1], fila[2], moneda_item, fila[3], _total(fila[2], fila[3])),
                ).lastrowid)
    return sid, ocs, items


def _payload(**datos):
    base = {"fecha_certificado": "2026-10-15", "periodo_desde": "2026-10-01", "periodo_hasta": "2026-10-15",
            "moneda": "USD", "tipo": "PARCIAL"}
    base.update(datos)
    return base


def _borrador(cliente, sid, **datos):
    return cliente.post(CERT_RUTA.format(sid=sid), json=_payload(**datos))


def _emitir(cliente, sid, cid, justificacion=None):
    cuerpo = {"justificacion_exceso": justificacion} if justificacion is not None else {}
    return cliente.post(CERT_RUTA.format(sid=sid) + f"/{cid}/emitir", json=cuerpo)


def _anular(cliente, sid, cid, motivo="Anulación de prueba por error de carga"):
    return cliente.post(CERT_RUTA.format(sid=sid) + f"/{cid}/anular", json={"motivo": motivo})


def _resumen(cliente, sid):
    respuesta = cliente.get(CERT_RUTA.format(sid=sid))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _oc_resumen(resumen, oid):
    return next(o for o in resumen["resumen"]["ordenes"] if o["orden_compra_cliente_id"] == oid)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paneles, acciones = _permisos(ACCIONES_BASE)
        _crear_usuario("cert.operador@test.local", ["operaciones"], paneles, acciones)
        paneles, acciones = _permisos(ACCIONES_BASE + ["autorizar_exceso"])
        _crear_usuario("cert.exceso@test.local", ["operaciones"], paneles, acciones)
        paneles, acciones = _permisos(["consultar"])
        _crear_usuario("cert.consulta@test.local", ["operaciones"], paneles, acciones)
        _crear_usuario("cert.sinpanel@test.local", ["operaciones"])
        cls.oper = _cliente("cert.operador@test.local")
        cls.exceso = _cliente("cert.exceso@test.local")
        cls.consulta = _cliente("cert.consulta@test.local")
        cls.sinpanel = _cliente("cert.sinpanel@test.local")
        cls.contingencia = _cliente("contingencia_test", "clave-contingencia-test")
        cls.anonimo = httpx.Client(base_url=BASE_URL, follow_redirects=False, trust_env=False)

    def ok(self, respuesta, codigo=200):
        self.assertEqual(respuesta.status_code, codigo, respuesta.text)
        return respuesta.json()

    def global_simple(self, monto=10000.0, moneda="USD"):
        return _crear_seg([{"moneda": moneda, "monto": monto}])

    def emitir_global(self, sid, oid, importe, tipo="PARCIAL", cliente=None, **extra):
        cliente = cliente or self.oper
        clave = "importe_periodo" if tipo == "PARCIAL" else "importe_final"
        datos = self.ok(_borrador(cliente, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=oid, tipo=tipo,
                                  lineas=[{"orden_compra_cliente_id": oid, clave: importe}], **extra))
        return self.ok(_emitir(cliente, sid, datos["certificado_id"]))["certificado"]


# ---------------------------------------------------------------------------
# A. Migración
# ---------------------------------------------------------------------------
class A_Migracion(Base):
    def test_a1_estructuras_nuevas_y_datos_previos_intactos(self):
        nuevas = {"certificados_cliente", "certificados_cliente_lineas", "certificados_cliente_descuentos",
                  "certificados_cliente_eventos", "certificados_cambios_modalidad"}
        self.assertTrue(nuevas.issubset(HUELLAS_DESPUES_INIT), "Faltan tablas de certificados")
        cambiadas = []
        for tabla, (cantidad, huella, columnas) in HUELLAS_ANTES.items():
            despues = HUELLAS_DESPUES_INIT[tabla]
            if tabla in ("centros_costos", "seguimiento_cc"):
                # Solo se agrega una columna: las columnas previas deben conservar exactamente sus valores.
                conn = sqlite3.connect(f"file:{DB_COPIA}?mode=ro", uri=True)
                try:
                    filas = conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in columnas)} FROM "{tabla}" ORDER BY rowid').fetchall()
                finally:
                    conn.close()
                if (len(filas), hashlib.sha256(repr(filas).encode()).hexdigest()) != (cantidad, huella):
                    cambiadas.append(tabla)
            elif (despues[0], despues[1]) != (cantidad, huella):
                cambiadas.append(tabla)
        self.assertEqual(cambiadas, [], f"init_sqlite modificó datos existentes en: {cambiadas}")

    def test_a2_columnas_nuevas_en_null(self):
        with _db() as conn:
            self.assertIn("provincia", [r[1] for r in conn.execute("PRAGMA table_info(centros_costos)")])
            self.assertIn("modalidad_certificacion", [r[1] for r in conn.execute("PRAGMA table_info(seguimiento_cc)")])
            ids = [r[0] for r in conn.execute("SELECT id FROM seguimiento_cc WHERE id IN (1, 2)")]
            for sid in ids:
                modalidad = conn.execute("SELECT modalidad_certificacion FROM seguimiento_cc WHERE id = ?", (sid,)).fetchone()[0]
                certificados = {r[0] for r in conn.execute("SELECT modalidad FROM certificados_cliente WHERE seguimiento_cc_id = ?", (sid,))}
                if certificados:
                    # Guardar un borrador registra la modalidad del seguimiento: debe coincidir con la de todos sus certificados.
                    self.assertEqual(certificados, {modalidad}, f"seguimiento {sid}")
                else:
                    # Sin certificados, la columna nueva sigue en NULL (la migración no asigna modalidad).
                    self.assertIsNone(modalidad, f"seguimiento {sid}")
        self.assertEqual(HUELLAS_DESPUES_INIT["centros_costos"][0], HUELLAS_ANTES["centros_costos"][0])

    def test_a3_integridad_y_claves_foraneas(self):
        with _db() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            tablas_cert = {"certificados_cliente", "certificados_cliente_lineas", "certificados_cliente_descuentos",
                           "certificados_cambios_modalidad", "sesiones"}
            self.assertEqual([tuple(f) for f in fk if f[0] in tablas_cert], [])
            triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_cert_%'")}
            self.assertEqual(len(triggers), 19, sorted(triggers))
            # La migración recrea los triggers: una base migrada antes debe quedar con la definición vigente.
            definicion = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'trg_cert_item_certificado'").fetchone()[0]
            self.assertIn("l.importe_periodo > 0", definicion)

    def test_a4_migracion_idempotente(self):
        antes = _huellas(DB_COPIA)
        with _db() as conn:
            for _ in range(2):
                main.migrar_centros_costos(conn)
                main.migrar_seguimiento_cc(conn)
                main.migrar_certificados_cliente(conn)
        self.assertEqual(_huellas(DB_COPIA), antes)

    def test_a5_schema_crea_base_vacia(self):
        ruta = os.path.join(TMP_DIR, "vacia.db")
        conn = sqlite3.connect(ruta)
        try:
            with open(main.SCHEMA_PATH, encoding="utf-8") as archivo:
                conn.executescript(archivo.read())
            main.migrar_centros_costos(conn)
            main.migrar_seguimiento_cc(conn)
            main.migrar_certificados_cliente(conn)
            conn.commit()
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertTrue({"certificados_cliente", "sesiones", "centros_costos", "seguimiento_cc"}.issubset(tablas))
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# B. Permisos
# ---------------------------------------------------------------------------
class B_Permisos(Base):
    def test_b1_sin_sesion_401(self):
        sid, ocs, _ = self.global_simple()
        self.assertEqual(self.anonimo.get(CERT_RUTA.format(sid=sid)).status_code, 401)
        self.assertEqual(self.anonimo.post(CERT_RUTA.format(sid=sid), json=_payload()).status_code, 401)
        self.assertEqual(self.anonimo.post(CERT_RUTA.format(sid=sid) + "/1/emitir", json={}).status_code, 401)

    def test_b2_sin_panel_o_sin_accion_403(self):
        sid, ocs, _ = self.global_simple()
        self.assertEqual(self.sinpanel.get(CERT_RUTA.format(sid=sid)).status_code, 403)
        self.assertEqual(self.consulta.get(CERT_RUTA.format(sid=sid)).status_code, 200)
        respuesta = _borrador(self.consulta, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                              lineas=[{"importe_periodo": 100}])
        self.assertEqual(respuesta.status_code, 403)

    def test_b3_endpoints_seg_existentes_conservan_su_permiso(self):
        sid, ocs, _ = self.global_simple()
        self.assertEqual(self.sinpanel.get("/operaciones/seguimientos-cliente").status_code, 200)
        self.assertEqual(self.sinpanel.get(SEG_RUTA.format(sid=sid)).status_code, 200)

    def test_b4_contingencia_no_ejecuta_acciones_nominales(self):
        sid, ocs, _ = self.global_simple()
        borrador = self.ok(_borrador(self.contingencia, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                     lineas=[{"importe_periodo": 100}]))
        cid = borrador["certificado_id"]
        self.assertEqual(_emitir(self.contingencia, sid, cid).status_code, 403)
        emitido = self.ok(_emitir(self.oper, sid, cid))
        self.assertEqual(emitido["certificado"]["estado"], "EMITIDO")
        self.assertEqual(_anular(self.contingencia, sid, cid).status_code, 403)
        respuesta = self.contingencia.post(SEG_RUTA.format(sid=sid) + "/modalidad",
                                           json={"modalidad": "ITEMS", "justificacion": "Cambio de prueba de contingencia"})
        self.assertEqual(respuesta.status_code, 403)

    def test_b5_usuario_sin_emitir_no_emite(self):
        sid, ocs, _ = self.global_simple()
        cid = self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                lineas=[{"importe_periodo": 100}]))["certificado_id"]
        paneles, acciones = _permisos(["consultar", "certificar"])
        _crear_usuario("cert.sinemitir@test.local", ["operaciones"], paneles, acciones)
        self.assertEqual(_emitir(_cliente("cert.sinemitir@test.local"), sid, cid).status_code, 403)


# ---------------------------------------------------------------------------
# C. Cálculos
# ---------------------------------------------------------------------------
class C_Calculos(Base):
    def test_c1_seg0001_items_parcial_y_porcentaje(self):
        # SEG-0001 real (copia): OC 5, ítem 8 (1 Gl x 504147.62) y ítem 7 (2 Gl x 4286.85). Solo se calcula.
        calculo = self.ok(self.oper.post(CERT_RUTA.format(sid=1) + "/calcular", json=_payload(
            modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=5,
            lineas=[{"item_oc_cliente_id": 8, "cantidad_periodo": 0.35}, {"item_oc_cliente_id": 7, "porcentaje_periodo": 50}])))
        lineas = {l["item_oc_cliente_id"]: l for l in calculo["lineas"]}
        self.assertEqual(lineas[8]["importe_periodo"], 176451.67)
        self.assertEqual(lineas[7]["cantidad_periodo"], 1.0)
        self.assertEqual(lineas[7]["importe_periodo"], 4286.85)
        self.assertEqual(set(lineas), {7, 8, 9, 10}, "Las líneas cubren todos los ítems de la OC")
        self.assertEqual(calculo["cabecera"]["importe_periodo"], 180738.52)
        self.assertEqual(calculo["cabecera"]["base_contractual"], 532721.32)

    def test_c2_ultimo_tramo_absorbe_redondeo(self):
        sid, ocs, items = _crear_seg([{"moneda": "USD", "items": [("Tramos", "Un", 3, 33.333)]}])
        iid = items[ocs[0]][0]
        importes = []
        for cantidad, tipo in ((1, "PARCIAL"), (1, "PARCIAL"), (3, "FINAL")):
            linea = {"item_oc_cliente_id": iid, "cantidad_periodo" if tipo == "PARCIAL" else "cantidad_final": cantidad}
            cid = self.ok(_borrador(self.oper, sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=ocs[0],
                                    tipo=tipo, lineas=[linea]))["certificado_id"]
            importes.append(self.ok(_emitir(self.oper, sid, cid))["certificado"]["importe_periodo"])
        self.assertEqual(importes, [33.33, 33.34, 33.33])
        self.assertEqual(round(sum(importes), 2), 100.0)
        oc = _oc_resumen(_resumen(self.oper, sid), ocs[0])
        self.assertEqual((oc["certificado"], oc["saldo"], oc["cerrada"]), (100.0, 0.0, True))

    def test_c3_final_bajo_base_cierra_oc_y_registra_saldo_no_ejecutado(self):
        sid, ocs, _ = self.global_simple(10000)
        self.emitir_global(sid, ocs[0], 4000)
        datos = self.ok(_borrador(self.oper, sid, alcance="OC", orden_compra_cliente_id=ocs[0], tipo="FINAL",
                                  lineas=[{"importe_final": 9000}]))
        self.assertTrue(any("saldo no ejecutado de 1000" in a for a in datos["calculo"]["advertencias"]))
        final = self.ok(_emitir(self.oper, sid, datos["certificado_id"]))["certificado"]
        self.assertEqual((final["importe_periodo"], final["saldo_resultante"]), (5000.0, 1000.0))
        respuesta = _borrador(self.oper, sid, alcance="OC", orden_compra_cliente_id=ocs[0], lineas=[{"importe_periodo": 10}])
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("cerrada", respuesta.json()["detail"])

    def test_c4_final_menor_que_lo_certificado_400(self):
        sid, ocs, _ = self.global_simple(10000)
        self.emitir_global(sid, ocs[0], 4000)
        respuesta = _borrador(self.oper, sid, alcance="OC", orden_compra_cliente_id=ocs[0], tipo="FINAL",
                              lineas=[{"importe_final": 3000}])
        self.assertEqual(respuesta.status_code, 400)

    def test_c5_global_oc_por_porcentaje(self):
        sid, ocs, _ = self.global_simple(12345.67)
        calculo = self.ok(self.oper.post(CERT_RUTA.format(sid=sid) + "/calcular", json=_payload(
            modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0], lineas=[{"porcentaje_periodo": 10}])))
        self.assertEqual(calculo["cabecera"]["importe_periodo"], 1234.57)

    def test_c6_global_seg_reparto_proporcional_exacto(self):
        sid, ocs, _ = _crear_seg([{"moneda": "USD", "monto": 10000}, {"moneda": "USD", "monto": 5000},
                                  {"moneda": "ARS", "monto": 999999}])
        self.emitir_global(sid, ocs[0], 5000)  # saldos: 5000 y 5000
        calculo = self.ok(self.oper.post(CERT_RUTA.format(sid=sid) + "/calcular", json=_payload(
            alcance="SEG", importe_total_periodo=3000.01)))
        reparto = {l["orden_compra_cliente_id"]: l["importe_periodo"] for l in calculo["lineas"]}
        self.assertEqual(set(reparto), {ocs[0], ocs[1]}, "La OC en ARS no entra en un certificado USD")
        self.assertEqual(round(sum(reparto.values()), 2), 3000.01)
        self.assertEqual(sorted(reparto.values()), [1500.0, 1500.01])
        respuesta = self.oper.post(CERT_RUTA.format(sid=sid) + "/calcular", json=_payload(alcance="SEG", importe_total_periodo=10000.01))
        self.assertEqual(respuesta.status_code, 400)

    def test_c7_oc_y_seg_no_se_cuentan_dos_veces(self):
        sid, ocs, _ = _crear_seg([{"moneda": "USD", "monto": 10000}, {"moneda": "USD", "monto": 5000}])
        self.emitir_global(sid, ocs[0], 4000)
        cid = self.ok(_borrador(self.oper, sid, alcance="SEG", lineas=[
            {"orden_compra_cliente_id": ocs[0], "importe_periodo": 6000},
            {"orden_compra_cliente_id": ocs[1], "importe_periodo": 3000}]))["certificado_id"]
        self.ok(_emitir(self.oper, sid, cid))
        resumen = _resumen(self.oper, sid)
        self.assertEqual(_oc_resumen(resumen, ocs[0])["certificado"], 10000.0)
        self.assertEqual(_oc_resumen(resumen, ocs[1])["certificado"], 3000.0)
        usd = next(g for g in resumen["resumen"]["por_moneda"] if g["moneda"] == "USD")
        self.assertEqual((usd["base"], usd["certificado"], usd["saldo"]), (15000.0, 13000.0, 2000.0))
        # Un nuevo certificado por OC parte del acumulado que dejó el certificado por SEG.
        calculo = self.ok(self.oper.post(CERT_RUTA.format(sid=sid) + "/calcular", json=_payload(
            alcance="OC", orden_compra_cliente_id=ocs[1], lineas=[{"importe_periodo": 2000}])))
        self.assertEqual(calculo["cabecera"]["acumulado_anterior"], 3000.0)
        self.assertEqual(calculo["cabecera"]["exceso_importe"], 0.0)

    def test_c8_no_se_mezclan_monedas(self):
        sid, ocs, _ = self.global_simple(1000, "USD")
        respuesta = _borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0], moneda="ARS",
                              lineas=[{"importe_periodo": 10}])
        self.assertEqual(respuesta.status_code, 400)
        sid, ocs, _ = _crear_seg([{"moneda": "USD", "monto": 300, "items": [("A", "Un", 1, 100), ("B", "Un", 2, 100, "EUR")]}])
        respuesta = _borrador(self.oper, sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=ocs[0], lineas=[])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("otra moneda", respuesta.json()["detail"])

    def test_c9_borradores_y_anulados_no_computan(self):
        sid, ocs, _ = self.global_simple(10000)
        self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0], lineas=[{"importe_periodo": 5000}]))
        self.assertEqual(_oc_resumen(_resumen(self.oper, sid), ocs[0])["certificado"], 0.0)
        emitido = self.emitir_global(sid, ocs[0], 2000)
        self.assertEqual(_oc_resumen(_resumen(self.oper, sid), ocs[0])["certificado"], 2000.0)
        self.ok(_anular(self.oper, sid, emitido["id"]))
        self.assertEqual(_oc_resumen(_resumen(self.oper, sid), ocs[0])["certificado"], 0.0)

    def test_c10_items_exige_conciliacion_con_monto_oc(self):
        sid, ocs, _ = _crear_seg([{"moneda": "USD", "monto": 999, "items": [("A", "Un", 1, 100)]}])
        respuesta = _borrador(self.oper, sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=ocs[0],
                              lineas=[{"item_oc_cliente_id": 1, "cantidad_periodo": 1}])
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("no coincide", respuesta.json()["detail"])

    def test_c11_seg_sin_centro_de_costos_y_parcial_sin_avance(self):
        respuesta = _borrador(self.oper, 2, modalidad="GLOBAL", alcance="SEG", importe_total_periodo=10)
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("Centro de Costos", respuesta.json()["detail"])
        sid, ocs, _ = self.global_simple()
        respuesta = _borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0], lineas=[{"importe_periodo": 0}])
        self.assertEqual(respuesta.status_code, 400)


# ---------------------------------------------------------------------------
# D. Emisión, numeración, inmutabilidad y PDF
# ---------------------------------------------------------------------------
class D_Emision(Base):
    def test_d1_numeracion_correlativa_por_seg(self):
        sid, ocs, _ = self.global_simple(10000)
        numeros = [self.emitir_global(sid, ocs[0], 100)["numero"] for _ in range(3)]
        self.assertEqual(numeros, [1, 2, 3])
        otro, ocs_otro, _ = self.global_simple(10000)
        self.assertEqual(self.emitir_global(otro, ocs_otro[0], 100)["numero"], 1)
        self.assertEqual(self.emitir_global(sid, ocs[0], 100)["numero_texto"], f"SEG-{sid:04d}-CERT-004")

    def _emitir_en_paralelo(self, sid, ids):
        barrera = threading.Barrier(len(ids))
        resultados = {}

        def tarea(cid):
            cliente = _cliente("cert.operador@test.local")
            barrera.wait()
            resultados[cid] = _emitir(cliente, sid, cid)

        hilos = [threading.Thread(target=tarea, args=(cid,)) for cid in ids]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        return resultados

    def test_d2_emision_concurrente_sin_duplicados(self):
        sid, ocs, _ = self.global_simple(10000)
        ids = [self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                 lineas=[{"importe_periodo": 1000}]))["certificado_id"] for _ in range(4)]
        resultados = self._emitir_en_paralelo(sid, ids)
        self.assertTrue(all(r.status_code == 200 for r in resultados.values()), {k: r.text for k, r in resultados.items()})
        self.assertEqual(sorted(r.json()["certificado"]["numero"] for r in resultados.values()), [1, 2, 3, 4])

    def test_d3_emision_concurrente_que_excede_el_saldo(self):
        sid, ocs, _ = self.global_simple(10000)
        ids = [self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                 lineas=[{"importe_periodo": 6000}]))["certificado_id"] for _ in range(2)]
        resultados = self._emitir_en_paralelo(sid, ids)
        codigos = sorted(r.status_code for r in resultados.values())
        self.assertEqual(codigos, [200, 403], {k: r.text for k, r in resultados.items()})
        self.assertEqual(_oc_resumen(_resumen(self.oper, sid), ocs[0])["certificado"], 6000.0)

    def test_d4_triggers_impiden_modificar_emitidos(self):
        sid, ocs, _ = self.global_simple(10000)
        cert = self.emitir_global(sid, ocs[0], 1000, descuentos=[{"concepto": "MULTA", "importe": 10}])
        sentencias = [
            ("UPDATE certificados_cliente SET importe_periodo = 1 WHERE id = ?", (cert["id"],)),
            ("UPDATE certificados_cliente SET estado = 'BORRADOR', numero = NULL WHERE id = ?", (cert["id"],)),
            ("DELETE FROM certificados_cliente WHERE id = ?", (cert["id"],)),
            ("UPDATE certificados_cliente_lineas SET importe_periodo = 1 WHERE certificado_id = ?", (cert["id"],)),
            ("DELETE FROM certificados_cliente_lineas WHERE certificado_id = ?", (cert["id"],)),
            ("INSERT INTO certificados_cliente_lineas (certificado_id, orden_compra_cliente_id, base_linea, importe_anterior, "
             "importe_periodo, importe_acumulado) VALUES (?, ?, 0, 0, 0, 0)", (cert["id"], ocs[0])),
            ("UPDATE certificados_cliente_descuentos SET importe = 1 WHERE certificado_id = ?", (cert["id"],)),
            ("DELETE FROM certificados_cliente_descuentos WHERE certificado_id = ?", (cert["id"],)),
            ("UPDATE certificados_cliente_eventos SET usuario = 'x' WHERE certificado_id = ?", (cert["id"],)),
            ("DELETE FROM certificados_cliente_eventos WHERE certificado_id = ?", (cert["id"],)),
        ]
        for sql, parametros in sentencias:
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.IntegrityError):
                    with _db() as conn:
                        conn.execute(sql, parametros)
        self.ok(_anular(self.oper, sid, cert["id"]))
        with self.assertRaises(sqlite3.IntegrityError):
            with _db() as conn:
                conn.execute("UPDATE certificados_cliente SET motivo_anulacion = 'otro' WHERE id = ?", (cert["id"],))

    def test_d5_api_rechaza_editar_o_borrar_emitidos(self):
        sid, ocs, _ = self.global_simple(10000)
        cert = self.emitir_global(sid, ocs[0], 1000)
        ruta = CERT_RUTA.format(sid=sid) + f"/{cert['id']}"
        self.assertEqual(self.oper.put(ruta, json=_payload(alcance="OC", orden_compra_cliente_id=ocs[0], lineas=[{"importe_periodo": 1}])).status_code, 409)
        self.assertEqual(self.oper.delete(ruta).status_code, 409)
        self.assertEqual(_emitir(self.oper, sid, cert["id"]).status_code, 409)

    def test_d6_snapshot_pdf_y_hash(self):
        sid, ocs, _ = self.global_simple(10000)
        cert = self.emitir_global(sid, ocs[0], 2500)
        with _db() as conn:
            fila = conn.execute("SELECT * FROM certificados_cliente WHERE id = ?", (cert["id"],)).fetchone()
        snapshot = json.loads(fila["snapshot_json"])
        self.assertEqual(snapshot["certificado"]["importe_periodo"], 2500.0)
        self.assertEqual(len(snapshot["codigo_verificacion"]), 12)
        ruta = os.path.join(TMP_DIR, fila["pdf_ruta"])
        with open(ruta, "rb") as archivo:
            contenido = archivo.read()
        self.assertTrue(contenido.startswith(b"%PDF"))
        self.assertEqual(hashlib.sha256(contenido).hexdigest(), fila["pdf_sha256"])
        respuesta = self.oper.get(CERT_RUTA.format(sid=sid) + f"/{cert['id']}/pdf")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.content, contenido)

    def test_d7_no_sobrescribe_pdf_existente(self):
        sid, ocs, _ = self.global_simple(10000)
        cid = self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                lineas=[{"importe_periodo": 100}]))["certificado_id"]
        ruta = os.path.join(TMP_DIR, "Doc_Certificados", f"SEG-{sid:04d}", f"SEG-{sid:04d}-CERT-001.pdf")
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "wb") as archivo:
            archivo.write(b"archivo previo")
        self.assertEqual(_emitir(self.oper, sid, cid).status_code, 409)
        with open(ruta, "rb") as archivo:
            self.assertEqual(archivo.read(), b"archivo previo")
        with _db() as conn:
            fila = conn.execute("SELECT estado, numero FROM certificados_cliente WHERE id = ?", (cid,)).fetchone()
        self.assertEqual((fila["estado"], fila["numero"]), ("BORRADOR", None))

    def test_d8_pdf_alterado_no_se_entrega(self):
        sid, ocs, _ = self.global_simple(10000)
        cert = self.emitir_global(sid, ocs[0], 100)
        with _db() as conn:
            ruta = os.path.join(TMP_DIR, conn.execute("SELECT pdf_ruta FROM certificados_cliente WHERE id = ?", (cert["id"],)).fetchone()[0])
        with open(ruta, "ab") as archivo:
            archivo.write(b"alterado")
        self.assertEqual(self.oper.get(CERT_RUTA.format(sid=sid) + f"/{cert['id']}/pdf").status_code, 500)

    def test_d9_vista_previa_de_borrador(self):
        sid, ocs, _ = self.global_simple(10000)
        cid = self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                lineas=[{"importe_periodo": 100}]))["certificado_id"]
        respuesta = self.oper.get(CERT_RUTA.format(sid=sid) + f"/{cid}/pdf")
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.content.startswith(b"%PDF"))
        self.assertFalse(os.path.exists(os.path.join(TMP_DIR, "Doc_Certificados", f"SEG-{sid:04d}")))


# ---------------------------------------------------------------------------
# E. Excesos
# ---------------------------------------------------------------------------
class E_Excesos(Base):
    def test_e1_exceso_requiere_permiso_y_justificacion(self):
        sid, ocs, _ = self.global_simple(1000)
        datos = self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                  lineas=[{"importe_periodo": 1250}]))
        self.assertEqual(datos["calculo"]["cabecera"]["exceso_importe"], 250.0)
        self.assertTrue(datos["calculo"]["requiere_autorizacion_exceso"])
        cid = datos["certificado_id"]
        self.assertEqual(_emitir(self.oper, sid, cid, "Justificación suficiente de exceso").status_code, 403)
        self.assertEqual(_emitir(self.contingencia, sid, cid, "Justificación suficiente de exceso").status_code, 403)
        self.assertEqual(_emitir(self.exceso, sid, cid).status_code, 400)
        self.assertEqual(_emitir(self.exceso, sid, cid, "   corta   ").status_code, 400)
        cert = self.ok(_emitir(self.exceso, sid, cid, "Adicional aprobado por el cliente en obra"))["certificado"]
        self.assertEqual(cert["exceso_importe"], 250.0)
        self.assertEqual(cert["exceso_autorizado_por"], "cert.exceso@test.local")
        self.assertEqual(cert["exceso_justificacion"], "Adicional aprobado por el cliente en obra")
        self.assertTrue(cert["exceso_autorizado_en"])
        historial = self.ok(self.oper.get(CERT_RUTA.format(sid=sid) + f"/{cid}/historial"))["eventos"]
        evento = next(e for e in historial if e["evento"] == "EXCESO_AUTORIZADO")
        self.assertEqual((evento["usuario"], evento["detalle"]["importe_excedido"]), ("cert.exceso@test.local", 250.0))
        self.assertEqual(_oc_resumen(_resumen(self.oper, sid), ocs[0])["saldo"], -250.0)

    def test_e2_sin_exceso_no_pide_justificacion(self):
        sid, ocs, _ = self.global_simple(1000)
        cert = self.emitir_global(sid, ocs[0], 1000)
        self.assertEqual((cert["exceso_importe"], cert["exceso_justificacion"]), (0.0, None))

    def test_e3_exceso_por_cantidad_en_items(self):
        sid, ocs, items = _crear_seg([{"moneda": "USD", "items": [("Hs equipo", "Hs", 10, 100)]}])
        datos = self.ok(_borrador(self.oper, sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=ocs[0],
                                  lineas=[{"item_oc_cliente_id": items[ocs[0]][0], "cantidad_periodo": 12}]))
        self.assertEqual(datos["calculo"]["cabecera"]["exceso_importe"], 200.0)
        self.ok(_emitir(self.exceso, sid, datos["certificado_id"], "Horas adicionales aprobadas en parte diario"))


# ---------------------------------------------------------------------------
# F. Descuentos
# ---------------------------------------------------------------------------
class F_Descuentos(Base):
    def test_f1_descuentos_y_total_a_facturar(self):
        sid, ocs, _ = self.global_simple(10000)
        cert = self.emitir_global(sid, ocs[0], 3000, descuentos=[
            {"concepto": "RECUPERO_ANTICIPO", "importe": 500}, {"concepto": "OTRO", "descripcion": "Ajuste", "importe": 100.5}])
        self.assertEqual((cert["descuentos_netos"], cert["total_a_facturar"]), (600.5, 2399.5))
        self.assertEqual(_oc_resumen(_resumen(self.oper, sid), ocs[0])["saldo"], 7000.0, "Los descuentos no cambian el saldo")

    def test_f2_descuentos_invalidos(self):
        sid, ocs, _ = self.global_simple(10000)
        casos = [
            [{"concepto": "MULTA", "importe": 3000.01}],
            [{"concepto": "OTRO", "importe": 10}],
            [{"concepto": "IVA", "importe": 10}],
            [{"concepto": "MULTA", "importe": 0}],
        ]
        for descuentos in casos:
            with self.subTest(descuentos=descuentos):
                respuesta = _borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                      lineas=[{"importe_periodo": 3000}], descuentos=descuentos)
                self.assertEqual(respuesta.status_code, 400, respuesta.text)


# ---------------------------------------------------------------------------
# G. Anulación y reemplazo
# ---------------------------------------------------------------------------
class G_Anulacion(Base):
    def test_g1_solo_el_ultimo_de_cada_oc(self):
        sid, ocs, _ = self.global_simple(10000)
        primero = self.emitir_global(sid, ocs[0], 1000)
        segundo = self.emitir_global(sid, ocs[0], 1000)
        respuesta = _anular(self.oper, sid, primero["id"])
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn(segundo["numero_texto"], respuesta.json()["detail"])
        self.ok(_anular(self.oper, sid, segundo["id"]))
        self.ok(_anular(self.oper, sid, primero["id"]))
        self.assertEqual(_oc_resumen(_resumen(self.oper, sid), ocs[0])["saldo"], 10000.0)

    def test_g2_global_verifica_todas_las_oc(self):
        sid, ocs, _ = _crear_seg([{"moneda": "USD", "monto": 10000}, {"moneda": "USD", "monto": 5000}])
        cid = self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="SEG", importe_total_periodo=3000))["certificado_id"]
        global_seg = self.ok(_emitir(self.oper, sid, cid))["certificado"]
        posterior = self.emitir_global(sid, ocs[1], 100)
        self.assertEqual(_anular(self.oper, sid, global_seg["id"]).status_code, 409)
        self.ok(_anular(self.oper, sid, posterior["id"]))
        self.ok(_anular(self.oper, sid, global_seg["id"]))

    def test_g3_motivo_obligatorio_y_pdf_conservado(self):
        sid, ocs, _ = self.global_simple(10000)
        cert = self.emitir_global(sid, ocs[0], 1000)
        self.assertEqual(_anular(self.oper, sid, cert["id"], "corto").status_code, 400)
        with _db() as conn:
            fila = conn.execute("SELECT pdf_ruta, pdf_sha256 FROM certificados_cliente WHERE id = ?", (cert["id"],)).fetchone()
        anulado = self.ok(_anular(self.oper, sid, cert["id"]))["certificado"]
        self.assertEqual((anulado["estado"], anulado["motivo_anulacion"]), ("ANULADO", "Anulación de prueba por error de carga"))
        self.assertEqual((anulado["pdf_ruta"], anulado["pdf_sha256"]), (fila["pdf_ruta"], fila["pdf_sha256"]))
        original = self.oper.get(CERT_RUTA.format(sid=sid) + f"/{cert['id']}/pdf")
        self.assertEqual(hashlib.sha256(original.content).hexdigest(), fila["pdf_sha256"])
        sellada = self.oper.get(CERT_RUTA.format(sid=sid) + f"/{cert['id']}/pdf?copia=sellada")
        self.assertEqual(sellada.status_code, 200)
        self.assertNotEqual(sellada.content, original.content)
        self.assertEqual(_anular(self.oper, sid, cert["id"]).status_code, 409)
        eventos = [e["evento"] for e in self.ok(self.oper.get(CERT_RUTA.format(sid=sid) + f"/{cert['id']}/historial"))["eventos"]]
        self.assertEqual(eventos, ["BORRADOR_CREADO", "EMITIDO", "ANULADO"])

    def test_g4_reemplazo_solo_de_anulados(self):
        sid, ocs, _ = self.global_simple(10000)
        cert = self.emitir_global(sid, ocs[0], 1000)
        respuesta = _borrador(self.oper, sid, alcance="OC", orden_compra_cliente_id=ocs[0], reemplaza_a_id=cert["id"],
                              lineas=[{"importe_periodo": 900}])
        self.assertEqual(respuesta.status_code, 409)
        self.ok(_anular(self.oper, sid, cert["id"]))
        ids = [self.ok(_borrador(self.oper, sid, alcance="OC", orden_compra_cliente_id=ocs[0], reemplaza_a_id=cert["id"],
                                 lineas=[{"importe_periodo": 900}]))["certificado_id"] for _ in range(2)]
        reemplazo = self.ok(_emitir(self.oper, sid, ids[0]))["certificado"]
        self.assertEqual(reemplazo["reemplaza_a_id"], cert["id"])
        self.assertEqual(_emitir(self.oper, sid, ids[1]).status_code, 409)


# ---------------------------------------------------------------------------
# H. Protección de OC e ítems certificados (endpoints existentes)
# ---------------------------------------------------------------------------
class H_Proteccion(Base):
    def setUp(self):
        self.sid, self.ocs, items = _crear_seg([{"moneda": "USD", "items": [("A", "Un", 10, 100), ("B", "Un", 5, 20)]}])
        self.oid = self.ocs[0]
        self.item_a, self.item_b = items[self.oid]
        cid = self.ok(_borrador(self.oper, self.sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=self.oid,
                                lineas=[{"item_oc_cliente_id": self.item_a, "cantidad_periodo": 4}]))["certificado_id"]
        self.ok(_emitir(self.oper, self.sid, cid))
        self.ruta_item = SEG_RUTA.format(sid=self.sid) + f"/ordenes/{self.oid}/items/{self.item_a}"
        self.ruta_oc = SEG_RUTA.format(sid=self.sid) + f"/ordenes/{self.oid}"

    def test_h1_cambios_bloqueados(self):
        with _db() as conn:
            hermana = conn.execute(
                "INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, moneda, monto_oc) VALUES (?, 9, 'HERMANA', 'USD', 0)",
                (self.sid,),
            ).lastrowid
        casos = [
            (self.oper.delete, self.ruta_item, None),
            (self.oper.put, self.ruta_item, {"precio": 101}),
            (self.oper.put, self.ruta_item, {"cantidad": 3}),
            (self.oper.put, self.ruta_item, {"orden_compra_cliente_id": hermana}),
            (self.oper.put, self.ruta_oc, {"moneda": "ARS"}),
            (self.oper.put, self.ruta_oc, {"monto_oc": 399}),
        ]
        for metodo, ruta, cuerpo in casos:
            with self.subTest(ruta=ruta, cuerpo=cuerpo):
                respuesta = metodo(ruta) if cuerpo is None else metodo(ruta, json=cuerpo)
                self.assertEqual(respuesta.status_code, 409, respuesta.text)

    def test_h2_cambios_permitidos(self):
        self.ok(self.oper.put(self.ruta_item, json={"observacion": "nota"}))
        self.ok(self.oper.put(self.ruta_item, json={"cantidad": 12}))
        self.ok(self.oper.put(SEG_RUTA.format(sid=self.sid) + f"/ordenes/{self.oid}/items/{self.item_b}", json={"precio": 25}))

    def test_h3_triggers_protegen_por_sql(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with _db() as conn:
                conn.execute("UPDATE items_oc_cliente SET precio = 1 WHERE id = ?", (self.item_a,))
        with self.assertRaises(sqlite3.IntegrityError):
            with _db() as conn:
                conn.execute("DELETE FROM items_oc_cliente WHERE id = ?", (self.item_a,))
        with self.assertRaises(sqlite3.IntegrityError):
            with _db() as conn:
                conn.execute("UPDATE Orden_de_compra_cliente SET moneda = 'ARS' WHERE id = ?", (self.oid,))

    def test_h4_oc_global_certificada_no_se_borra_y_borrador_vacio_no_bloquea(self):
        sid, ocs, _ = self.global_simple(1000)
        self.emitir_global(sid, ocs[0], 100)
        self.assertEqual(self.oper.delete(SEG_RUTA.format(sid=sid) + f"/ordenes/{ocs[0]}").status_code, 409)
        sid, ocs, items = _crear_seg([{"moneda": "USD", "items": [("A", "Un", 1, 100), ("B", "Un", 1, 50)]}])
        self.ok(_borrador(self.oper, sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=ocs[0],
                          lineas=[{"item_oc_cliente_id": items[ocs[0]][0], "cantidad_periodo": 1}]))
        ruta_b = SEG_RUTA.format(sid=sid) + f"/ordenes/{ocs[0]}/items/{items[ocs[0]][1]}"
        ruta_a = SEG_RUTA.format(sid=sid) + f"/ordenes/{ocs[0]}/items/{items[ocs[0]][0]}"
        self.ok(self.oper.delete(ruta_b))
        self.assertEqual(self.oper.delete(ruta_a).status_code, 409)


# ---------------------------------------------------------------------------
# I. Modalidad
# ---------------------------------------------------------------------------
class I_Modalidad(Base):
    def test_i1_modalidad_se_fija_al_emitir(self):
        sid, ocs, _ = self.global_simple(1000)
        cid = self.ok(_borrador(self.oper, sid, modalidad="GLOBAL", alcance="OC", orden_compra_cliente_id=ocs[0],
                                lineas=[{"importe_periodo": 100}]))["certificado_id"]
        self.assertEqual(_borrador(self.oper, sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=ocs[0]).status_code, 409)
        self.ok(self.oper.delete(CERT_RUTA.format(sid=sid) + f"/{cid}"))
        with _db() as conn:
            self.assertIsNone(conn.execute("SELECT modalidad_certificacion FROM seguimiento_cc WHERE id = ?", (sid,)).fetchone()[0])
        self.emitir_global(sid, ocs[0], 100)
        respuesta = _borrador(self.oper, sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=ocs[0])
        self.assertEqual(respuesta.status_code, 409)
        with self.assertRaises(sqlite3.IntegrityError):
            with _db() as conn:
                conn.execute("UPDATE seguimiento_cc SET modalidad_certificacion = 'ITEMS' WHERE id = ?", (sid,))

    def test_i2_procedimiento_de_cambio(self):
        sid, ocs, items = _crear_seg([{"moneda": "USD", "items": [("A", "Un", 10, 100)]}])
        cid = self.ok(_borrador(self.oper, sid, modalidad="ITEMS", alcance="OC", orden_compra_cliente_id=ocs[0],
                                lineas=[{"item_oc_cliente_id": items[ocs[0]][0], "cantidad_periodo": 2}]))["certificado_id"]
        self.ok(_emitir(self.oper, sid, cid))
        ruta = SEG_RUTA.format(sid=sid) + "/modalidad"
        self.ok(_borrador(self.oper, sid, alcance="OC", orden_compra_cliente_id=ocs[0],
                          lineas=[{"item_oc_cliente_id": items[ocs[0]][0], "cantidad_periodo": 1}]))
        justificacion = "Cliente pasa a certificar por monto global"
        self.assertEqual(self.oper.post(ruta, json={"modalidad": "GLOBAL", "justificacion": justificacion}).status_code, 409)
        with _db() as conn:
            conn.execute("DELETE FROM certificados_cliente WHERE seguimiento_cc_id = ? AND estado = 'BORRADOR'", (sid,))
        self.assertEqual(self.oper.post(ruta, json={"modalidad": "GLOBAL", "justificacion": "corta"}).status_code, 400)
        self.assertEqual(self.exceso.post(ruta, json={"modalidad": "GLOBAL", "justificacion": justificacion}).status_code, 200)
        # Con certificados válidos, GLOBAL -> ITEMS no se permite.
        self.assertEqual(self.oper.post(ruta, json={"modalidad": "ITEMS", "justificacion": justificacion}).status_code, 409)
        # El acumulado por ítems sigue valiendo a nivel OC en GLOBAL.
        calculo = self.ok(self.oper.post(CERT_RUTA.format(sid=sid) + "/calcular", json=_payload(
            alcance="OC", orden_compra_cliente_id=ocs[0], lineas=[{"importe_periodo": 100}])))
        self.assertEqual((calculo["cabecera"]["modalidad"], calculo["cabecera"]["acumulado_anterior"]), ("GLOBAL", 200.0))
        with _db() as conn:
            cambio = conn.execute("SELECT * FROM certificados_cambios_modalidad WHERE seguimiento_cc_id = ?", (sid,)).fetchone()
        self.assertEqual((cambio["modalidad_anterior"], cambio["modalidad_nueva"], cambio["usuario"]),
                         ("ITEMS", "GLOBAL", "cert.exceso@test.local"))


# ---------------------------------------------------------------------------
# J. Persistencia (nuevo proceso sobre la misma copia)
# K. Datos históricos
# ---------------------------------------------------------------------------
class J_K_Persistencia_e_Historicos(Base):
    def test_j1_persistencia_tras_reinicio(self):
        sid, ocs, _ = self.global_simple(10000)
        cert = self.emitir_global(sid, ocs[0], 1234.56)
        antes = _resumen(self.oper, sid)
        pdf_antes = self.oper.get(CERT_RUTA.format(sid=sid) + f"/{cert['id']}/pdf").content
        puerto = _puerto_libre()
        entorno = {**os.environ, "DATA_DIR": TMP_DIR}
        proceso = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(puerto), "--log-level", "warning"],
            cwd=BASE_DIR, env=entorno, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            url = f"http://127.0.0.1:{puerto}"
            for _ in range(240):
                try:
                    httpx.get(url + "/login", trust_env=False, timeout=1)
                    break
                except httpx.HTTPError:
                    time.sleep(0.25)
            cliente = httpx.Client(base_url=url, follow_redirects=False, trust_env=False, timeout=60)
            cliente.post("/login", data={"username": "cert.operador@test.local", "password": CLAVE})
            despues = cliente.get(CERT_RUTA.format(sid=sid)).json()
            self.assertEqual(despues, antes)
            self.assertEqual(cliente.get(CERT_RUTA.format(sid=sid) + f"/{cert['id']}/pdf").content, pdf_antes)
        finally:
            proceso.terminate()
            proceso.wait(timeout=30)

    def test_k1_datos_historicos_sin_cambios(self):
        # Además se intenta certificar SEG-0002 (sin Centro de Costos) para comprobar que no se toca.
        _borrador(self.oper, 2, modalidad="GLOBAL", alcance="SEG", importe_total_periodo=10)
        self.assertEqual(_historicos(DB_COPIA), HISTORICOS_ANTES)


def tearDownModule():
    _SERVIDOR.should_exit = True
    time.sleep(0.5)
    shutil.rmtree(TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
