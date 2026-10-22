"""Pruebas del dashboard de Almacen: consumo por centro de costos (destino del remito de salida).

Se ejecutan sobre una COPIA de dashboard.db en una carpeta temporal (DATA_DIR se fija antes de
importar main). Ejecutar en un proceso propio:

    .venv\\Scripts\\python.exe -m unittest tests.test_almacen_dashboard -v

La base de origen se elige con SMG_TEST_DB_ORIGEN (por defecto, dashboard.db, abierta en solo lectura).
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

if "main" in sys.modules:
    raise unittest.SkipTest("main ya fue importado con otra DATA_DIR; ejecute este módulo por separado.")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_REAL = os.path.abspath(os.environ.get("SMG_TEST_DB_ORIGEN") or os.path.join(BASE_DIR, "dashboard.db"))
TMP_DIR = tempfile.mkdtemp(prefix="smg_test_almacen_dashboard_")
DB_COPIA = os.path.join(TMP_DIR, "dashboard.db")

_origen = sqlite3.connect(f"file:{DB_REAL}?mode=ro", uri=True)
_destino = sqlite3.connect(DB_COPIA)
_origen.backup(_destino)
_destino.close()
_origen.close()

os.environ["DATA_DIR"] = TMP_DIR
sys.path.insert(0, BASE_DIR)
import main  # noqa: E402

SIN_CC = "Sin centro de costos"


def tearDownModule():
    shutil.rmtree(TMP_DIR, ignore_errors=True)


class ConsumoPorCentroCostosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patches = [
            mock.patch.object(main, "_enviar_notificacion_remito", lambda **kw: None),
            mock.patch.object(main, "_generar_pdf_remito_entrega", lambda *a, **kw: os.path.join(TMP_DIR, "remito.pdf")),
        ]
        for p in cls.patches:
            p.start()
        with main.get_sqlite_connection() as conn:
            cls.base = cls._totales_por_cc(conn)
            ids = []
            for i in range(3):
                cur = conn.execute(
                    "INSERT INTO productos (codigo, descripcion) VALUES (?, ?)",
                    (f"TST-DASH-{i}", f"Producto de prueba dashboard {i}"),
                )
                ids.append(cur.lastrowid)
                main.ajustar_stock_producto(conn, cur.lastrowid, 500)
            conn.commit()
        cls.ids = ids

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()

    @staticmethod
    def _totales_por_cc(conn):
        """Totales esperados calculados desde los remitos de salida autorizados (no desde movimientos)."""
        filas = conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(r.direccion), ''), NULLIF(TRIM(d.proyecto), ''), ?) AS cc,
                   SUM(d.cantidad) AS total
            FROM remitos_entrega r
            JOIN remitos_entrega_detalle d ON d.remito_id = r.id
            WHERE UPPER(COALESCE(r.estado_autorizacion, 'AUTORIZADO')) = 'AUTORIZADO'
            GROUP BY 1
            """,
            (SIN_CC,),
        ).fetchall()
        return {f["cc"]: float(f["total"]) for f in filas}

    def _salida(self, direccion, items, autorizar=True):
        payload = main.AlmacenREPayload(
            destinatario="Prueba",
            direccion=direccion,
            items=[main.AlmacenRemitoItemPayload(producto_id=pid, cantidad=cant) for pid, cant in items],
        )
        out = main.crear_remito_entrega(payload)
        self.assertNotIn("error", out, out)
        if autorizar:
            res = main.autorizar_remito_entrega(out["id"], main.AlmacenAutorizacionREPayload(autorizado_por="test"))
            self.assertNotIn("error", res, res)
        return out

    def test_totales_coinciden_con_remitos_de_salida(self):
        a, b, c = self.ids
        self._salida("101 - Obra Test Norte", [(a, 5), (b, 3)])
        self._salida("101 - Obra Test Norte", [(c, 2)])
        self._salida("  202 - Obra Test Sur  ", [(a, 4)])
        self._salida("", [(b, 7)])
        self._salida("101 - Obra Test Norte", [(a, 50)], autorizar=False)  # pendiente: no es salida aun

        dash = main.dashboard_almacen_v2(top_n=10)
        self.assertNotIn("consumo_por_proyecto", dash)
        obtenido = {f["centro_costos"]: float(f["total"]) for f in dash["consumo_por_centro_costos"]}

        with main.get_sqlite_connection() as conn:
            esperado = self._totales_por_cc(conn)
            total_salidas = conn.execute(
                "SELECT COALESCE(SUM(cantidad), 0) FROM movimientos_stock WHERE UPPER(tipo) = 'SALIDA'"
            ).fetchone()[0]

        self.assertEqual(obtenido, esperado)
        self.assertAlmostEqual(sum(obtenido.values()), float(total_salidas))
        # Deltas de esta prueba sobre lo que ya hubiera en la base copiada.
        self.assertEqual(obtenido["101 - Obra Test Norte"] - self.base.get("101 - Obra Test Norte", 0), 10)
        self.assertEqual(obtenido["202 - Obra Test Sur"] - self.base.get("202 - Obra Test Sur", 0), 4)
        self.assertEqual(obtenido[SIN_CC] - self.base.get(SIN_CC, 0), 7)

        fila_norte = next(f for f in dash["consumo_por_centro_costos"] if f["centro_costos"] == "101 - Obra Test Norte")
        self.assertGreaterEqual(fila_norte["remitos"], 2)
        totales = [f["total"] for f in dash["consumo_por_centro_costos"]]
        self.assertEqual(totales, sorted(totales, reverse=True))


if __name__ == "__main__":
    unittest.main()
