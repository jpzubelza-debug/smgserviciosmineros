import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

import main


class ViajesEstadoTests(unittest.TestCase):
    def _create_mock_request(self):
        scope = {
            "type": "http",
            "method": "PUT",
            "path": "/estado/1",
            "headers": [],
        }
        return Request(scope)

    def _setup_mock_db(self, viaje_id=1, estado_inicial="PENDIENTE"):
        viaje_data = {
            "id": viaje_id,
            "solicitante": "Juan Perez",
            "area": "Operaciones",
            "estado": estado_inicial,
        }
        mock_row = {"raw_json": json.dumps(viaje_data)}

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.__exit__.return_value = None

        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = mock_row
        fake_conn.execute.return_value = fake_cursor

        return fake_conn

    def test_admin_puede_cambiar_a_cualquier_estado_y_cambiar_veredicto(self):
        req = self._create_mock_request()
        fake_conn = self._setup_mock_db(viaje_id=1, estado_inicial="RECHAZADO")

        perfil_admin = {
            "usuario": "admin@example.com",
            "tipo_usuario": "ADMINISTRADOR",
            "es_desarrollador": True,
        }

        with patch.object(main, "_usuario_autenticado", return_value=perfil_admin), \
             patch.object(main, "get_sqlite_connection", return_value=fake_conn), \
             patch.object(main, "guardar_viaje_sql") as mock_guardar:
            res = main.cambiar_estado(1, "APROBADO", req)

        self.assertEqual(res["ok"], True)
        self.assertIn("APROBADO", res["mensaje"])
        mock_guardar.assert_called_once()
        guardado = mock_guardar.call_args[0][1]
        self.assertEqual(guardado["estado"], "APROBADO")

    def test_usuario_con_cambiar_veredicto_puede_revertir_o_cambiar_estado(self):
        req = self._create_mock_request()
        fake_conn = self._setup_mock_db(viaje_id=2, estado_inicial="ANULADO")

        perfil = {
            "usuario": "user@example.com",
            "tipo_usuario": "CONSULTOR",
            "es_desarrollador": False,
            "paneles": {"logistica": ["dashboard"]},
            "acciones": {"logistica": {"dashboard": ["ver", "cambiar_veredicto"]}},
        }

        with patch.object(main, "_usuario_autenticado", return_value=perfil), \
             patch.object(main, "get_sqlite_connection", return_value=fake_conn), \
             patch.object(main, "guardar_viaje_sql") as mock_guardar:
            res = main.cambiar_estado(2, "APROBADO", req)

        self.assertEqual(res["ok"], True)
        guardado = mock_guardar.call_args[0][1]
        self.assertEqual(guardado["estado"], "APROBADO")

    def test_usuario_sin_cambiar_veredicto_no_puede_cambiar_veredicto_resuelto(self):
        req = self._create_mock_request()
        fake_conn = self._setup_mock_db(viaje_id=3, estado_inicial="APROBADO")

        perfil = {
            "usuario": "user@example.com",
            "tipo_usuario": "CONSULTOR",
            "es_desarrollador": False,
            "paneles": {"logistica": ["dashboard"]},
            "acciones": {"logistica": {"dashboard": ["ver", "aprobar_solicitud"]}},
        }

        with patch.object(main, "_usuario_autenticado", return_value=perfil), \
             patch.object(main, "get_sqlite_connection", return_value=fake_conn):
            with self.assertRaises(HTTPException) as ctx:
                main.cambiar_estado(3, "RECHAZADO", req)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_usuario_con_anular_puede_anular_pendiente(self):
        req = self._create_mock_request()
        fake_conn = self._setup_mock_db(viaje_id=4, estado_inicial="PENDIENTE")

        perfil = {
            "usuario": "user@example.com",
            "tipo_usuario": "CONSULTOR",
            "es_desarrollador": False,
            "paneles": {"logistica": ["dashboard"]},
            "acciones": {"logistica": {"dashboard": ["ver", "anular_solicitud"]}},
        }

        with patch.object(main, "_usuario_autenticado", return_value=perfil), \
             patch.object(main, "get_sqlite_connection", return_value=fake_conn), \
             patch.object(main, "guardar_viaje_sql") as mock_guardar:
            res = main.cambiar_estado(4, "ANULADO", req)

        self.assertEqual(res["ok"], True)
        guardado = mock_guardar.call_args[0][1]
        self.assertEqual(guardado["estado"], "ANULADO")

    def test_estado_invalido_devuelve_400(self):
        req = self._create_mock_request()
        perfil = {"usuario": "admin@example.com", "es_desarrollador": True}

        with patch.object(main, "_usuario_autenticado", return_value=perfil):
            with self.assertRaises(HTTPException) as ctx:
                main.cambiar_estado(5, "ESTADO_INVENTADO", req)
            self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
