import asyncio
import unittest
from unittest.mock import MagicMock, patch

import os
import tempfile

# Importar main ejecuta init_sqlite(): sin esto migraría el dashboard.db de la carpeta donde se corre la prueba.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="smg_test_"))

import main  # noqa: E402


class ComprasRouteTests(unittest.TestCase):
    def test_ruta_compras_devuelve_html(self):
        with patch.object(main, "_requiere_modulo", return_value={}), patch.object(main, "_leer_html", return_value="compras") as leer_html:
            response = main.compras_view(None)

        self.assertEqual(response, "compras")
        leer_html.assert_called_once_with(main.COMPRAS_PATH)

    def test_emitir_pdf_muestra_nombres_y_titulo_correctos(self):
        class DummyRequest:
            async def json(self):
                return {
                    "id_solicitante": 368,
                    "destino_compra": "Proyecto Norte",
                    "prioridad_id": 2,
                    "observaciones": "Compra urgente",
                    "items": [{"detalle": "Aceite", "cantidad": 2, "unidad": "L", "centroCosto": 10, "ci": "CI-1", "motivo": "Mantención"}],
                }

        class FakeResult:
            def __init__(self, rows=None):
                self._rows = list(rows or [])

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def fetchall(self):
                return list(self._rows)

        class FakeCursor:
            def __init__(self, lastrowid=7):
                self.lastrowid = lastrowid

        def fake_execute(query, params=None):
            query_text = str(query).strip().upper()
            if query_text.startswith("INSERT"):
                return FakeCursor()
            if "FROM SOLICITUD_COMPRA SC" in query_text:
                return FakeResult([{"solicitante": "Juan Pérez", "prioridad": "Media", "destino_compra": "Proyecto Norte"}])
            if "SELECT NOMBRE FROM PERSONAL" in query_text:
                return FakeResult([("Juan Pérez",)])
            if "SELECT NOMBRE FROM PRIORIDADES_COMPRA" in query_text:
                return FakeResult([("Media",)])
            if "SELECT NOMBRE FROM PROYECTOS" in query_text:
                return FakeResult([("Proyecto Norte",)])
            return FakeResult()

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.__exit__.return_value = None
        fake_conn.execute.side_effect = fake_execute
        fake_conn.commit = MagicMock()

        with patch.object(main, "_requiere_accion_compras", return_value={"usuario": "jlopez", "nombre_apellido": "Juan López"}), \
             patch.object(main, "_usuario_autenticado", return_value={"usuario": "jlopez", "nombre_apellido": "Juan López"}), \
             patch.object(main, "get_sqlite_connection", return_value=fake_conn), \
             patch.object(main, "_ensure_prioridades_y_estados_compras"), \
             patch.object(main, "_ensure_detalle_compra_cc_nullable"), \
             patch.object(main, "generar_numero_solicitud", return_value="SC-00001"), \
             patch.object(main, "_obtener_proyecto_id_por_nombre", return_value=5), \
             patch.object(main.compras_departamentos, "destino_canonico", return_value="Proyecto Norte") as canonico, \
             patch.object(main, "generar_pdf_remito") as generar_pdf, \
             patch.object(main, "FileResponse", return_value="pdf-response"):
            response = asyncio.run(main.compras_emitir(DummyRequest()))

        canonico.assert_called_once()

        self.assertEqual(response, "pdf-response")
        self.assertEqual(generar_pdf.call_args.args[1], "SOLICITUD DE COMPRAS")
        cabecera = generar_pdf.call_args.args[2]
        self.assertIn(("Solicitante", "Juan Pérez"), cabecera)
        self.assertIn(("Prioridad", "Media"), cabecera)
        bloque_envio = generar_pdf.call_args.kwargs["bloque_envio"]
        self.assertIn(("Destino de compra", "Proyecto Norte"), bloque_envio)
        self.assertEqual(generar_pdf.call_args.kwargs["usuario_pie"], "Usuario jlopez")


    def test_dashboard_compras_con_filtros(self):
        class DummyRequest:
            pass

        class FakeResult:
            def __init__(self, rows=None):
                self._rows = list(rows or [])

            def fetchone(self):
                return self._rows[0] if self._rows else {"total": 0}

            def fetchall(self):
                return list(self._rows)

        executed_queries = []
        def fake_execute(query, params=None):
            executed_queries.append((str(query), params))
            if "solicitudes_plazo" in str(query) or "sc.id, sc.numero_solicitud" in str(query):
                return FakeResult([])
            if "GROUP BY" in str(query) or "ORDER BY sc.id DESC, d.id" in str(query):
                return FakeResult([])
            return FakeResult([{"total": 5}])

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.__exit__.return_value = None
        fake_conn.execute.side_effect = fake_execute

        with patch.object(main, "_requiere_accion_compras_por_tipo", return_value={"usuario": "admin"}), \
             patch.object(main, "get_sqlite_connection", return_value=fake_conn), \
             patch.object(main, "_ensure_orden_compra_solicitudes"):
            data = main.compras_dashboard(DummyRequest(), desde="2026-01-01", hasta="2026-12-31", centro_costo_id="10")

        self.assertIn("solicitudes", data)
        self.assertIn("ordenes", data)
        # Comprobar que los parámetros se incluyeron en las queries
        params_usados = [params for query, params in executed_queries if params]
        self.assertTrue(any("2026-01-01" in p for p in params_usados))
        self.assertTrue(any(10 in p for p in params_usados))

    def test_dashboard_compras_excel(self):
        class DummyRequest:
            pass

        class FakeResult:
            def __init__(self, rows=None):
                self._rows = list(rows or [])

            def fetchall(self):
                return list(self._rows)

        fake_rows = [{
            "numero_solicitud": "SC-0001",
            "fecha_solicitud": "2026-05-10",
            "solicitante": "Carlos",
            "centro_costo": "10 - Minera",
            "descripcion_articulo": "Filtro",
            "cantidad": 2,
            "unidad_medida": "UN",
            "estado_item": "Gestionado",
            "estado_solicitud": "Completada",
            "prioridad": "Alta",
            "numero_oc": "OC-0001",
            "fecha_oc": "2026-05-12",
            "proveedor": "Filtros SA",
            "precio_unitario": 1500.0,
            "total": 3000.0
        }]

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.__exit__.return_value = None
        fake_conn.execute.return_value = FakeResult(fake_rows)

        estados = main.compras_departamentos.ESTADOS_ITEM
        por_depto = {
            "estados": estados,
            "departamentos": [{"clave": "cc:10", "departamento": "10 Minera", "total": 1,
                               "estados": {e: int(e == "Gestionado") for e in estados}}],
            "total": {"total": 1, "estados": {e: int(e == "Gestionado") for e in estados}},
        }

        with patch.object(main, "_requiere_accion_compras_por_tipo", return_value={"usuario": "admin"}), \
             patch.object(main, "get_sqlite_connection", return_value=fake_conn), \
             patch.object(main, "_ensure_orden_compra_solicitudes"), \
             patch.object(main.compras_departamentos, "items_por_departamento", return_value=por_depto) as agregacion:
            response = main.compras_dashboard_excel(DummyRequest(), desde="2026-01-01", hasta="2026-12-31")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(len(response.body) > 0)
        # La hoja por departamento usa los mismos filtros que el resto del Excel.
        self.assertEqual(agregacion.call_args.args[1:], ("2026-01-01", "2026-12-31", None))
        from openpyxl import load_workbook
        import io
        libro = load_workbook(io.BytesIO(response.body))
        self.assertEqual(libro.sheetnames, ["Indicadores Compras", "Ítems por departamento"])
        self.assertEqual(libro["Ítems por departamento"]["A3"].value, "Total general")


