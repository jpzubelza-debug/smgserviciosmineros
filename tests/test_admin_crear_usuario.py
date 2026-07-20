import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class FakeCursor:
    def __init__(self, *, lastrowid=None, result=None):
        self.lastrowid = lastrowid
        self._result = result

    def execute(self, sql, params=None):
        if "last_insert_rowid" in sql:
            raise AssertionError("No debe usarse SELECT last_insert_rowid() en PostgreSQL")
        return self

    def fetchone(self):
        return [self._result]

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.last_inserted_id = 42

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        if sql.strip().startswith("INSERT INTO usuarios"):
            return FakeCursor(lastrowid=self.last_inserted_id)
        if "SELECT id FROM usuarios" in sql:
            return FakeCursor(result=None)
        return FakeCursor(result=None)

    def commit(self):
        pass


class AdminCrearUsuarioTests(unittest.TestCase):
    def test_crear_usuario_usa_lastrowid_del_cursor(self):
        fake_conn = FakeConnection()

        with patch.object(main, "get_sqlite_connection", return_value=fake_conn):
            payload = SimpleNamespace(
                nombre_apellido="Test User",
                dni="",
                legajo="",
                correo="testuser@example.com",
                password="123456",
                estado="ACTIVO",
                tipo_usuario="CONSULTOR",
                modulos=["administracion"],
                paneles={"administracion": []},
                acciones={},
                roles=[],
            )
            response = main.admin_crear_usuario(payload)

        self.assertEqual(response["ok"], True)


if __name__ == "__main__":
    unittest.main()
