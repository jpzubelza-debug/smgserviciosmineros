"""Puesta en marcha de Facturación y Cobranzas con carga inicial manual por cliente, sobre una COPIA de dashboard.db.

Casos: cliente en cero, factura cobrada (antecedente), factura pendiente, pago parcial (pesos sobre factura en USD)
y prevención de duplicados; además las operaciones nuevas sin fecha de corte global, el control por carga inicial, los bloqueos tras
validar, la auditoría y la migración desde la versión ya instalada (tablas fac_ de la versión 1).

No importa main.py ni abre la base de origen en escritura (copia con la API de backup desde solo lectura).

    .venv-1\\Scripts\\python.exe -m unittest tests.test_puesta_en_marcha -v
    (SMG_TEST_DB_ORIGEN=<ruta> elige otra base de origen, p. ej. una instantánea de la base productiva)

La fecha de inicio 2025-12-31 es SOLO un dato de prueba: la real está pendiente de Contabilidad.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal as D

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import facturacion as svc  # noqa: E402

DB_ORIGEN = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
U, R = "prueba.carga", "prueba.responsable"
T0 = "2026-10-19T10:00:00"
INICIO = "2025-12-31"
FAC = ("fac_", "ix_fac_", "ux_fac_", "trg_fac_", "sqlite_autoindex_fac_")


def _sha(ruta):
    with open(ruta, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _huellas(conn):
    res = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        cols = [r[1] for r in conn.execute(f'PRAGMA table_xinfo("{t}")')]
        filas = conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()
        res[t] = (len(filas), hashlib.sha256(repr(filas).encode()).hexdigest(), cols)
    return res


def _esquema(conn):
    return {r[0]: (r[1], r[2]) for r in conn.execute("SELECT name, type, sql FROM sqlite_master")}


def _sin_fac(ruta):
    """Copia de la base sin ningún objeto fac_ (como antes de instalar el módulo)."""
    c = sqlite3.connect(ruta)
    for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_fac%'").fetchall():
        c.execute(f"DROP TRIGGER {n}")
    for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'fac_%'").fetchall():
        c.execute(f"DROP TABLE {n}")
    c.execute("DELETE FROM sqlite_sequence WHERE name LIKE 'fac_%'")
    c.commit()
    c.close()


class PuestaEnMarcha(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha_origen = _sha(DB_ORIGEN)
        cls.tmp = tempfile.mkdtemp(prefix="smg_test_puesta_marcha_")
        cls.copia = os.path.join(cls.tmp, "origen.db")
        o = sqlite3.connect(f"file:{DB_ORIGEN}?mode=ro", uri=True)
        d = sqlite3.connect(cls.copia)
        o.backup(d)
        d.close()
        o.close()
        c = sqlite3.connect(cls.copia)
        cls.clientes = [r[0] for r in c.execute("SELECT codigo FROM clientes ORDER BY codigo LIMIT 6")]
        cls.cert = c.execute("SELECT c.id, s.cliente_codigo, c.moneda, c.total_a_facturar FROM certificados_cliente c "
                             "JOIN seguimiento_cc s ON s.id = c.seguimiento_cc_id WHERE c.estado = 'EMITIDO' "
                             "AND s.cliente_codigo IS NOT NULL ORDER BY c.id LIMIT 1").fetchone()
        c.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.ruta = os.path.join(self.tmp, f"{self._testMethodName}.db")
        shutil.copyfile(self.copia, self.ruta)
        self.conn = sqlite3.connect(self.ruta)
        self.conn.execute("PRAGMA foreign_keys = ON")
        svc.migrar_facturacion(self.conn, "migracion", T0)
        self.conn.commit()
        self.A, self.B, self.C = self.clientes[:3]

    def tearDown(self):
        self.conn.close()

    # ------------------------------------------------------------ utilidades
    def _inicio(self, fecha=INICIO):
        # La base real ya tiene el corte en borrador sin fecha (creado al instalar la v1): se reutiliza, como en la operación real.
        existente = svc.corte_actual(self.conn)
        if existente:
            self.assertEqual((existente["estado"], existente["fecha_corte"]), ("BORRADOR", None), "el corte de origen no está en borrador sin fecha")
            self._err("CORTE_EXISTE", svc.crear_corte, self.conn, U, T0)
            corte = existente["id"]
        else:
            corte = svc.crear_corte(self.conn, U, T0)
        if fecha:
            svc.fijar_fecha_corte(self.conn, fecha, U, T0)
        return corte

    def _doc(self, cliente, numero, fecha, moneda, neto, tipo="FC", pv=6, vto=None, **extra):
        datos = {"tipo": tipo, "letra": "A", "punto_venta": pv, "numero": numero, "cliente_codigo": cliente, "fecha_emision": fecha,
                 "fecha_vencimiento": None if tipo == "NC" else (vto or fecha), "moneda": moneda,
                 "lineas": [{"descripcion": "Servicio", "alicuota_iva": "21", "neto": neto}]}
        if tipo != "FC":
            datos["motivo_codigo"] = extra.pop("motivo_codigo", "OTRO")
        datos.update(extra)
        return svc.registrar_documento(self.conn, datos, U, T0)

    def _rec(self, cliente, numero, fecha, moneda, importe, imputaciones=(), **extra):
        return svc.registrar_recibo(self.conn, {"punto_venta": 1, "numero": numero, "cliente_codigo": cliente, "fecha_pago": fecha,
                                                "moneda": moneda, "valores": [{"medio": "TRANSFERENCIA", "importe": importe}],
                                                "imputaciones": list(imputaciones), **extra}, U, T0)

    def _err(self, codigo, funcion, *args, **kw):
        # Como main.py (transacción revertida ante un error): un rechazo no deja registros a medias.
        self.conn.execute("SAVEPOINT prueba")
        try:
            with self.assertRaises(svc.FacError) as e:
                funcion(*args, **kw)
        finally:
            self.conn.execute("ROLLBACK TO prueba")
            self.conn.execute("RELEASE prueba")
        self.assertEqual(e.exception.codigo, codigo, str(e.exception))
        return e.exception

    def _validar(self, cliente, situacion, responsable=True):
        return svc.validar_estado_inicial(self.conn, cliente, situacion, "Validado en prueba", R, T0, responsable)

    # ------------------------------------------------------------ 00. migración desde la versión instalada
    def test_00_migracion_solo_fac_idempotente_y_igual_desde_cero(self):
        original = sqlite3.connect(self.copia)
        antes, esquema_antes = _huellas(original), _esquema(original)
        original.close()
        despues, esquema_despues = _huellas(self.conn), _esquema(self.conn)
        for t, h in antes.items():
            if not t.startswith("fac_"):
                self.assertEqual(h, despues[t], f"cambió la tabla preexistente {t}")
            else:
                self.assertEqual(h[:2], despues[t][:2], f"cambiaron filas de {t}")
        for n, v in esquema_antes.items():
            if not n.startswith(FAC):
                self.assertEqual(esquema_despues[n], v, f"cambió la definición de {n}")
        self.assertTrue(all(n.startswith(FAC) for n in set(esquema_despues) - set(esquema_antes)))
        self.assertTrue({"fac_estado_inicial", "fac_antecedentes_certificado"} <= set(despues))
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(fac_cancelaciones_corte)")}
        self.assertTrue({"fecha_pago", "moneda_pago", "importe_pago", "tipo_cambio", "recibo_punto_venta", "recibo_numero",
                         "referencia"} <= cols)
        for _ in range(2):
            svc.migrar_facturacion(self.conn, "migracion", "2026-10-21T00:00:00")
        self.assertEqual(_esquema(self.conn), esquema_despues)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_config").fetchone()[0], len(svc.CONFIG_INICIAL))
        # Instalación desde cero (sin ningún objeto fac_): mismo esquema fac_ que la actualización.
        cero = os.path.join(self.tmp, "desde_cero.db")
        shutil.copyfile(self.copia, cero)
        _sin_fac(cero)
        c = sqlite3.connect(cero)
        svc.migrar_facturacion(c, "migracion", T0)
        c.commit()
        fac_cero = {n: v for n, v in _esquema(c).items() if n.startswith(FAC)}
        c.close()
        self.assertEqual(fac_cero, {n: v for n, v in esquema_despues.items() if n.startswith(FAC)})
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    # ------------------------------------------------------------ 10. operaciones nuevas sin fecha de corte global
    def test_10_operaciones_nuevas_sin_fecha_global_y_control_por_carga_inicial(self):
        A, B, C = self.A, self.B, self.C
        D4 = self.clientes[3]
        corte_origen = svc.corte_actual(self.conn)
        # El seguimiento empieza con las operaciones nuevas: sin fecha de corte (con o sin corte en borrador) se registran.
        self.assertTrue(self._doc(A, 1, "2026-02-01", "USD", "100"))
        self.assertTrue(self._rec(A, 1, "2026-02-02", "USD", "50"))
        # No se habilita ni se modifica nada histórico: el corte queda igual y no se crea ningún estado inicial.
        self.assertEqual(svc.corte_actual(self.conn), corte_origen)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_estado_inicial").fetchone()[0], 0)
        # La carga inicial por cliente sigue exigiendo su fecha para validarse.
        self._inicio(fecha=None)
        self._err("SIN_CORTE", self._validar, B, "CON_ANTECEDENTES")
        svc.fijar_fecha_corte(self.conn, INICIO, U, T0)
        corte = svc.corte_actual(self.conn)["id"]
        # Cliente con carga inicial sin validar: sus operaciones nuevas esperan la validación (comprobantes y cobranzas).
        self._doc(B, 100, "2025-11-15", "USD", "100", origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15")
        self._err("CLIENTE_SIN_VALIDAR", self._doc, B, 2, "2026-02-01", "USD", "100")
        self._err("CLIENTE_SIN_VALIDAR", self._rec, B, 2, "2026-02-01", "USD", "100")
        # También cuenta como carga inicial un saldo según Tango cargado sin comprobantes.
        svc.cargar_conciliacion(self.conn, corte, D4, "ARS", "0", None, U, T0)
        self._err("CLIENTE_SIN_VALIDAR", self._doc, D4, 3, "2026-02-01", "ARS", "100")
        # Los demás clientes siguen operando, incluso con fechas anteriores a la carga inicial de otros (no hay corte global).
        self.assertTrue(self._doc(C, 4, "2026-02-01", "USD", "100"))
        self.assertTrue(self._doc(A, 5, "2025-12-01", "USD", "100"))
        # Validada la carga (solo un responsable), el cliente opera con fechas posteriores a ella.
        svc.cargar_conciliacion(self.conn, corte, B, "USD", "121", None, U, T0)
        self._err("PERMISO", self._validar, B, "CON_ANTECEDENTES", responsable=False)
        self._validar(B, "CON_ANTECEDENTES")
        self._err("ANTERIOR_AL_CORTE", self._doc, B, 2, INICIO, "USD", "100")
        self.assertTrue(self._doc(B, 2, "2026-02-01", "USD", "100"))
        self.assertTrue(self._rec(B, 2, "2026-02-02", "USD", "50"))
        # Validado sin antecedentes con su fecha: el mismo control de fechas.
        self._validar(D4, "SIN_ANTECEDENTES")
        self._err("ANTERIOR_AL_CORTE", self._doc, D4, 3, INICIO, "ARS", "100")
        # Los pedidos no dependen de la puesta en marcha.
        pid = svc.guardar_pedido_borrador(self.conn, {"tipo_solicitado": "FACTURA", "fecha_emision": "2026-02-01", "cliente_codigo": C,
                                                      "moneda": "USD", "lineas": [{"cantidad": 1, "detalle": "x", "precio_unitario": "10",
                                                                                   "alicuota_iva": "21"}]}, U, T0)
        self.assertTrue(pid)

    # ------------------------------------------------------------ 20. cliente nuevo / sin antecedentes: saldo cero
    def test_20_cliente_en_cero(self):
        A = self.A
        self._inicio()
        self._validar(A, "SIN_ANTECEDENTES")
        e = svc.estado_inicial(self.conn, A)
        self.assertEqual(e["situacion"], "SIN_ANTECEDENTES")
        self.assertEqual(e["resumen"]["saldos"], [])
        for mon in ("ARS", "USD", "EUR"):
            self.assertEqual(svc.saldo_cliente(self.conn, A, mon), 0)
            self.assertEqual(svc.cuenta_corriente(self.conn, A, mon, "2026-10-19")["movimientos"], [])
        # La carga inicial queda congelada (servicio y base).
        self._err("CLIENTE_VALIDADO", self._doc, A, 10, "2025-10-01", "USD", "100", origen="SALDO_INICIAL", corte_id=1, vto="2025-11-01")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO fac_documentos (tipo, letra, punto_venta, numero, origen, corte_id, cliente_codigo, fecha_emision, "
                              "fecha_vencimiento, moneda, total, creado_por, creado_en) VALUES ('FC','A',6,11,'SALDO_INICIAL',1,?,"
                              "'2025-10-01','2025-11-01','USD',100,'x','x')", (A,))
        # Sin antecedentes no admite cargas iniciales ni saldo según Tango distinto de cero.
        B = self.B
        self._doc(B, 12, "2025-10-01", "USD", "100", origen="SALDO_INICIAL", corte_id=1, vto="2025-11-01")
        self._err("TIENE_ANTECEDENTES", self._validar, B, "SIN_ANTECEDENTES")
        C = self.C
        svc.cargar_conciliacion(self.conn, 1, C, "ARS", "1000", None, U, T0)
        self._err("SALDO_NO_CERO", self._validar, C, "SIN_ANTECEDENTES")
        self._err("SIN_REGISTROS", self._validar, self.clientes[3], "CON_ANTECEDENTES")

    # ------------------------------------------------------------ 30. factura cobrada antes del inicio (antecedente)
    def test_30_factura_cobrada_como_antecedente(self):
        if not self.cert:
            self.skipTest("la base de origen no tiene certificados emitidos")
        cert_id, cliente, moneda, total = self.cert
        total = svc.r2(total)
        self._inicio()
        docs_antes = self.conn.execute("SELECT count(*) FROM fac_documentos").fetchone()[0]
        ant = svc.registrar_antecedente(self.conn, {"certificado_id": cert_id, "neto_facturado": total, "factura_letra": "A",
                                                    "factura_punto_venta": 6, "factura_numero": 500, "fecha_factura": "2025-11-30",
                                                    "tipo_cambio": "1000" if moneda != "ARS" else None, "nota": "Cobrada según Tango"}, U, T0)
        self.assertTrue(ant)
        self.assertEqual(svc.pendiente_certificado(self.conn, cert_id), 0)
        self.assertEqual(svc._cert_comprometido(self.conn, cert_id, 0), total)
        # No es comprobante, deuda ni cobranza.
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_documentos").fetchone()[0], docs_antes)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_recibos").fetchone()[0], 0)
        self.assertEqual(svc.saldo_cliente(self.conn, cliente, moneda), 0)
        # Duplicados: el mismo antecedente, otro que exceda el certificado y la misma factura como comprobante.
        self._err("CERTIFICADO_EXCEDE", svc.registrar_antecedente, self.conn,
                  {"certificado_id": cert_id, "neto_facturado": total, "factura_letra": "A", "factura_punto_venta": 6, "factura_numero": 500}, U, T0)
        self._err("CERTIFICADO_EXCEDE", svc.registrar_antecedente, self.conn, {"certificado_id": cert_id, "neto_facturado": "1"}, U, T0)
        self._err("DUPLICADO", self._doc, cliente, 500, "2025-11-30", moneda, "1", origen="SALDO_INICIAL", corte_id=1, vto="2025-12-30")
        self._err("POSTERIOR_AL_CORTE", svc.registrar_antecedente, self.conn,
                  {"certificado_id": cert_id, "neto_facturado": "1", "fecha_factura": "2026-01-05"}, U, T0)
        # Un pedido nuevo de ese certificado se rechaza: ya se facturó en Tango.
        pid = svc.guardar_pedido_borrador(self.conn, {"tipo_solicitado": "FACTURA", "fecha_emision": "2026-02-01", "cliente_codigo": cliente,
                                                      "moneda": moneda, "justificacion_diferencia": "Prueba de duplicado",
                                                      "lineas": [{"cantidad": 1, "detalle": "x", "precio_unitario": "10", "alicuota_iva": "21",
                                                                  "certificado_id": cert_id}]}, U, T0)
        self._err("CERTIFICADO_EXCEDE", svc.emitir_pedido, self.conn, pid, U, T0, generar_pdf=lambda s: b"%PDF")
        # Con solo antecedentes se valida con antecedentes (saldo cero) y después la factura tampoco entra como normal.
        self._validar(cliente, "CON_ANTECEDENTES")
        self._err("DUPLICADO", self._doc, cliente, 500, "2026-02-01", moneda, "1")
        self._err("CLIENTE_VALIDADO", svc.registrar_antecedente, self.conn, {"certificado_id": cert_id, "neto_facturado": "1"}, U, T0)

    # ------------------------------------------------------------ 40. factura pendiente al inicio (deuda documentada)
    def test_40_factura_pendiente(self):
        A = self.A
        corte = self._inicio()
        fc = self._doc(A, 445, "2025-11-15", "USD", "1000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15",
                       tipo_cambio_emision="1050", nota="Informe de Tango de comprobantes pendientes")
        self._err("SIN_CONCILIAR", self._validar, A, "CON_ANTECEDENTES")
        svc.cargar_conciliacion(self.conn, corte, A, "USD", "1210", None, U, T0)
        self._validar(A, "CON_ANTECEDENTES")
        self.assertEqual(svc.saldo_cliente(self.conn, A, "USD"), D("1210.00"))
        cc = svc.cuenta_corriente(self.conn, A, "USD", "2026-10-19")
        self.assertEqual([m["saldo_inicial"] for m in cc["movimientos"]], [True])
        self.assertEqual(D(svc.estado_inicial(self.conn, A)["resumen"]["saldos"][0]["saldo_smg"]), D("1210.00"))
        # Después del inicio se cobra con un recibo normal: no se duplica la deuda ni el ingreso.
        self._rec(A, 900, "2026-01-10", "USD", "1210", [{"documento_id": fc, "importe_recibo": "1210"}])
        self.assertEqual(svc.saldo_documento(self.conn, fc), 0)
        self.assertEqual(svc.saldo_cliente(self.conn, A, "USD"), 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_documentos WHERE cliente_codigo = ?", (A,)).fetchone()[0], 1)
        # La misma factura no puede volver a cargarse (ni inicial ni normal).
        self._err("DUPLICADO", self._doc, A, 445, "2026-01-05", "USD", "1000")

    # ------------------------------------------------------------ 50. pago parcial antes del inicio, en pesos de una factura en USD
    def test_50_pago_parcial_con_moneda_y_tc(self):
        A = self.A
        corte = self._inicio()
        base = dict(origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15")
        pago = {"fecha_pago": "2025-12-10", "moneda_pago": "ARS", "importe_pago": "500000", "tipo_cambio": "1000",
                "recibo_punto_venta": 1, "recibo_numero": 77, "referencia": "REC 0001-00000077 de Tango",
                "recibo_total": "500000", "recibo_moneda": "ARS"}
        self._err("PAGO_TC", self._doc, A, 450, "2025-11-15", "USD", "1000", pagos_previos=[{**pago, "tipo_cambio": None}], **base)
        self._err("PAGO_INCONSISTENTE", self._doc, A, 450, "2025-11-15", "USD", "1000", pagos_previos=[{**pago, "importe": "400"}], **base)
        self._err("POSTERIOR_AL_CORTE", self._doc, A, 450, "2025-11-15", "USD", "1000", pagos_previos=[{**pago, "fecha_pago": "2026-01-02"}], **base)
        self._err("CANCELACION", self._doc, A, 450, "2025-11-15", "USD", "1000",
                  pagos_previos=[pago, {"fecha_pago": "2025-12-11", "moneda_pago": "USD", "importe_pago": "710"}], **base)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_documentos").fetchone()[0], 0, "un error no deja registros a medias")
        fc = self._doc(A, 450, "2025-11-15", "USD", "1000", pagos_previos=[pago], **base)
        det = svc.detalle_documento(self.conn, fc, "2026-10-19")
        self.assertEqual(det["total"], D("1210.00"))
        self.assertEqual(det["cancelado_antes_corte"], D("500.00"))
        self.assertEqual(det["saldo"], D("710.00"))
        p = det["cancelaciones_corte"][0]
        self.assertEqual((p["moneda_pago"], D(str(p["importe_pago"])), D(str(p["tipo_cambio"])), p["recibo_numero"], p["criterio"]),
                         ("ARS", D("500000"), D("1000"), 77, "SEGUN_TANGO"))
        # El pago previo no es una cobranza: no hay recibos ni ingresos nuevos.
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_recibos").fetchone()[0], 0)
        svc.cargar_conciliacion(self.conn, corte, A, "USD", "710", None, U, T0)
        self._validar(A, "CON_ANTECEDENTES")
        self.assertEqual(svc.saldo_cliente(self.conn, A, "USD"), D("710.00"))
        # El recibo 77 de Tango ya está como pago previo: no entra como cobranza nueva.
        self._err("DUPLICADO", self._rec, A, 77, "2026-01-10", "ARS", "710000", [{"documento_id": fc, "importe_recibo": "710000", "tipo_cambio": "1000"}])
        self._rec(A, 78, "2026-01-10", "ARS", "710000", [{"documento_id": fc, "importe_recibo": "710000", "tipo_cambio": "1000"}])
        self.assertEqual(svc.saldo_documento(self.conn, fc), 0)
        self.assertEqual(svc.saldo_cliente(self.conn, A, "USD"), 0)
        self.assertEqual(svc.saldo_cliente(self.conn, A, "ARS"), 0)

    # ------------------------------------------------------------ 55. recibos de Tango usados en pagos previos y anticipos
    def test_55_recibos_previos(self):
        A, B = self.A, self.B
        corte = self._inicio()
        n = iter(range(5000, 5100))

        def fac(cliente=A, moneda="ARS", neto="100000", pagos=(), **x):  # total = neto + IVA 21 %
            return self._doc(cliente, next(n), "2025-11-15", moneda, neto, origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15",
                             pagos_previos=list(pagos), **x)

        def pago(importe, nro, total, moneda="ARS", rec_moneda=None, **x):
            return {"fecha_pago": "2025-12-10", "moneda_pago": moneda, "importe_pago": importe, "recibo_punto_venta": 1,
                    "recibo_numero": nro, "recibo_total": total, "recibo_moneda": rec_moneda or moneda, **x}

        def anticipo(nro, importe, moneda="ARS", cliente=A):
            return self._rec(cliente, nro, "2025-12-20", moneda, importe, origen="SALDO_INICIAL", corte_id=corte)

        def atribuido(nro):
            return svc._recibo_previo(self.conn, 1, nro)[1]

        # Total y moneda obligatorios con número de recibo; sin número no se exigen.
        self._err("RECIBO_TOTAL", fac, pagos=[{**pago("1000", 600, "1000"), "recibo_total": None}])
        self._err("RECIBO_TOTAL", fac, pagos=[{**pago("1000", 600, "1000"), "recibo_moneda": None}])
        self._err("RECIBO_TOTAL", fac, pagos=[pago("1000", 600, "0")])
        self.assertTrue(fac(pagos=[{"fecha_pago": "2025-12-10", "importe_pago": "1000"}]))
        # Límites: el total exacto se acepta; un centavo más, no.
        self._err("RECIBO_EXCEDE", fac, pagos=[pago("50000.01", 601, "50000")])
        fac(pagos=[pago("50000", 601, "50000")])
        self.assertEqual(atribuido(601), D("50000.00"))
        # Recibo compartido entre dos facturas del cliente: suma acumulada contra el total.
        fac(pagos=[pago("60000", 602, "100000")])
        self._err("RECIBO_EXCEDE", fac, pagos=[pago("40000.01", 602, "100000")])
        fac(pagos=[pago("40000", 602, "100000")])
        self._err("RECIBO_EXCEDE", fac, pagos=[pago("0.01", 602, "100000")])
        # Mismo recibo con otro total o moneda: inconsistente.
        fac(pagos=[pago("10000", 603, "30000")])
        self._err("RECIBO_INCONSISTENTE", fac, pagos=[pago("10000", 603, "35000")])
        self._err("RECIBO_INCONSISTENTE", fac, pagos=[pago("10", 603, "30000", moneda="USD", tipo_cambio=None)], moneda="USD", neto="100")
        # Dentro de una misma carga (dos pagos del mismo recibo en un comprobante).
        self._err("RECIBO_EXCEDE", fac, pagos=[pago("30000", 604, "50000"), pago("20000.01", 604, "50000")])
        self._err("RECIBO_INCONSISTENTE", fac, pagos=[pago("30000", 604, "50000"), pago("10000", 604, "60000")])
        self.assertEqual(atribuido(604), 0, "un rechazo no deja pagos a medias")
        fac(pagos=[pago("30000", 604, "50000"), pago("20000", 604, "50000")])
        self.assertEqual(atribuido(604), D("50000.00"))
        # Monedas distintas: recibo en pesos aplicado a una factura en USD (TC de Tango); la moneda del pago y la del recibo coinciden.
        self._err("RECIBO_MONEDA", fac, moneda="USD", neto="1000", pagos=[pago("500000", 605, "800000", tipo_cambio="1000", rec_moneda="USD")])
        usd = fac(moneda="USD", neto="1000", pagos=[pago("500000", 605, "800000", tipo_cambio="1000")])
        self.assertEqual(svc.saldo_documento(self.conn, usd), D("710.00"))
        self._err("RECIBO_EXCEDE", fac, moneda="USD", neto="1000", pagos=[pago("300000.01", 605, "800000", tipo_cambio="1000")])
        fac(moneda="ARS", neto="300000", pagos=[pago("300000", 605, "800000")])  # mismo recibo, otra factura en pesos
        self.assertEqual(atribuido(605), D("800000.00"))
        # Anticipo al inicio con el mismo número (la parte a cuenta del recibo).
        fac(pagos=[pago("70000", 606, "100000")])
        self._err("RECIBO_EXCEDE", anticipo, 606, "30000.01")
        self._err("RECIBO_MONEDA", anticipo, 606, "100", moneda="USD")
        anticipo(606, "30000")
        self._err("RECIBO_EXCEDE", fac, pagos=[pago("0.01", 606, "100000")])
        # Anticipo cargado primero y pagos previos después.
        anticipo(607, "40000")
        self._err("RECIBO_EXCEDE", fac, pagos=[pago("60000.01", 607, "100000")])
        self._err("RECIBO_MONEDA", fac, moneda="USD", neto="1000", pagos=[pago("100", 607, "100000", moneda="USD")])
        fac(pagos=[pago("60000", 607, "100000")])
        self.assertEqual(atribuido(607) + svc.a_cuenta_recibo(self.conn, self.conn.execute(
            "SELECT id FROM fac_recibos WHERE numero = 607").fetchone()[0]), D("100000.00"))
        # Duplicados: otro cliente y cobranza nueva con el número de un recibo previo.
        self._err("DUPLICADO", fac, cliente=B, pagos=[pago("1", 602, "100000")])
        self._err("DUPLICADO", anticipo, 602, "1", cliente=B)
        # Anular un comprobante libera lo que su pago previo ocupaba en el recibo.
        libre = fac(pagos=[pago("20000", 608, "20000")])
        self._err("RECIBO_EXCEDE", fac, pagos=[pago("1", 608, "20000")])
        svc.anular_documento(self.conn, libre, "Carga duplicada en la prueba de recibos", R, T0, True)
        fac(pagos=[pago("20000", 608, "20000")])
        self.assertEqual(atribuido(608), D("20000.00"))
        # El detalle conserva el total y la moneda del recibo.
        p = svc.detalle_documento(self.conn, usd, "2026-10-20")["cancelaciones_corte"][0]
        self.assertEqual((D(str(p["recibo_total"])), p["recibo_moneda"], p["moneda_pago"]), (D("800000"), "ARS", "ARS"))

    def test_56_factura_historica_cobrada_sin_certificado(self):
        corte = self._inicio()
        e = self._err("CANCELACION", self._doc, self.A, 5200, "2025-11-15", "ARS", "100", origen="SALDO_INICIAL", corte_id=corte,
                      vto="2025-12-15", pagos_previos=[{"fecha_pago": "2025-12-10", "importe_pago": "121"}])
        self.assertIn("no se carga como saldo inicial", str(e))
        self.assertIn("si no tiene certificado, no se registra", str(e))
        fc = self._doc(self.A, 5201, "2025-11-15", "ARS", "100", origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15")
        e = self._err("CANCELACION", svc.revisar_cancelaciones, self.conn, self.A, "ARS", {fc: "121"}, "SEGUN_TANGO", "Cobrada según Tango", U, T0, True)
        self.assertIn("si no tiene certificado, no se registra", str(e))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_documentos WHERE numero = 5200").fetchone()[0], 0)

    # ------------------------------------------------------------ 60. duplicados, bloqueos y reapertura
    def test_60_duplicados_bloqueos_y_reapertura(self):
        A, B = self.A, self.B
        corte = self._inicio()
        fc = self._doc(A, 460, "2025-11-15", "USD", "1000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15")
        svc.cargar_conciliacion(self.conn, corte, A, "USD", "1210", None, U, T0)
        self._validar(A, "CON_ANTECEDENTES")
        self._err("DUPLICADO", self._validar, A, "CON_ANTECEDENTES")
        with self.assertRaises(sqlite3.IntegrityError):  # índice único: un solo estado vigente por cliente
            self.conn.execute("INSERT INTO fac_estado_inicial (corte_id, cliente_codigo, situacion, resumen_json, creado_por, creado_en) "
                              "VALUES (?, ?, 'SIN_ANTECEDENTES', '{}', 'x', 'x')", (corte, A))
        self._err("CLIENTE_VALIDADO", svc.cargar_conciliacion, self.conn, corte, A, "USD", "1", None, U, T0)
        self._err("CLIENTE_VALIDADO", svc.anular_documento, self.conn, fc, "Error de carga en la prueba unitaria", R, T0, True)
        self._err("CLIENTE_VALIDADO", self._rec, A, 5, INICIO, "USD", "10", origen="SALDO_INICIAL", corte_id=corte)
        self._err("CLIENTES_VALIDADOS", svc.fijar_fecha_corte, self.conn, "2025-11-30", U, T0)
        # Estado inicial inmutable y sin borrados.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE fac_estado_inicial SET situacion = 'SIN_ANTECEDENTES'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM fac_estado_inicial")
        # Reabrir: responsable y motivo; luego se corrige y se vuelve a validar (queda la historia).
        self._err("PERMISO", svc.reabrir_estado_inicial, self.conn, A, "Corrección de la carga inicial en prueba", U, T0, False)
        self._err("MOTIVO", svc.reabrir_estado_inicial, self.conn, A, "corto", R, T0, True)
        svc.reabrir_estado_inicial(self.conn, A, "Corrección de la carga inicial en prueba", R, T0, True)
        svc.cargar_conciliacion(self.conn, corte, A, "USD", "1210", "Revisado", U, T0)
        self._validar(A, "CON_ANTECEDENTES")
        self.assertEqual(self.conn.execute("SELECT estado, count(*) FROM fac_estado_inicial WHERE cliente_codigo = ? GROUP BY estado "
                                           "ORDER BY estado", (A,)).fetchall(), [("ANULADO", 1), ("VIGENTE", 1)])
        # Con operaciones posteriores al inicio ya no se reabre.
        self._rec(A, 920, "2026-01-10", "USD", "100", [{"documento_id": fc, "importe_recibo": "100"}])
        self._err("CON_OPERACIONES", svc.reabrir_estado_inicial, self.conn, A, "Otra corrección de la carga inicial", R, T0, True)
        # Un recibo de Tango no puede ser pago previo de dos clientes, ni pago previo y cobranza nueva.
        self._err("DUPLICADO", self._doc, B, 470, "2025-11-15", "USD", "1000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15",
                  pagos_previos=[{"fecha_pago": "2025-12-01", "importe_pago": "100", "recibo_punto_venta": 1, "recibo_numero": 920,
                                  "recibo_total": "100", "recibo_moneda": "USD"}])
        self._doc(B, 471, "2025-11-15", "USD", "1000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15",
                  pagos_previos=[{"fecha_pago": "2025-12-01", "importe_pago": "100", "recibo_punto_venta": 1, "recibo_numero": 930,
                                  "recibo_total": "100", "recibo_moneda": "USD"}])
        self._err("DUPLICADO", self._doc, self.C, 472, "2025-11-15", "USD", "1000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-15",
                  pagos_previos=[{"fecha_pago": "2025-12-01", "importe_pago": "100", "recibo_punto_venta": 1, "recibo_numero": 930,
                                  "recibo_total": "100", "recibo_moneda": "USD"}])

    # ------------------------------------------------------------ 70. estimaciones revisables y cierre global
    def test_70_estimado_revisable_y_cierre_global(self):
        A, B = self.A, self.B
        corte = self._inicio()
        f1 = self._doc(A, 480, "2025-09-15", "ARS", "1000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-10-15")
        f2 = self._doc(A, 481, "2025-10-15", "ARS", "1000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-11-15")
        svc.estimar_por_antiguedad(self.conn, corte, A, "ARS", "1500", U, T0)
        svc.cargar_conciliacion(self.conn, corte, A, "ARS", "1500", None, U, T0)
        self._validar(A, "CON_ANTECEDENTES")
        self.assertEqual(svc.estado_inicial(self.conn, A)["resumen"]["con_estimados"], ["ARS"])
        # Validado: la estimación se revisa solo por un responsable y sin cambiar el total conciliado.
        self._err("PERMISO", svc.revisar_cancelaciones, self.conn, A, "ARS", {f1: "500", f2: "420"}, "REVISADO", "Según Contabilidad", U, T0, False)
        self._err("TOTAL_CONCILIADO", svc.revisar_cancelaciones, self.conn, A, "ARS", {f1: "500", f2: "400"}, "REVISADO", "Según Contabilidad", R, T0, True)
        svc.revisar_cancelaciones(self.conn, A, "ARS", {f1: "500", f2: "420"}, "REVISADO", "Según Contabilidad", R, T0, True)
        self.assertEqual(svc.saldo_cliente(self.conn, A, "ARS"), D("1500.00"))
        # Cierre global: exige validar a todos los clientes con carga inicial.
        self._doc(B, 482, "2025-10-15", "ARS", "100", origen="SALDO_INICIAL", corte_id=corte, vto="2025-11-15")
        svc.cargar_conciliacion(self.conn, corte, B, "ARS", "121", None, U, T0)
        self._err("SIN_VALIDAR", svc.confirmar_corte, self.conn, R, T0, True)
        self._validar(B, "CON_ANTECEDENTES")
        svc.confirmar_corte(self.conn, R, T0, True)

    # ------------------------------------------------------------ 80. auditoría y listados
    def test_80_auditoria_y_listado(self):
        A = self.A
        self._inicio()
        self._validar(A, "SIN_ANTECEDENTES")
        svc.reabrir_estado_inicial(self.conn, A, "Reapertura de prueba para la auditoría", R, T0, True)
        ev = self.conn.execute("SELECT entidad, evento, usuario FROM fac_eventos WHERE entidad = 'ESTADO_INICIAL' ORDER BY id").fetchall()
        self.assertEqual(ev, [("ESTADO_INICIAL", "VALIDADO", R), ("ESTADO_INICIAL", "REABIERTO", R)])
        lista = svc.puesta_en_marcha(self.conn)
        self.assertEqual(lista["resumen"]["clientes"], self.conn.execute("SELECT count(*) FROM clientes").fetchone()[0])
        self.assertEqual(lista["resumen"]["validados"], 0)
        det = svc.detalle_inicial_cliente(self.conn, A, "2026-10-19")
        self.assertEqual(len(det["eventos"]), 2)
        self.assertIsNone(det["estado_inicial"])

    def test_99_base_de_origen_intacta(self):
        self.assertEqual(_sha(DB_ORIGEN), self.sha_origen)


if __name__ == "__main__":
    unittest.main()
