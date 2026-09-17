# smgserviciosmineros

## Migracion de JSON a SQLite

## Notificaciones de compras

Para enviar los avisos de nuevas solicitudes de compra, configure estas variables de entorno en el servidor (no las guarde en el repositorio):

- `SMTP_USER`: cuenta emisora.
- `SMTP_PASSWORD`: contraseña de aplicación de la cuenta.
- `SMTP_FROM`: dirección que figura como remitente (normalmente la misma cuenta).

Gmail usa por defecto `smtp.gmail.com` por SSL en el puerto 465. Desde Administración se pueden asignar uno o varios responsables a Compras, Logística o Almacén. Las solicitudes de Compra y los remitos de Ingreso/Entrega adjuntan su PDF; las Solicitudes de Viaje envían su resumen. Los responsables reciben las solicitudes y el emisor recibe una copia en Compras y Logística.

## Plantillas de emails

En Administración > Plantillas de Emails se pueden editar los asuntos y cuerpos de las notificaciones de viaje, compra y remitos. Las variables admitidas se muestran junto al editor y se validan al guardar; una plantilla inactiva conserva el envio usando el texto original del sistema como respaldo.

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
