import io
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compras_departamentos as cd  # noqa: E402

ESQUEMA = """
CREATE TABLE centros_costos (id INTEGER PRIMARY KEY, nombre TEXT);
CREATE TABLE proyectos (id INTEGER PRIMARY KEY, codigo TEXT, nombre TEXT);
CREATE TABLE solicitud_compra (id INTEGER PRIMARY KEY, numero_solicitud TEXT, fecha_solicitud TEXT, id_proyecto INTEGER);
CREATE TABLE solicitud_compra_detalle (id INTEGER PRIMARY KEY, id_solicitud INTEGER, id_centro_costo INTEGER, estado_aprobacion TEXT);
CREATE TABLE OrdenCompraDetalleSolicitud (id INTEGER PRIMARY KEY, solicitud_compra_detalle_id INTEGER, orden_compra_detalle_id INTEGER);
"""

# Consulta de la tarjeta "Ítems por estado" (main.compras_dashboard), para comparar.
SQL_POR_ESTADO = f"""
SELECT {cd.SQL_ESTADO_ITEM} AS estado, COUNT(*) AS total
FROM solicitud_compra_detalle d
INNER JOIN solicitud_compra sc ON sc.id = d.id_solicitud
LEFT JOIN OrdenCompraDetalleSolicitud vinculo ON vinculo.solicitud_compra_detalle_id = d.id
{{where}}
GROUP BY estado
"""


def crear_base():
    conn = sqlite3.connect(":memory:")
    conn.executescript(ESQUEMA)
    conn.executemany("INSERT INTO centros_costos VALUES (?, ?)", [
        (1, "ADMINISTRACION"), (27, "TALLER ALTO LA TORRE"), (6, "ALTO LA TORRE"), (10, "OBRA CIVIL"),
    ])
    destinos = [
        "27", "TALLER ALTO LA TORRE", "27 TALLER ALTO LA TORRE",   # -> 27
        "1", "Administración", "1 ADMINISTRACION",                # -> 1
        "107", "107 CONSTRUCCIÓN DE POZAS EXAR, UTE SMG-AB-BSD",  # -> 107 (fuera de la maestra)
        "Exar", "Pirquitas", "",                                   # tal cual / sin destino
    ]
    conn.executemany("INSERT INTO proyectos (id, codigo, nombre) VALUES (?, ?, ?)",
                     [(i + 1, f"P{i}", n) for i, n in enumerate(destinos)])
    estados = ["Pendiente", "APROBADA", "RECHAZADA", "GESTIONADO", "Pendiente"]
    det_id = 0
    for sol_id in range(1, 41):
        proyecto = ((sol_id - 1) % (len(destinos) + 1)) + 1   # el último índice no existe -> sin destino
        fecha = f"2026-{(sol_id % 12) + 1:02d}-15"
        conn.execute("INSERT INTO solicitud_compra VALUES (?, ?, ?, ?)",
                     (sol_id, f"SC-{sol_id:05d}", fecha, proyecto if sol_id % 7 else None))
        for k in range(sol_id % 4 + 1):
            det_id += 1
            conn.execute("INSERT INTO solicitud_compra_detalle VALUES (?, ?, ?, ?)",
                         (det_id, sol_id, 10 if det_id % 3 == 0 else 27, estados[det_id % len(estados)]))
            if det_id % 5 == 0:
                conn.execute("INSERT INTO OrdenCompraDetalleSolicitud VALUES (NULL, ?, 1)", (det_id,))
    return conn


class ResolverDepartamentoTests(unittest.TestCase):
    def setUp(self):
        self.conn = crear_base()
        self.addCleanup(self.conn.close)
        self.por_id, self.por_nombre = cd.cargar_centros_costos(self.conn)

    def resolver(self, texto):
        return cd.resolver_departamento(texto, self.por_id, self.por_nombre)

    def test_codigo_nombre_y_ambos_se_agrupan(self):
        for texto in ["27", "TALLER ALTO LA TORRE", "27 TALLER ALTO LA TORRE", " 27 - taller alto la torre "]:
            self.assertEqual(self.resolver(texto)[:2], ("cc:27", "27 TALLER ALTO LA TORRE"), texto)
        for texto in ["1", "ADMINISTRACION", "Administración", "1 ADMINISTRACION"]:
            self.assertEqual(self.resolver(texto)[:2], ("cc:1", "1 ADMINISTRACION"), texto)

    def test_codigo_fuera_de_maestra_agrupa_por_codigo(self):
        self.assertEqual(self.resolver("107")[0], "cc:107")
        self.assertEqual(self.resolver("107 CONSTRUCCIÓN DE POZAS EXAR, UTE SMG-AB-BSD")[0], "cc:107")
        self.assertFalse(self.resolver("107")[3])

    def test_sin_codigo_ni_coincidencia_queda_tal_cual(self):
        self.assertEqual(self.resolver("Exar")[1], "Exar")
        self.assertTrue(self.resolver("Exar")[0].startswith("txt:"))
        self.assertEqual(self.resolver("4X4 camioneta")[1], "4X4 camioneta")  # "4X4" no es un código

    def test_vacio_es_sin_destino(self):
        for texto in [None, "", "   "]:
            self.assertEqual(self.resolver(texto)[:2], (cd.CLAVE_SIN_DESTINO, cd.ETIQUETA_SIN_DESTINO))

    def test_destino_canonico_solo_maestra(self):
        self.assertEqual(cd.destino_canonico(self.conn, "27"), "27 TALLER ALTO LA TORRE")
        self.assertEqual(cd.destino_canonico(self.conn, "Taller Alto La Torre"), "27 TALLER ALTO LA TORRE")
        self.assertIsNone(cd.destino_canonico(self.conn, "107"))
        self.assertIsNone(cd.destino_canonico(self.conn, "Exar"))
        self.assertIsNone(cd.destino_canonico(self.conn, ""))


