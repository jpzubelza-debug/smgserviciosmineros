import asyncio
import unittest
from unittest.mock import MagicMock, patch

import main


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
             patch.object(main, "generar_pdf_remito") as generar_pdf, \
             patch.object(main, "FileResponse", return_value="pdf-response"):
            response = asyncio.run(main.compras_emitir(DummyRequest()))

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


if __name__ == "__main__":
    unittest.main()
