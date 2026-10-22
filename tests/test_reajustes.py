"""Pruebas de reajustes por polinómica, retroactivos y configuración del contrato (Etapa 3).

Igual que test_certificados: se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal
(DATA_DIR se fija antes de importar main). Ejecutar en un proceso propio:

    .venv\\Scripts\\python.exe -m unittest tests.test_reajustes -v

La base de origen se elige con SMG_TEST_DB_ORIGEN (por defecto, dashboard.db, abierta en solo lectura).
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
DB_REAL = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_reajustes_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")

_origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
_destino = sqlite3.connect(DB_COPIA)
_origen.backup(_destino)
_destino.close()
_origen.close()

COLUMNAS_IGNORADAS = {"clientes": {"updated_at"}}


def _huellas(ruta):
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        resultado = {}
        for (tabla,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            columnas = [r[1] for r in conn.execute(f'PRAGMA table_info("{tabla}")') if r[1] not in COLUMNAS_IGNORADAS.get(tabla, set())]
            filas = conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in columnas)} FROM "{tabla}" ORDER BY rowid').fetchall()
            resultado[tabla] = (columnas, filas)
        return resultado
    finally:
        conn.close()


HUELLAS_ANTES = _huellas(DB_COPIA)
os.environ["DATA_DIR"] = TMP_DIR
os.environ["APP_LOGIN_USER"] = "contingencia_test"
os.environ["APP_LOGIN_PASSWORD"] = "clave-contingencia-test"

import main  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402

assert os.path.abspath(main.SQLITE_DB_PATH) == os.path.abspath(DB_COPIA), "Las pruebas no apuntan a la copia"
HUELLAS_DESPUES = _huellas(DB_COPIA)


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
CERT = "/operaciones/seguimiento-cliente/{sid}/certificados"
RJ = "/operaciones/seguimiento-cliente/{sid}/reajustes"
BASICAS = ["consultar", "certificar", "emitir_certificado", "anular_certificado", "cambiar_modalidad"]
TODAS = BASICAS + ["autorizar_exceso", "configurar_reajuste", "cargar_indices", "incorporar_historico"]
FORMULA = {
    "vigente_desde": "2024-08", "mes_base": "2024-07", "factor_rebase": "1", "permite_negativo": True,
    "motivo": "Versión inicial cargada desde el contrato",
    "componentes": [
        {"codigo": "MO", "nombre": "Mano de obra", "ponderacion": "0.40", "valor_base": "1000", "fuente": "Jornal CCT"},
        {"codigo": "GASOIL", "nombre": "Gasoil", "ponderacion": "0.35", "valor_base": "500", "fuente": "Precio cliente"},
        {"codigo": "ACERO", "nombre": "Acero", "ponderacion": "0.25", "valor_base": "2000", "fuente": "Índice publicado"},
    ],
}
INDICES = {"2024-08": (1030, 510, 2020), "2024-09": (1050, 520, 2050), "2024-10": (1100, 530, 2060)}


def _db():
    conn = sqlite3.connect(DB_COPIA, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _crear_usuario(correo, acciones):
    with _db() as conn:
        conn.execute("DELETE FROM usuarios WHERE correo = ?", (correo,))
        conn.execute(
            """
            INSERT INTO usuarios (nombre_apellido, correo, password_hash, estado, tipo_usuario, modulos_json,
                                  paneles_json, acciones_json, bloqueado, intentos_fallidos)
            VALUES (?, ?, ?, 'ACTIVO', 'OPERADOR', ?, ?, ?, 0, 0)
            """,
            (correo, correo, hashlib.sha256(CLAVE.encode()).hexdigest(), json.dumps(["operaciones"]),
             json.dumps({"operaciones": ["seguimiento_cliente"]} if acciones is not None else {}),
             json.dumps({"operaciones": {"seguimiento_cliente": acciones}} if acciones is not None else {})),
        )


def _cliente(usuario, clave=CLAVE):
    cliente = httpx.Client(base_url=BASE_URL, follow_redirects=False, trust_env=False, timeout=60)
    respuesta = cliente.post("/login", data={"username": usuario, "password": clave})
    assert respuesta.headers.get("location") == "/", f"Login fallido para {usuario}"
    return cliente


def _crear_seg(monto=500000, moneda="ARS", items=None, ocs=1):
    with _db() as conn:
        sid = conn.execute(
            "INSERT INTO seguimiento_cc (centro_costo_id, estado_seguimiento, cotizacion, fecha_ultima_actualizacion) "
            "VALUES (1, 'En curso', 'COT-RJ', CURRENT_TIMESTAMP)"
        ).lastrowid
        ordenes, lista_items = [], {}
        for n in range(1, ocs + 1):
            oid = conn.execute(
                "INSERT INTO Orden_de_compra_cliente (seguimiento_cc_id, numero, orden_compra, revision, moneda, monto_oc) "
                "VALUES (?, ?, ?, 'R0', ?, ?)",
                (sid, n, f"RJ{sid}-{n}", moneda, monto),
            ).lastrowid
            ordenes.append(oid)
            lista_items[oid] = []
            for m, (desc, cantidad, precio) in enumerate(items or [], start=1):
                lista_items[oid].append(conn.execute(
                    "INSERT INTO items_oc_cliente (orden_compra_cliente_id, numero, descripcion, unidad, cantidad, moneda, precio, total) "
                    "VALUES (?, ?, ?, 'u', ?, ?, ?, ?)",
                    (oid, m, desc, cantidad, moneda, precio, round(cantidad * precio, 2)),
                ).lastrowid)
    return sid, ordenes, lista_items


def _mes(periodo, dia_final=True):
    anio, mes = map(int, periodo.split("-"))
    ultimo = {2: 28, 4: 30, 6: 30, 9: 30, 11: 30}.get(mes, 31)
    return f"{periodo}-01", f"{periodo}-{ultimo:02d}"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _crear_usuario("rj.admin@test.local", TODAS)
        _crear_usuario("rj.oper@test.local", BASICAS)
        _crear_usuario("rj.indices@test.local", ["consultar", "cargar_indices"])
        _crear_usuario("rj.consulta@test.local", ["consultar"])
        _crear_usuario("rj.sinpanel@test.local", None)
        cls.admin = _cliente("rj.admin@test.local")
        cls.oper = _cliente("rj.oper@test.local")
        cls.indices = _cliente("rj.indices@test.local")
        cls.consulta = _cliente("rj.consulta@test.local")
        cls.sinpanel = _cliente("rj.sinpanel@test.local")
        cls.contingencia = _cliente("contingencia_test", "clave-contingencia-test")

    def ok(self, respuesta, codigo=200):
        self.assertEqual(respuesta.status_code, codigo, respuesta.text)
        return respuesta.json()

    def formula(self, sid, cliente=None, validada=True, **cambios):
        """Crea una versión; por defecto la valida contractualmente, requisito para emitir con ella."""
        datos = self.ok((cliente or self.admin).post(RJ.format(sid=sid) + "/formulas", json={**FORMULA, **cambios}))
        if validada:
            self.validar(sid, datos["version"])
        return datos

    def validar(self, sid, version):
        fid = next(f["id"] for f in self.estado(sid)["formulas"] if f["version"] == version)
        return self.ok(self.admin.post(RJ.format(sid=sid) + f"/formulas/{fid}/validar",
                                       json={"confirmo": True, "referencia_contractual": "Contrato 66-1764, cláusula 8"}))

    def indice(self, sid, codigo, periodo, valor, estado="DEFINITIVO", motivo=None, cliente=None):
        return (cliente or self.admin).post(RJ.format(sid=sid) + "/indices", json={
            "codigo": codigo, "periodo": periodo, "valor": valor, "estado": estado, "motivo": motivo})

    def cargar_indices(self, sid, periodos=("2024-08", "2024-09", "2024-10"), provisorio_oct=True):
        for periodo in periodos:
            for codigo, valor in zip(("MO", "GASOIL", "ACERO"), INDICES[periodo]):
                estado = "PROVISORIO" if provisorio_oct and periodo == "2024-10" and codigo == "MO" else "DEFINITIVO"
                self.ok(self.indice(sid, codigo, periodo, valor, estado))

    def payload(self, oid, periodo, importe, **extra):
        desde, hasta = _mes(periodo)
        return {"fecha_certificado": hasta, "periodo_desde": desde, "periodo_hasta": hasta, "moneda": "ARS", "tipo": "PARCIAL",
                "modalidad": "GLOBAL", "alcance": "OC", "orden_compra_cliente_id": oid,
                "lineas": [{"orden_compra_cliente_id": oid, "importe_periodo": importe}], **extra}

    def borrador(self, sid, oid, periodo, importe, cliente=None, **extra):
        return self.ok((cliente or self.admin).post(CERT.format(sid=sid), json=self.payload(oid, periodo, importe, **extra)))

    def emitir(self, sid, cid, cliente=None, codigo=200, **extra):
        cliente = cliente or self.admin
        calculo = self.ok(cliente.get(CERT.format(sid=sid) + f"/{cid}"))
        huella = self.calcular_borrador(sid, cid, cliente)["reajuste"]["huella"]
        return self.ok(cliente.post(CERT.format(sid=sid) + f"/{cid}/emitir", json={"huella_reajustes": huella, **extra}), codigo)

    def calcular_borrador(self, sid, cid, cliente=None):
        cert = self.ok((cliente or self.admin).get(CERT.format(sid=sid) + f"/{cid}"))
        cuerpo = {
            "certificado_id": cid, "fecha_certificado": cert["fecha_certificado"], "periodo_desde": cert["periodo_desde"],
            "periodo_hasta": cert["periodo_hasta"], "moneda": cert["moneda"], "tipo": cert["tipo"], "modalidad": cert["modalidad"],
            "alcance": cert["alcance"], "orden_compra_cliente_id": cert["orden_compra_cliente_id"],
            "lineas": [{"orden_compra_cliente_id": l["orden_compra_cliente_id"], "importe_periodo": l["importe_periodo"]} for l in cert["lineas"]],
            "descuentos": [{k: d[k] for k in ("concepto", "descripcion", "importe", "base", "porcentaje")} for d in cert["descuentos"]],
        }
        return self.ok((cliente or self.admin).post(CERT.format(sid=sid) + "/calcular", json=cuerpo))

    def emitir_mes(self, sid, oid, periodo, importe, **extra):
        cid = self.borrador(sid, oid, periodo, importe, **extra)["certificado_id"]
        return self.emitir(sid, cid)["certificado"]

    def estado(self, sid, cliente=None):
        return self.ok((cliente or self.admin).get(RJ.format(sid=sid)))

    def pendiente(self, sid):
        return round(sum(l["importe"] for l in self.estado(sid)["pendientes"]), 2)


# ---------------------------------------------------------------------------
class A_Migracion(Base):
    def test_a1_tablas_triggers_y_datos_previos_intactos(self):
        nuevas = {"reajuste_formulas", "reajuste_componentes", "reajuste_indices", "certificados_cliente_reajustes",
                  "reajuste_previsiones", "certificados_reglas_descuento", "certificados_saldo_inicial",
                  "certificados_saldo_inicial_lineas", "reajuste_auditoria"}
        self.assertTrue(nuevas.issubset(HUELLAS_DESPUES))
        for tabla, (columnas, filas) in HUELLAS_ANTES.items():
            columnas_despues, filas_despues = HUELLAS_DESPUES[tabla]
            self.assertEqual(columnas_despues[:len(columnas)], columnas, tabla)
            if tabla in nuevas:
                continue
            # Las columnas previas conservan exactamente sus valores (las nuevas se agregan al final).
            self.assertEqual([tuple(f)[:len(columnas)] for f in filas_despues], [tuple(f) for f in filas], tabla)
        with _db() as conn:
            triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_rj_%'")}
        for nombre in ("trg_rj_formula_solo_validacion", "trg_rj_indices_sin_update", "trg_rj_lineas_update",
                       "trg_rj_cert_emitido_inmutable", "trg_rj_anular_con_retroactivos", "trg_rj_reajuste_auditoria_sin_update"):
            self.assertIn(nombre, triggers)

    def test_a2_migracion_idempotente(self):
        with _db() as conn:
            antes = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
            main.migrar_certificados_cliente(conn)
            main.migrar_reajustes(conn)
            main.migrar_reajustes(conn)
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], antes)

    def test_a3_historicos_protegidos_sin_cambios(self):
        for tabla, fila_id in (("items_oc_cliente", 9), ("items_oc_cliente", 10), ("seguimiento_cc", 2), ("Orden_de_compra_cliente", 6)):
            columnas, filas = HUELLAS_ANTES[tabla]
            _, filas_despues = HUELLAS_DESPUES[tabla]
            antes = [tuple(f) for f in filas if f[0] == fila_id]
            despues = [tuple(f)[:len(columnas)] for f in filas_despues if f[0] == fila_id]
            self.assertEqual(antes, despues, f"{tabla} {fila_id}")


# ---------------------------------------------------------------------------
class B_Formula(Base):
    def test_b1_validaciones_de_la_formula(self):
        sid, _, _ = _crear_seg()
        url = RJ.format(sid=sid) + "/formulas"
        malas = [
            ({"componentes": [{**c, "ponderacion": "0.5"} for c in FORMULA["componentes"]]}, "sumar exactamente 1"),
            ({"componentes": [FORMULA["componentes"][0], FORMULA["componentes"][0]]}, "repetido"),
            ({"motivo": "corto"}, "motivo"),
            ({"mes_base": "2024-09"}, "mes base"),
            ({"factor_rebase": "0"}, "mayor que cero"),
            ({"componentes": [{**FORMULA["componentes"][0], "valor_base": "-1"}]}, "mayor que cero"),
        ]
        for cambio, texto in malas:
            respuesta = self.admin.post(url, json={**FORMULA, **cambio})
            self.assertEqual(respuesta.status_code, 400, cambio)
            self.assertIn(texto, respuesta.json()["detail"].lower() if texto.islower() else respuesta.json()["detail"])
        self.assertEqual(self.formula(sid, validada=False)["version"], 1)
        self.assertEqual(self.formula(sid, validada=False, factor_rebase="1.30589", motivo="Adenda 2: rebase de precios")["version"], 2)
        formulas = self.estado(sid)["formulas"]
        self.assertEqual([f["validacion_contractual"] for f in formulas], ["PENDIENTE", "PENDIENTE"])
        self.assertEqual(formulas[1]["factor_rebase"], "1.30589")

    def test_b2_permisos_de_configuracion(self):
        sid, _, _ = _crear_seg()
        self.assertEqual(self.oper.post(RJ.format(sid=sid) + "/formulas", json=FORMULA).status_code, 403)
        self.assertEqual(self.contingencia.post(RJ.format(sid=sid) + "/formulas", json=FORMULA).status_code, 403)
        self.assertEqual(self.sinpanel.get(RJ.format(sid=sid)).status_code, 403)
        self.ok(self.consulta.get(RJ.format(sid=sid)))
        self.formula(sid)
        self.assertEqual(self.indice(sid, "MO", "2024-08", 1030, cliente=self.oper).status_code, 403)
        self.assertEqual(self.indice(sid, "MO", "2024-08", 1030, cliente=self.contingencia).status_code, 403)
        self.ok(self.indice(sid, "MO", "2024-08", 1030, cliente=self.indices))
        for ruta, cuerpo in (("/numeracion", {"numero_proximo": 5, "motivo": "Certificados previos en planilla"}),
                             ("/previsiones", {"moneda": "ARS", "monto": 1000, "motivo": "Contrato"})):
            self.assertEqual(self.indices.post(RJ.format(sid=sid) + ruta, json=cuerpo).status_code, 403)

    def test_b3_validacion_contractual_auditada_y_unica(self):
        sid, _, _ = _crear_seg()
        self.formula(sid, validada=False)
        fid = self.estado(sid)["formulas"][0]["id"]
        url = RJ.format(sid=sid) + f"/formulas/{fid}/validar"
        self.assertEqual(self.admin.post(url, json={"referencia_contractual": "Contrato 66-1764, cláusula 8"}).status_code, 400)
        self.assertEqual(self.admin.post(url, json={"confirmo": True, "referencia_contractual": "x"}).status_code, 400)
        self.assertEqual(self.contingencia.post(url, json={"confirmo": True, "referencia_contractual": "Contrato 66"}).status_code, 403)
        self.ok(self.admin.post(url, json={"confirmo": True, "referencia_contractual": "Contrato 66-1764, cláusula 8"}))
        formula = self.estado(sid)["formulas"][0]
        self.assertEqual((formula["validacion_contractual"], formula["validada_por"]), ("VALIDADA", "rj.admin@test.local"))
        self.assertTrue(formula["validada_en"])
        self.assertEqual(self.admin.post(url, json={"confirmo": True, "referencia_contractual": "Otra referencia"}).status_code, 409)
        auditoria = self.estado(sid)["auditoria"]
        self.assertEqual(auditoria[0]["accion"], "Fórmula v1 validada contractualmente")
        self.assertIn("cláusula 8", json.dumps(auditoria[0]["detalle"], ensure_ascii=False))

    def test_b4_inmutabilidad_en_la_base(self):
        sid, _, _ = _crear_seg()
        self.formula(sid, validada=False)
        self.ok(self.indice(sid, "MO", "2024-08", 1030))
        with _db() as conn:
            fid = conn.execute("SELECT id FROM reajuste_formulas WHERE seguimiento_cc_id = ?", (sid,)).fetchone()[0]
            for sentencia in (
                f"UPDATE reajuste_formulas SET factor_rebase = '2' WHERE id = {fid}",
                f"DELETE FROM reajuste_formulas WHERE id = {fid}",
                f"UPDATE reajuste_componentes SET ponderacion = '0.5' WHERE formula_id = {fid}",
                f"UPDATE reajuste_indices SET valor = '1' WHERE seguimiento_cc_id = {sid}",
                f"DELETE FROM reajuste_indices WHERE seguimiento_cc_id = {sid}",
                f"UPDATE reajuste_auditoria SET usuario = 'x' WHERE seguimiento_cc_id = {sid}",
                f"DELETE FROM reajuste_auditoria WHERE seguimiento_cc_id = {sid}",
                # Validar sin referencia, o cambiar otro dato al validar, tampoco se admite.
                f"UPDATE reajuste_formulas SET validacion_contractual = 'VALIDADA', validada_por = 'x', validada_en = 'y' WHERE id = {fid}",
                f"UPDATE reajuste_formulas SET validacion_contractual = 'VALIDADA', validada_por = 'x', validada_en = 'y', "
                f"referencia_contractual = 'Contrato 1', motivo = 'otro motivo cualquiera' WHERE id = {fid}",
            ):
                with self.assertRaises(sqlite3.DatabaseError, msg=sentencia):
                    conn.execute(sentencia)


# ---------------------------------------------------------------------------
class C_Indices(Base):
    def test_c1_correccion_con_motivo_y_versiones(self):
        sid, _, _ = _crear_seg()
        self.formula(sid)
        self.assertEqual(self.indice(sid, "COBRE", "2024-08", 10).status_code, 400)
        self.assertEqual(self.indice(sid, "MO", "2024-13", 10).status_code, 400)
        self.assertEqual(self.indice(sid, "MO", "2024-08", 0).status_code, 400)
        self.ok(self.indice(sid, "MO", "2024-08", 1030, "PROVISORIO"))
        self.assertEqual(self.indice(sid, "MO", "2024-08", 1040).status_code, 400)  # corrección sin motivo
        self.assertEqual(self.indice(sid, "MO", "2024-08", 1030, "PROVISORIO", "Mismo valor, sin cambio").status_code, 400)
        self.assertEqual(self.ok(self.indice(sid, "MO", "2024-08", 1040, "DEFINITIVO", "Valor definitivo publicado"))["version"], 2)
        versiones = [i for i in self.estado(sid)["indices"] if i["codigo"] == "MO" and i["periodo"] == "2024-08"]
        self.assertEqual([(v["version"], v["valor"], v["estado"]) for v in versiones], [(1, "1030", "PROVISORIO"), (2, "1040", "DEFINITIVO")])
        self.assertEqual(self.estado(sid)["auditoria"][0]["accion"], "Índice corregido")


# ---------------------------------------------------------------------------
class D_Retroactivos(Base):
    def test_d1_escenario_de_la_maqueta(self):
        """Mismos importes que el prototipo validado y la maqueta aprobada."""
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        self.cargar_indices(sid)
        self.ok(self.admin.post(RJ.format(sid=sid) + "/previsiones", json={"moneda": "ARS", "monto": 15000, "motivo": "Previsión del contrato"}))
        self.ok(self.admin.post(RJ.format(sid=sid) + "/reglas-descuento", json={"concepto": "FONDO_REPARO", "base": "EJECUCION_REAJUSTADA", "porcentaje": 5}))
        agosto = self.emitir_mes(sid, oid, "2024-08", 80000)
        self.assertEqual((agosto["reajuste_periodo"], agosto["total_a_facturar"]), (1720.0, 81720.0))
        septiembre = self.emitir_mes(sid, oid, "2024-09", 90000)
        self.assertEqual(septiembre["reajuste_periodo"], 3622.5)
        self.assertEqual(self.pendiente(sid), 0)

        self.ok(self.indice(sid, "MO", "2024-09", 1080, motivo="Paritaria septiembre (acuerdo CCT)"))
        estado = self.estado(sid)
        self.assertEqual(self.pendiente(sid), 1080.0)
        self.assertEqual(estado["pendientes"][0]["origen_certificado_id"], septiembre["id"])
        self.assertEqual(estado["pendientes"][0]["coeficiente_anterior"], "1.04025")

        descuentos = [{"concepto": "RECUPERO_ANTICIPO", "descripcion": "Anticipo OC", "importe": 5000},
                      {"concepto": "FONDO_REPARO", "descripcion": "Retención contractual", "base": "EJECUCION_REAJUSTADA", "porcentaje": 5}]
        datos = self.borrador(sid, oid, "2024-10", 55000, descuentos=descuentos)
        calculo = datos["calculo"]
        self.assertEqual(calculo["cabecera"]["reajuste_periodo"], 3767.5)
        self.assertEqual(calculo["cabecera"]["reajuste_retroactivo"], 1080.0)
        self.assertEqual(calculo["cabecera"]["descuentos_netos"], 7938.38)
        self.assertEqual(calculo["cabecera"]["total_a_facturar"], 51909.12)
        self.assertEqual(calculo["descuentos"][1]["base_importe"], 58767.5)
        self.assertEqual(calculo["reajuste"]["provisorios"], ["MO Octubre 2024"])
        self.assertEqual(calculo["reajuste"]["prevision"]["saldo"], 15000 - 1720 - 3622.5 - 3767.5 - 1080)
        octubre = self.emitir(sid, datos["certificado_id"])["certificado"]
        self.assertEqual(octubre["total_a_facturar"], 51909.12)
        self.assertEqual(self.pendiente(sid), 0, "un retroactivo reconocido no debe quedar pendiente")
        self.assertEqual(self.estado(sid)["monedas"][0]["reajustes_acumulados"], 10190.0)

        # Segunda corrección: solo se cobra el incremento (90000 × 0,40 × 20/1000 = 720).
        self.ok(self.indice(sid, "MO", "2024-09", 1100, motivo="Paritaria septiembre, segundo tramo"))
        self.assertEqual(self.pendiente(sid), 720.0)
        # El índice definitivo de octubre genera su propia diferencia (55000 × 0,40 × 10/1000 = 220).
        self.ok(self.indice(sid, "MO", "2024-10", 1110, motivo="Valor definitivo de octubre"))
        self.assertEqual(self.pendiente(sid), 940.0)

        # No se anula un certificado cuyo reajuste ya reconoció otro emitido (ni por API ni directo en la base).
        respuesta = self.admin.post(CERT.format(sid=sid) + f"/{septiembre['id']}/anular", json={"motivo": "Prueba de anulación indebida"})
        self.assertEqual(respuesta.status_code, 409)
        with _db() as conn, self.assertRaises(sqlite3.DatabaseError):
            conn.execute("UPDATE certificados_cliente SET estado = 'ANULADO', anulado_por = 'x', anulado_en = 'y', "
                         "motivo_anulacion = 'directo' WHERE id = ?", (septiembre["id"],))
        detalle = self.ok(self.admin.get(CERT.format(sid=sid) + f"/{septiembre['id']}"))
        self.assertIn(octubre["numero_texto"], detalle["bloqueos_anulacion"])

        # Anular el portador devuelve a pendiente lo que había reconocido, y el reemplazo lo toma una sola vez.
        self.ok(self.admin.post(CERT.format(sid=sid) + f"/{octubre['id']}/anular", json={"motivo": "Error en cantidades de octubre"}))
        self.assertEqual(self.pendiente(sid), 1800.0, "un certificado anulado deja de ser origen de retroactivos")
        reemplazo = self.borrador(sid, oid, "2024-10", 55000, reemplaza_a_id=octubre["id"])["calculo"]
        self.assertEqual(reemplazo["cabecera"]["reajuste_retroactivo"], 1800.0)
        self.assertEqual(reemplazo["cabecera"]["reajuste_periodo"], 3987.5)  # C(oct) = 0,444 + 0,371 + 0,2575 = 1,0725

    def test_d2_formula_retroactiva_genera_diferencias(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        self.cargar_indices(sid, ("2024-08",))
        self.emitir_mes(sid, oid, "2024-08", 80000)
        cambios = {"componentes": [{**FORMULA["componentes"][0], "ponderacion": "0.5"}, {**FORMULA["componentes"][1], "ponderacion": "0.25"},
                                   FORMULA["componentes"][2]], "motivo": "Adenda: nueva ponderación retroactiva"}
        self.formula(sid, validada=False, **cambios)
        # Nueva C(ago) = 0,5×1,03 + 0,25×1,02 + 0,25×1,01 = 1,0225 → esperado 1800; reconocido 1720.
        self.assertEqual(self.pendiente(sid), 80.0)
        self.assertEqual(self.estado(sid)["auditoria"][0]["detalle"]["retroactiva"], True)

    def test_d3_huella_rechaza_emitir_con_reajustes_cambiados(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        self.cargar_indices(sid, ("2024-08",))
        cid = self.borrador(sid, oid, "2024-08", 80000)["certificado_id"]
        huella = self.calcular_borrador(sid, cid)["reajuste"]["huella"]
        self.ok(self.indice(sid, "MO", "2024-08", 1050, motivo="Corrección publicada por el organismo"))
        respuesta = self.admin.post(CERT.format(sid=sid) + f"/{cid}/emitir", json={"huella_reajustes": huella})
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("Recalcule", respuesta.json()["detail"])
        self.assertEqual(self.admin.post(CERT.format(sid=sid) + f"/{cid}/emitir", json={}).status_code, 409)
        self.assertEqual(self.emitir(sid, cid)["certificado"]["reajuste_periodo"], 2360.0)  # C = 0,42 + 0,357 + 0,2525

    def test_d4_faltan_indices_no_emite(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        self.cargar_indices(sid, ("2024-08",))
        datos = self.borrador(sid, oid, "2024-11", 1000)
        self.assertTrue(any("Faltan índices" in a for a in datos["calculo"]["advertencias"]))
        respuesta = self.admin.post(CERT.format(sid=sid) + f"/{datos['certificado_id']}/emitir", json={})
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("MO Noviembre 2024", respuesta.json()["detail"])

    def test_d6_formula_pendiente_no_permite_emitir(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid, validada=False)
        self.cargar_indices(sid, ("2024-08", "2024-09"))
        datos = self.borrador(sid, oid, "2024-08", 80000)
        calculo = datos["calculo"]
        self.assertEqual(calculo["cabecera"]["reajuste_periodo"], 1720.0)  # el cálculo se hace igual
        self.assertEqual(calculo["reajuste"]["sin_validar"], ["v1"])
        self.assertTrue(any("pendiente de validación contractual" in a and "no puede emitirse" in a for a in calculo["advertencias"]))
        respuesta = self.admin.post(CERT.format(sid=sid) + f"/{datos['certificado_id']}/emitir",
                                    json={"huella_reajustes": self.calcular_borrador(sid, datos["certificado_id"])["reajuste"]["huella"]})
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("pendiente de validación contractual", respuesta.json()["detail"])
        self.assertEqual(self.ok(self.admin.get(CERT.format(sid=sid) + f"/{datos['certificado_id']}"))["estado"], "BORRADOR")
        _, plano = F_Pdf.texto(self.admin.get(CERT.format(sid=sid) + f"/{datos['certificado_id']}/pdf").content)
        self.assertIn("vista previa sin validez", plano)
        self.assertIn("pendiente de validación contractual", plano)
        # Validada, el mismo borrador se emite.
        self.validar(sid, 1)
        self.assertEqual(self.emitir(sid, datos["certificado_id"])["certificado"]["reajuste_periodo"], 1720.0)
        # Una versión retroactiva pendiente bloquea la emisión de las diferencias que genera.
        cambios = {"componentes": [{**FORMULA["componentes"][0], "ponderacion": "0.5"}, {**FORMULA["componentes"][1], "ponderacion": "0.25"},
                                   FORMULA["componentes"][2]], "motivo": "Adenda: nueva ponderación retroactiva"}
        self.formula(sid, validada=False, **cambios)
        datos = self.borrador(sid, oid, "2024-09", 1000)
        self.assertEqual(datos["calculo"]["reajuste"]["sin_validar"], ["v2"])
        self.assertTrue(any(l["tipo"] == "RETROACTIVO" for l in datos["calculo"]["reajuste"]["lineas"]))
        self.emitir(sid, datos["certificado_id"], codigo=409)
        self.validar(sid, 2)
        self.emitir(sid, datos["certificado_id"])

    def test_d5_negativos_y_rebase(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        for codigo, valor in (("MO", 950), ("GASOIL", 480), ("ACERO", 1950)):
            self.ok(self.indice(sid, codigo, "2024-08", valor))
        # C = 0,38 + 0,336 + 0,24375 = 0,95975 → -3220
        self.assertEqual(self.emitir_mes(sid, oid, "2024-08", 80000)["reajuste_periodo"], -3220.0)
        self.formula(sid, vigente_desde="2024-09", permite_negativo=False, motivo="Versión sin reajustes negativos")
        for codigo, valor in (("MO", 950), ("GASOIL", 480), ("ACERO", 1950)):
            self.ok(self.indice(sid, codigo, "2024-09", valor))
        self.assertEqual(self.borrador(sid, oid, "2024-09", 10000)["calculo"]["cabecera"]["reajuste_periodo"], 0.0)
        # Un retroactivo negativo mayor que la ejecución del mes: el certificado no puede resultar negativo.
        sid2, (oid2,), _ = _crear_seg()
        self.formula(sid2)
        self.cargar_indices(sid2, ("2024-08", "2024-09"))
        self.emitir_mes(sid2, oid2, "2024-08", 80000)
        for codigo, valor in (("MO", 10), ("GASOIL", 5), ("ACERO", 20)):
            self.ok(self.indice(sid2, codigo, "2024-08", valor, motivo="Corrección de prueba a la baja"))
        respuesta = self.admin.post(CERT.format(sid=sid2), json=self.payload(oid2, "2024-09", 1000))
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("negativo", respuesta.json()["detail"])
        # Rebase: REBASE = B × (f − 1) y POLINOMICA = B × f × (C − 1).
        sid3, (oid3,), _ = _crear_seg()
        self.formula(sid3, factor_rebase="1.30589", motivo="Contrato con rebase de precios")
        for codigo, valor in zip(("MO", "GASOIL", "ACERO"), INDICES["2024-08"]):
            self.ok(self.indice(sid3, codigo, "2024-08", valor))
        reajuste = self.borrador(sid3, oid3, "2024-08", 100000)["calculo"]["reajuste"]
        importes = {l["componente"]: l["importe"] for l in reajuste["lineas"]}
        self.assertEqual(importes, {"REBASE": 30589.0, "POLINOMICA": 2807.66})  # 100000 × 1,30589 × 0,0215 = 2807,6635


# ---------------------------------------------------------------------------
class E_Contrato(Base):
    def test_e1_prevision_requiere_autorizacion(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        self.cargar_indices(sid, ("2024-08",))
        self.ok(self.admin.post(RJ.format(sid=sid) + "/previsiones", json={"moneda": "ARS", "monto": 1000, "motivo": "Previsión inicial"}))
        self.assertEqual(self.admin.post(RJ.format(sid=sid) + "/previsiones", json={"moneda": "USD", "monto": 1, "motivo": "Otra moneda"}).status_code, 400)
        datos = self.borrador(sid, oid, "2024-08", 80000, cliente=self.oper)
        self.assertEqual(datos["calculo"]["cabecera"]["exceso_prevision"], 720.0)
        self.assertTrue(datos["calculo"]["requiere_autorizacion_exceso"])
        huella = datos["calculo"]["reajuste"]["huella"]
        respuesta = self.oper.post(CERT.format(sid=sid) + f"/{datos['certificado_id']}/emitir", json={"huella_reajustes": huella})
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self.admin.post(CERT.format(sid=sid) + f"/{datos['certificado_id']}/emitir",
                                         json={"huella_reajustes": huella, "justificacion_exceso": "corta"}).status_code, 400)
        emitido = self.emitir(sid, datos["certificado_id"], justificacion_exceso="Adenda de previsión en trámite con el cliente")
        self.assertEqual(emitido["certificado"]["exceso_prevision"], 720.0)
        with _db() as conn:
            evento = conn.execute("SELECT detalle_json FROM certificados_cliente_eventos WHERE certificado_id = ? AND evento = 'EXCESO_AUTORIZADO'",
                                  (datos["certificado_id"],)).fetchone()
        self.assertEqual(json.loads(evento[0])["exceso_prevision"], 720.0)

    def test_e2_numeracion_y_acumulado_inicial(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        self.cargar_indices(sid, ("2024-08", "2024-09"))
        url = RJ.format(sid=sid)
        self.assertEqual(self.admin.post(url + "/saldo-inicial", json={"hasta_numero": 19, "justificacion": "corta", "modalidad": "GLOBAL",
                                                                       "lineas": [{"orden_compra_cliente_id": oid, "base_acumulada": 100000}]}).status_code, 400)
        self.ok(self.admin.post(url + "/saldo-inicial", json={
            "hasta_numero": 19, "modalidad": "GLOBAL", "justificacion": "Certificados 1 a 19 emitidos en planilla Excel (Cert. N° 19)",
            "lineas": [{"orden_compra_cliente_id": oid, "base_acumulada": 100000, "reajustes_acumulados": 5000}]}))
        estado = self.estado(sid)
        self.assertEqual(estado["numeracion"]["proximo"], 20)
        self.assertEqual(estado["monedas"][0]["reajustes_acumulados"], 5000.0)
        self.assertEqual(self.pendiente(sid), 0, "el acumulado inicial no genera retroactivos")
        cert = self.emitir_mes(sid, oid, "2024-08", 80000)
        self.assertEqual((cert["numero"], cert["acumulado_anterior"], cert["acumulado"]), (20, 100000.0, 180000.0))
        resumen = self.ok(self.admin.get(CERT.format(sid=sid)))["resumen"]["ordenes"][0]
        self.assertEqual(resumen["saldo"], 320000.0)
        self.assertEqual(self.admin.post(url + "/numeracion", json={"numero_proximo": 20, "motivo": "Intento de reutilizar número"}).status_code, 409)
        self.ok(self.admin.post(url + "/numeracion", json={"numero_proximo": 30, "motivo": "Salto acordado con el cliente"}))
        self.assertEqual(self.emitir_mes(sid, oid, "2024-09", 1000)["numero"], 30)
        self.assertEqual(self.admin.post(url + "/saldo-inicial", json={
            "hasta_numero": 25, "modalidad": "GLOBAL", "justificacion": "Corrección posterior que pisaría números ya emitidos",
            "lineas": [{"orden_compra_cliente_id": oid, "base_acumulada": 1}]}).status_code, 409)

    def test_e3_acumulado_inicial_por_items(self):
        sid, (oid,), items = _crear_seg(monto=10000, items=[("Hormigón", 100, 50), ("Acero", 10, 500)])
        url = RJ.format(sid=sid)
        cuerpo = {"hasta_numero": 2, "modalidad": "ITEMS", "justificacion": "Certificados 1 y 2 emitidos antes del sistema",
                  "lineas": [{"orden_compra_cliente_id": oid, "base_acumulada": 1000}]}
        self.assertEqual(self.admin.post(url + "/saldo-inicial", json=cuerpo).status_code, 400)
        cuerpo["lineas"] = [{"orden_compra_cliente_id": oid, "item_oc_cliente_id": items[oid][0], "cantidad": 40}]
        self.ok(self.admin.post(url + "/saldo-inicial", json=cuerpo))
        desde, hasta = _mes("2024-08")
        datos = self.ok(self.admin.post(CERT.format(sid=sid), json={
            "fecha_certificado": hasta, "periodo_desde": desde, "periodo_hasta": hasta, "moneda": "ARS", "tipo": "PARCIAL",
            "modalidad": "ITEMS", "alcance": "OC", "orden_compra_cliente_id": oid,
            "lineas": [{"item_oc_cliente_id": items[oid][0], "cantidad_periodo": 10}]}))
        linea = next(l for l in datos["calculo"]["lineas"] if l["item_oc_cliente_id"] == items[oid][0])
        self.assertEqual((linea["cantidad_anterior"], linea["importe_anterior"], linea["importe_acumulado"]), (40.0, 2000.0, 2500.0))
        self.assertEqual(datos["calculo"]["cabecera"]["acumulado_anterior"], 2000.0)

    def test_e4_certificado_historico_con_reajuste_pagado(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        self.cargar_indices(sid, ("2024-08",))
        desde, hasta = _mes("2024-08")
        cuerpo = {**self.payload(oid, "2024-08", 80000), "numero": 3,
                  "justificacion": "Certificado N° 3 emitido en planilla; PDF original archivado",
                  "reajustes_pagados": [{"orden_compra_cliente_id": oid, "componente": "POLINOMICA", "importe": 1500, "coeficiente": "1.01875"}]}
        self.assertEqual(self.oper.post(CERT.format(sid=sid) + "/historico", json=cuerpo).status_code, 403)
        datos = self.ok(self.admin.post(CERT.format(sid=sid) + "/historico", json=cuerpo))
        cert = self.ok(self.admin.get(CERT.format(sid=sid) + f"/{datos['certificado_id']}"))
        self.assertEqual((cert["estado"], cert["origen"], cert["numero"], cert["reajuste_periodo"]), ("EMITIDO", "HISTORICO", 3, 1500.0))
        # Con los índices vigentes corresponde 1720: la diferencia (220) queda pendiente como retroactivo.
        self.assertEqual(self.pendiente(sid), 220.0)
        self.assertEqual(self.admin.post(CERT.format(sid=sid) + "/historico", json=cuerpo).status_code, 409)
        self.assertEqual(self.estado(sid)["numeracion"]["proximo"], 4)
        self.assertEqual(self.estado(sid)["historicos"][0]["numero"], 3)
        pdf = self.admin.get(CERT.format(sid=sid) + f"/{datos['certificado_id']}/pdf")
        self.assertEqual((pdf.status_code, pdf.content[:4]), (200, b"%PDF"))

    def test_e5_emitidos_inmutables_en_columnas_nuevas(self):
        sid, (oid,), _ = _crear_seg()
        self.formula(sid)
        self.cargar_indices(sid, ("2024-08",))
        cert = self.emitir_mes(sid, oid, "2024-08", 80000)
        with _db() as conn:
            for sentencia in ("UPDATE certificados_cliente SET reajuste_periodo = 0 WHERE id = ?",
                              "UPDATE certificados_cliente SET periodo_ejecucion = '2024-09' WHERE id = ?",
                              "UPDATE certificados_cliente_reajustes SET importe = 0 WHERE certificado_id = ?",
                              "DELETE FROM certificados_cliente_reajustes WHERE certificado_id = ?"):
                with self.assertRaises(sqlite3.DatabaseError, msg=sentencia):
                    conn.execute(sentencia, (cert["id"],))
            snapshot = json.loads(conn.execute("SELECT snapshot_json FROM certificados_cliente WHERE id = ?", (cert["id"],)).fetchone()[0])
        self.assertEqual(snapshot["certificado"]["reajuste_periodo"], 1720.0)
        self.assertEqual(snapshot["reajuste"]["lineas"][0]["importe"], 1720.0)
        self.assertEqual(snapshot["reajuste"]["coeficiente"], "1.0215")


# ---------------------------------------------------------------------------
class F_Pdf(Base):
    """PDF generado por el servidor (diseño aprobado). Requiere pypdf para leer el texto."""

    @staticmethod
    def texto(contenido):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise unittest.SkipTest("pypdf no está instalado: no se puede leer el texto del PDF")
        import io
        import re
        paginas = [p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages]
        return paginas, re.sub(r"\s+", " ", "\n".join(paginas)).replace(" ", " ")

    def test_f1_pdf_con_reajuste_retroactivo_y_descuentos(self):
        sid, (oid,), _ = _crear_seg(moneda="USD")
        self.formula(sid)
        self.cargar_indices(sid)
        self.emitir_mes(sid, oid, "2024-09", 90000, moneda="USD", observaciones="Obra A & B <etapa 1>")
        self.ok(self.indice(sid, "MO", "2024-09", 1080, motivo="Paritaria septiembre (acuerdo CCT)"))
        descuentos = [{"concepto": "FONDO_REPARO", "descripcion": "Retención & garantía", "base": "EJECUCION_REAJUSTADA", "porcentaje": 5}]
        datos = self.borrador(sid, oid, "2024-10", 55000, moneda="USD", descuentos=descuentos)
        borrador = self.admin.get(CERT.format(sid=sid) + f"/{datos['certificado_id']}/pdf")
        paginas, plano = self.texto(borrador.content)
        self.assertIn("vista previa sin validez", plano)
        cert = self.emitir(sid, datos["certificado_id"])["certificado"]
        paginas, plano = self.texto(self.admin.get(CERT.format(sid=sid) + f"/{cert['id']}/pdf").content)
        self.assertIn("Importes en dólares estadounidenses (US$)", plano)
        self.assertNotRegex(plano, r"(?<!US)\$ ")
        self.assertNotIn("pendiente de validación contractual", plano)
        for texto in ("DETALLE DE REAJUSTES", "Fórmula v1", "Índices utilizados",
                      "REAJUSTE · COEF. 1,068500", "DIFERENCIAS RETROACTIVAS", "US$ 3.767,50", "US$ 1.080,00",
                      "Retención & garantía (5,00 % s/ ejecución + reajuste US$ 58.767,50)", "Paritaria septiembre (acuerdo CCT)",
                      f"Verificación: ", "TOTAL A FACTURAR (NETO, SIN IVA)", "US$ 56.909,12"):
            self.assertIn(texto, plano)
        self.assertIn(self.ok(self.admin.get(CERT.format(sid=sid)))["certificados"][1]["numero_texto"], plano)
        # Copia sellada del anulado: conserva el diseño y agrega fecha, usuario y motivo.
        self.ok(self.admin.post(CERT.format(sid=sid) + f"/{cert['id']}/anular", json={"motivo": "Error en la medición & cantidades"}))
        _, plano = self.texto(self.admin.get(CERT.format(sid=sid) + f"/{cert['id']}/pdf?copia=sellada").content)
        self.assertIn("Certificado ANULADO", plano)
        self.assertIn("Motivo: Error en la medición & cantidades", plano)
        # El original sigue siendo el mismo archivo (integridad verificada por el servidor).
        self.assertEqual(self.admin.get(CERT.format(sid=sid) + f"/{cert['id']}/pdf").status_code, 200)

    def test_f2_pdf_sin_formula_no_menciona_reajustes(self):
        sid, (oid,), _ = _crear_seg()
        cert = self.emitir_mes(sid, oid, "2024-08", 1000, observaciones="Texto con < y > y &")
        _, plano = self.texto(self.admin.get(CERT.format(sid=sid) + f"/{cert['id']}/pdf").content)
        self.assertNotIn("reajuste", plano.lower())
        self.assertIn("Importes en pesos argentinos ($)", plano)
        self.assertIn("Texto con < y > y &", plano)


if __name__ == "__main__":
    unittest.main()
