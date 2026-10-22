"""Pruebas del servicio y la migración de Facturación y Cobranzas (facturacion.py), sobre una COPIA de dashboard.db.

No importa main.py (importarlo ejecuta init_sqlite) ni abre la base real en escritura: la copia se
hace con la API de backup desde una conexión de solo lectura.

    .venv-1\\Scripts\\python.exe -m unittest tests.test_facturacion -v

Los importes son los de la maqueta. La fecha de corte 2025-12-31 es SOLO un dato de prueba: la fecha
real está pendiente de confirmar con Contabilidad y ningún saldo de estas pruebas es definitivo.
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

mig = svc
DB_REAL = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
U, R = "prueba.carga", "prueba.responsable"
T0 = "2026-10-19T10:00:00"
CORTE_PRUEBA = "2025-12-31"
EXAR, VIAP, MODULAR, PIRQ = "000070", "000125", "000142", "000069"


def _sha_archivo(ruta):
    with open(ruta, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _huellas(conn):
    res = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        filas = conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
        res[t] = (len(filas), hashlib.sha256(repr(filas).encode()).hexdigest(), cols)
    return res


def _esquema(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}


def comparar_migracion(prueba, antes_conn, despues_conn):
    """Lo que la migración puede cambiar, también sobre una base que ya tiene el módulo instalado (con datos):
    crea solo las tablas fac_ que faltan; las tablas que no son fac_ quedan idénticas; las fac_ existentes conservan sus
    filas y solo reciben al final las columnas declaradas en FAC_COLUMNAS; los índices existentes no cambian y los
    triggers trg_fac_ se recrean por diseño."""
    antes, despues = _huellas(antes_conn), _huellas(despues_conn)
    esquema_antes, esquema_despues = _esquema(antes_conn), _esquema(despues_conn)
    prueba.assertEqual(set(despues) - set(antes), set(mig.FAC_TABLAS) - set(antes))
    for t, (filas, huella, cols) in antes.items():
        if not t.startswith("fac_"):
            prueba.assertEqual(despues[t], antes[t], f"cambió la tabla existente {t}")
            continue
        cols_despues = despues[t][2]
        prueba.assertEqual(cols_despues[:len(cols)], cols, f"cambiaron las columnas previas de {t}")
        permitidas = [c for c, _ in mig.FAC_COLUMNAS.get(t, ())]
        prueba.assertTrue(set(cols_despues[len(cols):]) <= set(permitidas), f"{t}: columnas no declaradas {cols_despues[len(cols):]}")
        q = f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}" ORDER BY rowid'
        prueba.assertEqual(despues_conn.execute(q).fetchall(), antes_conn.execute(q).fetchall(), f"cambiaron filas de {t}")
    for nombre, sql in esquema_antes.items():
        if nombre.startswith("trg_fac_") or nombre in mig.FAC_COLUMNAS:
            continue
        prueba.assertEqual(esquema_despues.get(nombre), sql, f"cambió la definición de {nombre}")
    prueba.assertTrue(all(n.startswith(("fac_", "ix_fac_", "ux_fac_", "trg_fac_")) for n in set(esquema_despues) - set(esquema_antes)))


class FacturacionSobreCopia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha_real_antes = _sha_archivo(DB_REAL)
        cls.tmp = tempfile.mkdtemp(prefix="smg_test_facturacion_")
        cls.copia = os.path.join(cls.tmp, "dashboard.db")
        origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
        destino = sqlite3.connect(cls.copia)
        origen.backup(destino)
        destino.close()
        origen.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # Cada prueba arranca de una copia nueva y migrada.
        self.ruta = os.path.join(self.tmp, f"{self._testMethodName}.db")
        shutil.copyfile(self.copia, self.ruta)
        self.conn = sqlite3.connect(self.ruta)
        self.conn.execute("PRAGMA foreign_keys = ON")
        mig.migrar_facturacion(self.conn, "migracion", T0)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    # ------------------------------------------------------------ utilidades
    def _corte(self):
        """La base real ya tiene el corte en borrador sin fecha (creado al instalar la v1): se reutiliza, como en la operación."""
        existente = svc.corte_actual(self.conn)
        if not existente:
            return svc.crear_corte(self.conn, U, T0)
        self.assertEqual((existente["estado"], existente["fecha_corte"]), ("BORRADOR", None), "el corte de origen no está en borrador sin fecha")
        with self.assertRaises(svc.FacError) as e:
            svc.crear_corte(self.conn, U, T0)
        self.assertEqual(e.exception.codigo, "CORTE_EXISTE")
        return existente["id"]

    def _corte_confirmado(self, saldos=None):
        """Corte de PRUEBA con un saldo inicial de VIAP (cancelado estimado) y la conciliación cargada.
        Puesta en marcha: VIAP se valida con antecedentes; EXAR y PIRQ, sin antecedentes (operan después del inicio)."""
        c = self.conn
        corte = self._corte()
        svc.fijar_fecha_corte(c, CORTE_PRUEBA, U, T0)
        self._fc(VIAP, 6, 445, "2025-10-15", "ARS", "8348404.24", origen="SALDO_INICIAL", corte_id=corte, vto="2025-11-14")
        svc.estimar_por_antiguedad(c, corte, VIAP, "ARS", "7630941.23", U, T0)
        svc.cargar_conciliacion(c, corte, VIAP, "ARS", "7630941.23", None, U, T0)
        svc.validar_estado_inicial(c, VIAP, "CON_ANTECEDENTES", "Prueba", R, T0, True)
        svc.confirmar_corte(c, R, T0, True)
        for cliente in (EXAR, PIRQ):
            svc.validar_estado_inicial(c, cliente, "SIN_ANTECEDENTES", "Prueba", R, T0, True)
        return corte

    def _fc(self, cliente, pv, numero, fecha, moneda, neto, alic="21", tipo="FC", vto=None, **extra):
        datos = {"tipo": tipo, "letra": "A", "punto_venta": pv, "numero": numero, "cliente_codigo": cliente,
                 "fecha_emision": fecha, "fecha_vencimiento": None if tipo == "NC" else (vto or fecha), "moneda": moneda,
                 "lineas": [{"descripcion": "Servicio", "alicuota_iva": alic, "neto": neto}]}
        if tipo != "FC":
            datos["motivo_codigo"] = extra.pop("motivo_codigo", "OTRO")
        datos.update(extra)
        return svc.registrar_documento(self.conn, datos, U, T0)

    # ------------------------------------------------------------ migración
    def test_01_migracion_solo_agrega_y_es_idempotente(self):
        original = sqlite3.connect(self.copia)
        comparar_migracion(self, original, self.conn)
        original.close()
        esquema_despues = _esquema(self.conn)
        # Segunda y tercera ejecución: mismo esquema, sin duplicar configuración.
        mig.migrar_facturacion(self.conn, "migracion", "2026-10-20T00:00:00")
        mig.migrar_facturacion(self.conn, "migracion", "2026-10-21T00:00:00")
        self.assertEqual(_esquema(self.conn), esquema_despues)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fac_config").fetchone()[0], len(mig.CONFIG_INICIAL))
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_02_migracion_no_pisa_configuracion(self):
        svc.guardar_config(self.conn, "adjuntos_requeridos", {**mig.CONFIG_INICIAL["adjuntos_requeridos"], "FC": True}, R, T0, True)
        mig.migrar_facturacion(self.conn, "migracion", T0)
        self.assertTrue(svc.leer_config(self.conn, "adjuntos_requeridos")["FC"])

    # ------------------------------------------------------------ cálculos puros
    def test_10_iva_por_alicuota_percepcion_y_redondeo(self):
        lineas = [{"alicuota_iva": "21", "neto": "1000000.00"}, {"alicuota_iva": "10.5", "neto": "333333.33"},
                  {"alicuota_iva": "0", "neto": "50.00"}]
        trib = [{"tipo": "PERCEPCION_IIBB", "jurisdiccion": "Jujuy", "base": "1000000.00", "alicuota": "1.5"}]
        r = svc.calcular_importes(lineas, trib)
        self.assertEqual(r["iva_por_alicuota"], {"21": D("210000.00"), "10.5": D("35000.00")})
        self.assertEqual(r["tributos_total"], D("15000.00"))
        self.assertEqual(r["total"], D("1593383.33"))
        con_pdf = svc.calcular_importes(lineas, trib, total_pdf="1593383.36")
        self.assertEqual((con_pdf["redondeo"], con_pdf["total"]), (D("0.03"), D("1593383.36")))
        with self.assertRaises(svc.FacError) as e:
            svc.calcular_importes(lineas, trib, total_pdf="1593383.43")
        self.assertEqual(e.exception.codigo, "TOTAL_NO_COINCIDE")

    def test_11_equivalente_ars_usd(self):
        self.assertEqual(svc.equivalente("42000000.00", "60175.43", "1383"), D("30368.76"))
        # Pesos que cubren el saldo dentro de un centavo de USD: cancela exacto.
        self.assertEqual(svc.equivalente("41222624.61", "29806.67", "1383"), D("29806.67"))
        self.assertEqual(svc.equivalente("41222612.00", "29806.67", "1383"), D("29806.67"))
        self.assertEqual(svc.equivalente("41222600.00", "29806.67", "1383"), D("29806.65"))

    # ------------------------------------------------------------ corte y saldos iniciales
    def test_20_sin_fecha_de_corte_se_opera_pero_no_se_confirma(self):
        c = self.conn
        corte = self._corte()  # fecha pendiente de Contabilidad
        self.assertIsNone(svc.corte_actual(c)["fecha_corte"])
        # Sin fecha de corte global las operaciones nuevas se registran (EXAR no tiene carga inicial).
        fc = self._fc(EXAR, 6, 600, "2026-10-01", "USD", "1000")
        self.assertEqual(svc.documento(c, fc)["total"], D("1210.00"))
        self.assertIsNone(svc.corte_actual(c)["fecha_corte"], "registrar una operación no fija ni cambia la fecha del corte")
        with self.assertRaises(svc.FacError) as e:
            svc.confirmar_corte(c, R, T0, True)
        self.assertEqual(e.exception.codigo, "CORTE_FECHA")
        # Los pedidos sí funcionan sin corte.
        pid = svc.guardar_pedido_borrador(c, {"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-19", "cliente_codigo": EXAR,
                                              "moneda": "USD", "condicion_pago": "30 FF",
                                              "lineas": [{"cantidad": 1, "detalle": "Cert. 1", "precio_unitario": "1000",
                                                          "alicuota_iva": "21"}]}, U, T0)
        previos = c.execute("SELECT count(*) FROM fac_pedidos WHERE numero_solicitud LIKE '20261019-%'").fetchone()[0]  # la base real ya tiene uno
        self.assertEqual(svc.emitir_pedido(c, pid, U, T0), f"20261019-{previos + 1}")
        self.assertIsNotNone(corte)

    def test_21_cancelado_estimado_por_antiguedad_queda_marcado(self):
        c = self.conn
        corte = self._corte()
        svc.fijar_fecha_corte(c, CORTE_PRUEBA, U, T0)
        fc = self._fc(VIAP, 6, 445, "2025-10-15", "ARS", "8348404.24", origen="SALDO_INICIAL", corte_id=corte, vto="2025-11-14")
        self.assertEqual(svc.documento(c, fc)["total"], D("10101569.13"))
        filas = svc.estimar_por_antiguedad(c, corte, VIAP, "ARS", "7630941.23", U, T0)
        self.assertEqual(filas, [{"documento_id": fc, "importe": D("2470627.90")}])
        self.assertEqual(svc.cancelaciones_estimadas(c, fc), D("2470627.90"))
        self.assertEqual(svc.saldo_documento(c, fc), D("7630941.23"))
        crit = c.execute("SELECT criterio FROM fac_cancelaciones_corte WHERE documento_id = ?", (fc,)).fetchone()[0]
        self.assertEqual(crit, "ESTIMADO_ANTIGUEDAD")
        svc.cargar_conciliacion(c, corte, VIAP, "ARS", "7630941.23", None, U, T0)
        fila = svc.conciliacion(c, corte)[0]
        self.assertTrue(fila["conciliada"])
        self.assertEqual(fila["cancelado_estimado"], D("2470627.90"))

    def test_22_revision_de_estimado_conserva_historial_y_total(self):
        c = self.conn
        corte = self._corte()
        svc.fijar_fecha_corte(c, CORTE_PRUEBA, U, T0)
        viejo = self._fc(EXAR, 6, 480, "2025-09-30", "USD", "100000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-10-30")
        nuevo = self._fc(EXAR, 6, 490, "2025-11-30", "USD", "50000", origen="SALDO_INICIAL", corte_id=corte, vto="2025-12-30")
        # Facturado abierto 181.500 USD; Tango dice 151.500: se estima que se cobró 30.000 de la más vieja.
        svc.estimar_por_antiguedad(c, corte, EXAR, "USD", "151500.00", U, T0)
        self.assertEqual((svc.cancelado_antes_corte(c, viejo), svc.cancelado_antes_corte(c, nuevo)), (D("30000.00"), D("0")))
        svc.cargar_conciliacion(c, corte, EXAR, "USD", "151500.00", None, U, T0)
        svc.validar_estado_inicial(c, EXAR, "CON_ANTECEDENTES", "Prueba", R, T0, True)
        svc.confirmar_corte(c, R, T0, True)
        # Con el corte confirmado, la revisión no puede cambiar el total conciliado...
        with self.assertRaises(svc.FacError) as e:
            svc.revisar_cancelaciones(c, EXAR, "USD", {viejo: "20000"}, "REVISADO", "Contabilidad informa imputación real", R, T0, True)
        self.assertEqual(e.exception.codigo, "TOTAL_CONCILIADO")
        # ...ni hacerla alguien que no es responsable.
        with self.assertRaises(svc.FacError):
            svc.revisar_cancelaciones(c, EXAR, "USD", {viejo: "0", nuevo: "30000"}, "SEGUN_TANGO", "Recibo 0001-410 imputado", U, T0, False)
        # Redistribuir sí: Tango informa que el recibo canceló parte de la nueva.
        svc.revisar_cancelaciones(c, EXAR, "USD", {viejo: "10000", nuevo: "20000"}, "SEGUN_TANGO",
                                  "Recibo 0001-410 imputado según Tango", R, T0, True)
        self.assertEqual(svc.saldo_cliente(c, EXAR, "USD"), D("151500.00"))
        filas = c.execute("SELECT documento_id, importe, criterio, estado, revision_de_id FROM fac_cancelaciones_corte ORDER BY id").fetchall()
        self.assertEqual([f[2:4] for f in filas], [("ESTIMADO_ANTIGUEDAD", "ANULADO"), ("SEGUN_TANGO", "VIGENTE"), ("SEGUN_TANGO", "VIGENTE")])
        self.assertEqual(filas[1][4], filas[0][0])
        self.assertEqual(svc.cancelaciones_estimadas(c, viejo), D("0"))

    def test_23_corte_confirmado_bloquea_fechas_y_altas_sin_motivo(self):
        corte = self._corte_confirmado()
        c = self.conn
        with self.assertRaises(svc.FacError) as e:
            self._fc(EXAR, 6, 601, CORTE_PRUEBA, "USD", "1000")
        self.assertEqual(e.exception.codigo, "ANTERIOR_AL_CORTE")
        with self.assertRaises(sqlite3.IntegrityError):
            self._fc(MODULAR, 6, 484, "2025-12-15", "ARS", "1000", origen="SALDO_INICIAL", corte_id=corte)
        self._fc(MODULAR, 6, 484, "2025-12-15", "ARS", "1000", origen="SALDO_INICIAL", corte_id=corte,
                 motivo_alta_post_corte="Omitido en la carga inicial; figura en el informe de Tango")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE fac_cortes SET fecha_corte = '2026-01-31'")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE fac_corte_conciliacion SET saldo_tango = 0")
        with self.assertRaises(svc.FacError) as e:
            svc.confirmar_corte(c, R, T0, True)
        self.assertEqual(e.exception.codigo, "CORTE_ESTADO")

    def test_24_corte_sin_conciliar_no_se_confirma(self):
        c = self.conn
        corte = self._corte()
        svc.fijar_fecha_corte(c, CORTE_PRUEBA, U, T0)
        self._fc(VIAP, 6, 445, "2025-10-15", "ARS", "8348404.24", origen="SALDO_INICIAL", corte_id=corte, vto="2025-11-14")
        with self.assertRaises(svc.FacError) as e:
            svc.confirmar_corte(c, R, T0, True)
        self.assertEqual(e.exception.codigo, "SIN_CONCILIAR")
        with self.assertRaises(svc.FacError) as e:
            svc.confirmar_corte(c, U, T0, False)
        self.assertEqual(e.exception.codigo, "PERMISO")

    # ------------------------------------------------------------ comprobantes, NC y cobros
    def test_30_fc_usd_nc_y_cobro_en_pesos(self):
        self._corte_confirmado()
        c = self.conn
        fc = self._fc(EXAR, 6, 514, "2026-02-28", "USD", "290000", vto="2026-03-30")
        self.assertEqual(svc.documento(c, fc)["total"], D("350900.00"))
        nc = self._fc(EXAR, 5, 185, "2026-05-14", "USD", "240268.24", tipo="NC", motivo_codigo="AJUSTE_PRECIO", documento_origen_id=fc)
        svc.aplicar_nc(c, nc, fc, "290724.57", U, T0)
        self.assertEqual(svc.saldo_documento(c, fc), D("60175.43"))
        self.assertEqual(svc.estado_documento(c, nc, "2026-10-19"), "APLICADA")
        rec = svc.registrar_recibo(c, {"punto_venta": 1, "numero": 472, "cliente_codigo": EXAR, "fecha_pago": "2026-10-16",
                                       "moneda": "ARS", "valores": [{"medio": "TRANSFERENCIA", "importe": "41500000"},
                                                                    {"medio": "RETENCION_IIBB", "importe": "500000"}],
                                       "imputaciones": [{"documento_id": fc, "importe_recibo": "42000000", "tipo_cambio": "1383"}]},
                                   U, T0)
        imp = c.execute("SELECT importe_documento, saldo_documento_antes, saldo_documento_despues, fecha_tipo_cambio "
                        "FROM fac_imputaciones WHERE recibo_id = ?", (rec,)).fetchone()
        self.assertEqual([svc.r2(x) for x in imp[:3]] + [imp[3]], [D("30368.76"), D("60175.43"), D("29806.67"), "2026-10-16"])
        self.assertEqual(svc.a_cuenta_recibo(c, rec), D("0"))
        self.assertEqual(svc.estado_documento(c, fc, "2026-10-19"), "VENCIDA")
        # Saldos separados por moneda: el cobro en pesos no genera saldo en ARS.
        self.assertEqual(svc.saldo_cliente(c, EXAR, "USD"), D("29806.67"))
        self.assertEqual(svc.saldo_cliente(c, EXAR, "ARS"), D("0"))
        # Cobro final que cancela exacto.
        svc.registrar_recibo(c, {"punto_venta": 1, "numero": 480, "cliente_codigo": EXAR, "fecha_pago": "2026-11-05", "moneda": "ARS",
                                 "valores": [{"medio": "TRANSFERENCIA", "importe": "41222620.00"}],
                                 "imputaciones": [{"documento_id": fc, "importe_recibo": "41222620.00", "tipo_cambio": "1383"}]},
                             U, T0)
        self.assertEqual(svc.saldo_documento(c, fc), D("0"))
        self.assertEqual(svc.estado_documento(c, fc, "2026-11-05"), "CANCELADA")
        # Sin TC no se imputa entre monedas.
        fc2 = self._fc(EXAR, 6, 530, "2026-10-01", "USD", "1000", vto="2026-10-31")
        with self.assertRaises(svc.FacError) as e:
            svc.registrar_recibo(c, {"punto_venta": 1, "numero": 481, "cliente_codigo": EXAR, "fecha_pago": "2026-11-05",
                                     "moneda": "ARS", "valores": [{"medio": "TRANSFERENCIA", "importe": "1000"}],
                                     "imputaciones": [{"documento_id": fc2, "importe_recibo": "1000"}]}, U, T0)
        self.assertEqual(e.exception.codigo, "TC_REQUERIDO")

    def test_31_nd_diferencia_de_cambio_se_carga_y_no_se_calcula(self):
        self._corte_confirmado()
        c = self.conn
        fc = self._fc(EXAR, 6, 505, "2026-01-30", "USD", "10000", vto="2026-03-01")
        nd = self._fc(EXAR, 5, 25, "2026-04-10", "ARS", "1500000", tipo="ND", motivo_codigo="DIFERENCIA_CAMBIO",
                      documento_origen_id=fc, vto="2026-05-10")
        self.assertEqual(svc.saldo_cliente(c, EXAR, "ARS"), D("1815000.00"))
        self.assertEqual(svc.saldo_cliente(c, EXAR, "USD"), D("12100.00"))
        nc_ars = self._fc(EXAR, 5, 30, "2026-04-11", "ARS", "100", tipo="NC")
        with self.assertRaises(svc.FacError) as e:
            svc.aplicar_nc(c, nc_ars, fc, "121", U, T0)
        self.assertEqual(e.exception.codigo, "MONEDA")
        self.assertIsNotNone(nd)

    def test_32_duplicados(self):
        self._corte_confirmado()
        self._fc(EXAR, 6, 514, "2026-02-28", "USD", "290000", vto="2026-03-30")
        with self.assertRaises(svc.FacError) as e:
            self._fc(PIRQ, 6, 514, "2026-03-01", "ARS", "10", vto="2026-03-30")
        self.assertEqual(e.exception.codigo, "DUPLICADO")
        with self.assertRaises(sqlite3.IntegrityError):  # el índice único protege aunque se saltee el servicio
            self.conn.execute("INSERT INTO fac_documentos (tipo, letra, punto_venta, numero, cliente_codigo, fecha_emision, "
                              "fecha_vencimiento, moneda, total, creado_por, creado_en) VALUES "
                              "('FC','A',6,514,?, '2026-03-01','2026-03-02','ARS',10,'x','x')", (PIRQ,))

    def test_33_factura_con_varios_pedidos(self):
        self._corte_confirmado()
        c = self.conn
        pedidos = []
        for neto in ("4000000", "1500000"):
            pid = svc.guardar_pedido_borrador(c, {"tipo_solicitado": "FACTURA", "fecha_emision": "2026-10-10", "cliente_codigo": VIAP,
                                                  "moneda": "ARS", "lineas": [{"cantidad": 1, "detalle": "Servicio", "precio_unitario": neto,
                                                                               "alicuota_iva": "21"}]}, U, T0)
            svc.emitir_pedido(c, pid, U, T0)
            pedidos.append(pid)
        nuevos = c.execute(f"SELECT numero_solicitud FROM fac_pedidos WHERE id IN ({pedidos[0]}, {pedidos[1]}) ORDER BY id").fetchall()
        self.assertEqual(nuevos, [("20261010-1",), ("20261010-2",)])  # numeración por fecha, sin contar los pedidos previos de otras fechas
        vinc = [{"pedido_id": pedidos[0], "neto_asignado": "4000000"}, {"pedido_id": pedidos[1], "neto_asignado": "1500000"}]
        self._fc(VIAP, 6, 523, "2026-10-15", "ARS", "5500000", vto="2026-11-14", vinculos=vinc)
        self.assertEqual([svc.facturado_pedido(c, p) for p in pedidos], [D("4000000.00"), D("1500000.00")])
        with self.assertRaises(svc.FacError) as e:
            self._fc(VIAP, 6, 524, "2026-10-15", "ARS", "100", vto="2026-11-14",
                     vinculos=[{"pedido_id": pedidos[0], "neto_asignado": "101"}])
        self.assertEqual(e.exception.codigo, "VINCULO_EXCEDE")
        # Pedido emitido: inmutable y sin borrado.
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE fac_pedidos SET total = 1 WHERE id = ?", (pedidos[0],))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("DELETE FROM fac_pedidos WHERE id = ?", (pedidos[0],))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE fac_pedido_lineas SET detalle = 'x' WHERE pedido_id = ?", (pedidos[0],))

    def test_34_anulacion_en_cascada_conserva_historial(self):
        self._corte_confirmado()
        c = self.conn
        fc = self._fc(EXAR, 6, 514, "2026-02-28", "USD", "290000", vto="2026-03-30")
        rec = svc.registrar_recibo(c, {"punto_venta": 1, "numero": 472, "cliente_codigo": EXAR, "fecha_pago": "2026-10-16",
                                       "moneda": "USD", "valores": [{"medio": "TRANSFERENCIA", "importe": "1000"}],
                                       "imputaciones": [{"documento_id": fc, "importe_recibo": "1000"}]}, U, T0)
        with self.assertRaises(svc.FacError):
            svc.anular_documento(c, fc, "Error de carga: número equivocado", U, T0, False)
        with self.assertRaises(svc.FacError):
            svc.anular_documento(c, fc, "corto", R, T0, True)
        svc.anular_documento(c, fc, "Error de carga: número de comprobante equivocado", R, T0, True)
        self.assertEqual(svc.estado_documento(c, fc, "2026-10-19"), "ANULADO")
        self.assertEqual(svc.a_cuenta_recibo(c, rec), D("1000.00"))
        self.assertEqual(c.execute("SELECT estado, anulado_por FROM fac_imputaciones").fetchone(), ("ANULADO", R))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE fac_documentos SET estado = 'VIGENTE' WHERE id = ?", (fc,))
        for tabla in ("fac_documentos", "fac_imputaciones", "fac_recibos", "fac_eventos"):
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(f"DELETE FROM {tabla}")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE fac_eventos SET usuario = 'otro'")
        # Se puede volver a cargar el número correcto (el anulado no cuenta para el único).
        self._fc(EXAR, 6, 514, "2026-02-28", "USD", "290000", vto="2026-03-30", reemplaza_a_id=fc)

    # ------------------------------------------------------------ adjuntos opcionales
    def test_40_adjuntos_opcionales_y_configurables(self):
        self._corte_confirmado()
        c = self.conn
        sin_adjunto = self._fc(EXAR, 6, 700, "2026-10-01", "USD", "1000", vto="2026-10-31")  # por defecto, no se exige
        self.assertEqual(c.execute("SELECT count(*) FROM fac_adjuntos").fetchone()[0], 0)
        self.assertFalse(any(svc.leer_config(c, "adjuntos_requeridos").values()))
        with self.assertRaises(svc.FacError):
            svc.guardar_config(c, "adjuntos_requeridos", {"FC": True}, U, T0, False)
        svc.guardar_config(c, "adjuntos_requeridos", {**mig.CONFIG_INICIAL["adjuntos_requeridos"], "FC": True}, R, T0, True)
        with self.assertRaises(svc.FacError) as e:
            self._fc(EXAR, 6, 701, "2026-10-01", "USD", "1000", vto="2026-10-31")
        self.assertEqual(e.exception.codigo, "ADJUNTO_REQUERIDO")
        enlace = {"tipo": "COMPROBANTE", "nombre": "FC 6-701.pdf", "url_externa": "https://drive.google.com/file/d/ejemplo"}
        svc.registrar_documento(c, {"tipo": "FC", "letra": "A", "punto_venta": 6, "numero": 701, "cliente_codigo": EXAR,
                                    "fecha_emision": "2026-10-01", "fecha_vencimiento": "2026-10-31", "moneda": "USD",
                                    "lineas": [{"alicuota_iva": "21", "neto": "1000"}]}, U, T0, adjuntos=[enlace])
        # La exigencia no es retroactiva: el comprobante anterior sigue operable (se le puede cobrar y adjuntar después).
        svc.registrar_recibo(c, {"punto_venta": 1, "numero": 900, "cliente_codigo": EXAR, "fecha_pago": "2026-10-20", "moneda": "USD",
                                 "valores": [{"medio": "TRANSFERENCIA", "importe": "1210"}],
                                 "imputaciones": [{"documento_id": sin_adjunto, "importe_recibo": "1210"}]}, U, T0)
        svc.adjuntar(c, "DOCUMENTO", sin_adjunto, enlace, U, T0)
        # Mientras el almacenamiento sea ENLACE, no se guardan archivos en el servidor.
        with self.assertRaises(svc.FacError) as e:
            svc.adjuntar(c, "DOCUMENTO", sin_adjunto, {"nombre": "x.pdf", "ruta": "Doc_Facturacion/x.pdf", "sha256": "0" * 64}, U, T0)
        self.assertEqual(e.exception.codigo, "ADJUNTO_ALMACENAMIENTO")

    # ------------------------------------------------------------ al final: integridad y base real intacta
    def test_99_integridad_y_base_real_intacta(self):
        self._corte_confirmado()
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        eventos = self.conn.execute("SELECT evento FROM fac_eventos ORDER BY id").fetchall()
        self.assertIn(("CANCELADO_ESTIMADO",), eventos)
        self.assertIn(("CONFIRMADO",), eventos)
        detalle = json.loads(self.conn.execute("SELECT detalle_json FROM fac_eventos WHERE evento = 'CONFIRMADO'").fetchone()[0])
        self.assertEqual(detalle["con_estimados"], [f"{VIAP} ARS"])
        self.assertEqual(_sha_archivo(DB_REAL), self.sha_real_antes, "la base real cambió durante las pruebas")


if __name__ == "__main__":
    unittest.main(verbosity=2)