class ComprasProveedoresYPlazosTests(unittest.TestCase):
    """CUIT de proveedores y vencimiento de solicitudes (listado de /compras)."""

    def _conn(self, proveedores):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute('CREATE TABLE proveedores (id INTEGER PRIMARY KEY, "NOM_PROVEE" TEXT, "CUIT" TEXT)')
        conn.executemany('INSERT INTO proveedores (id, "NOM_PROVEE", "CUIT") VALUES (?, ?, ?)', proveedores)
        return conn

    @staticmethod
    def _valores(cuit):
        return ("Proveedor", "", "", "", "", "", cuit, "", "")

    def test_cuit_digito_verificador(self):
        self.assertTrue(main._cuit_valido(main._cuit_digitos("30-71999888-3")))
        self.assertTrue(main._cuit_valido("20299427421"))
        self.assertFalse(main._cuit_valido("30123456780"))
        self.assertFalse(main._cuit_valido("3012345678"))

    def test_alta_valida_formato_y_duplicado(self):
        conn = self._conn([(1, "EXISTENTE SA", "20-29942742-1")])
        valores = main._validar_cuit_proveedor(conn, self._valores("30719998883"))
        self.assertEqual(valores[6], "30-71999888-3")
        with self.assertRaises(main.HTTPException) as e:
            main._validar_cuit_proveedor(conn, self._valores("30-12345678-0"))
        self.assertEqual(e.exception.status_code, 400)
        with self.assertRaises(main.HTTPException) as e:
            main._validar_cuit_proveedor(conn, self._valores("20299427421"))
        self.assertEqual(e.exception.status_code, 409)
        self.assertEqual(main._validar_cuit_proveedor(conn, self._valores(""))[6], "")

    def test_edicion_no_bloquea_cuit_historico(self):
        conn = self._conn([(1, "VIEJO SRL", "30-72496198-3"), (2, "OTRO SRL", "20-29942742-1")])
        # El CUIT inválido que ya tenía se conserva al editar otros datos
        self.assertEqual(main._validar_cuit_proveedor(conn, self._valores("30-72496198-3"), 1)[6], "30-72496198-3")
        # Si lo cambia, se valida (y no puede tomar el de otro proveedor)
        with self.assertRaises(main.HTTPException):
            main._validar_cuit_proveedor(conn, self._valores("20-29942742-1"), 1)

    def test_vencimiento_y_fuera_de_plazo(self):
        vencida = main._resumen_plazo_solicitud(
            {"prioridad": "Urgente", "fecha_solicitud": "2026-01-01"}, {"total": 2, "sin_resolver": 1, "por_gestionar": 0})
        self.assertEqual(vencida["vencimiento"], "2026-01-08")
        self.assertTrue(vencida["fuera_plazo"])
        self.assertEqual(vencida["cantidad_items"], 2)
        resuelta = main._resumen_plazo_solicitud(
            {"prioridad": "Baja", "fecha_solicitud": "2026-01-01"}, {"total": 1, "gestionados": 1})
        self.assertEqual(resuelta["vencimiento"], "2026-01-31")
        self.assertFalse(resuelta["fuera_plazo"])
        sin_prioridad = main._resumen_plazo_solicitud({"prioridad": "", "fecha_solicitud": "2026-01-01"}, {})
        self.assertIsNone(sin_prioridad["vencimiento"])
        self.assertEqual(sin_prioridad["cantidad_items"], 0)


if __name__ == "__main__":
    unittest.main()