class ItemsPorDepartamentoTests(unittest.TestCase):
    def setUp(self):
        self.conn = crear_base()
        self.addCleanup(self.conn.close)

    def por_estado(self, desde=None, hasta=None, cc=None):
        where, params = cd.filtros_items(desde, hasta, cc)
        sql = SQL_POR_ESTADO.format(where=("WHERE " + " AND ".join(where)) if where else "")
        return {estado: total for estado, total in self.conn.execute(sql, params).fetchall()}

    def assert_coincide(self, desde=None, hasta=None, cc=None):
        datos = cd.items_por_departamento(self.conn, desde, hasta, cc)
        esperado = self.por_estado(desde, hasta, cc)
        for estado in cd.ESTADOS_ITEM:
            self.assertEqual(datos["total"]["estados"][estado], esperado.get(estado, 0), estado)
        self.assertEqual(datos["total"]["total"], sum(esperado.values()))
        self.assertEqual(sum(d["total"] for d in datos["departamentos"]), datos["total"]["total"])
        for d in datos["departamentos"]:
            self.assertEqual(sum(d["estados"].values()), d["total"])
        return datos

    def test_sin_filtros_coincide_y_agrupa(self):
        datos = self.assert_coincide()
        etiquetas = [d["departamento"] for d in datos["departamentos"]]
        self.assertIn("27 TALLER ALTO LA TORRE", etiquetas)
        self.assertIn("1 ADMINISTRACION", etiquetas)
        self.assertIn("107 CONSTRUCCIÓN DE POZAS EXAR, UTE SMG-AB-BSD", etiquetas)  # texto más completo
        self.assertIn("Exar", etiquetas)
        self.assertIn(cd.ETIQUETA_SIN_DESTINO, etiquetas)
        self.assertEqual(len(etiquetas), len(set(etiquetas)))
        totales = [d["total"] for d in datos["departamentos"]]
        self.assertEqual(totales, sorted(totales, reverse=True))

    def test_con_filtros_coincide(self):
        self.assert_coincide(desde="2026-03-01")
        self.assert_coincide(hasta="2026-06-30")
        self.assert_coincide(desde="2026-03-01", hasta="2026-09-30")
        self.assert_coincide(cc=10)
        self.assert_coincide(desde="2026-04-01", hasta="2026-10-31", cc=27)
        datos = self.assert_coincide(cc=999)
        self.assertEqual(datos["departamentos"], [])

    def test_condicion_departamento_trae_los_mismos_items(self):
        datos = cd.items_por_departamento(self.conn)
        for d in datos["departamentos"]:
            cond, params = cd.condicion_departamento(self.conn, d["clave"])
            n = self.conn.execute(
                f"""SELECT COUNT(*) FROM solicitud_compra_detalle d
                    INNER JOIN solicitud_compra sc ON sc.id = d.id_solicitud
                    LEFT JOIN proyectos pr ON pr.id = sc.id_proyecto
                    LEFT JOIN OrdenCompraDetalleSolicitud vinculo ON vinculo.solicitud_compra_detalle_id = d.id
                    WHERE {cond}""", params).fetchone()[0]
            self.assertEqual(n, d["total"], d["departamento"])

    def test_hoja_excel(self):
        from openpyxl import Workbook, load_workbook
        wb = Workbook()
        datos = cd.items_por_departamento(self.conn)
        cd.escribir_hoja_excel(wb, datos)
        buf = io.BytesIO()
        wb.save(buf)
        ws = load_workbook(io.BytesIO(buf.getvalue()))["Ítems por departamento"]
        self.assertEqual([c.value for c in ws[1]][:4], ["Departamento", "Total ítems", "Gestionado", "% Gestionado"])
        ultima = [c.value for c in ws[ws.max_row]]
        self.assertEqual(ultima[0], "Total general")
        self.assertEqual(ultima[1], datos["total"]["total"])
        self.assertEqual(ws.max_row, len(datos["departamentos"]) + 2)
        fila = [c.value for c in ws[2]]
        self.assertAlmostEqual(fila[3], fila[2] / fila[1])


if __name__ == "__main__":
    unittest.main()
