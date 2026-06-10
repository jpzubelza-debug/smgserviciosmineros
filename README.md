# smgserviciosmineros

## Migracion de JSON a SQLite

Se agregaron dos archivos para arrancar la migracion sin romper el backend actual:

- `schema.sql`: define tablas relacionales e indices.
- `migrate_json_to_sqlite.py`: migra datos desde `db.json`, `ordenes_salida.json`, `vehiculos.json`, `choferes.json` y `personal.json`.

### Ejecutar migracion

```bash
python migrate_json_to_sqlite.py --reset
```

Opcionalmente se puede cambiar el archivo de salida:

```bash
python migrate_json_to_sqlite.py --db dashboard_v2.db --reset
```

### Resultado

Se genera `dashboard.db` (o el nombre indicado en `--db`) con tablas:

- `viajes`
- `recursos_viaje`
- `recurso_acompanantes`
- `ordenes_salida`
- `vehiculos`
- `choferes`
- `personal`

Cada tabla guarda tambien una columna `raw_json` para trazabilidad y rollback funcional.

### Siguiente paso recomendado

Migrar endpoints de a bloques:

1. `vehiculos`, `choferes`, `personal`
2. `viajes` y `estado`
3. `recursos` y `ordenes`

Mantener una bandera de compatibilidad para poder volver temporalmente a JSON si aparece un incidente.