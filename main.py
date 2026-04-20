
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from dotenv import load_dotenv
import json
import os

BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))
api_key = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=api_key) if api_key else None
DB_PATH = os.path.join(BASE_DIR, "db.json")
DASHBOARD_PATH = os.path.join(BASE_DIR, "dashboard.html")
MENU_PRINCIPAL_PATH = os.path.join(BASE_DIR, "menu_principal.html")
FORM_VIAJE_PATH = os.path.join(BASE_DIR, "form_viaje.html")
FORM_RECURSOS_PATH = os.path.join(BASE_DIR, "form_recursos.html")
PERSONAL_FORM_PATH = os.path.join(BASE_DIR, "personal.html")
PRINT_VIAJE_PATH = os.path.join(BASE_DIR, "print_viaje.html")
ORDENES_VIEW_PATH = os.path.join(BASE_DIR, "ordenes_view.html")
VEHICULOS_PATH = os.path.join(BASE_DIR, "vehiculos.json")
CHOFERES_PATH = os.path.join(BASE_DIR, "choferes.json")
PERSONAL_PATH = os.path.join(BASE_DIR, "personal.json")
ORDENES_PATH = os.path.join(BASE_DIR, "ordenes_salida.json")
VEHICULOS_HTML_PATH = os.path.join(BASE_DIR, "vehiculos.html")
DOC_LOG_VIAJES_DIR = os.path.join(BASE_DIR, "Doc_Log_Viajes")

os.makedirs(DOC_LOG_VIAJES_DIR, exist_ok=True)

app = FastAPI()
app.mount("/Imagenes", StaticFiles(directory=os.path.join(BASE_DIR, "Imagenes")), name="Imagenes")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    with open(DB_PATH, "r") as f:
        db = json.load(f)
except:
    db = []

def guardar_db():
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=4)


def cargar_json(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f, indent=4)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def asegurar_lista(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def normalizar_texto(texto):
    if texto is None:
        return ""
    tabla = str.maketrans("áéíóúÁÉÍÓÚñÑ", "aeiouAEIOUnN")
    return str(texto).strip().translate(tabla).lower()


def obtener_campo_habilitacion(destino):
    destino_norm = normalizar_texto(destino)
    mapa = {
        "pirquitas": "habilitacion_pirquitas",
        "exar": "habilitacion_exar",
        "sdj": "habilitacion_sdj",
        "rincon": "habilitacion_rincon",
        "arli": "habilitacion_arli",
    }
    for clave, campo in mapa.items():
        if clave in destino_norm:
            return campo
    return None


def esta_habilitado(persona, campo_habilitacion):
    if campo_habilitacion is None:
        return True
    valor = str(persona.get(campo_habilitacion, "")).strip().upper()
    return valor in {"SI", "S", "OK", "X", "1", "TRUE", "HABILITADO"}


def guardar_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


async def guardar_adjunto_logistico(upload: UploadFile | None, nro_orden: str, nombre_base: str):
    if upload is None or not upload.filename:
        return None

    ext = os.path.splitext(upload.filename)[1]
    nombre_archivo = f"{nro_orden}-{nombre_base}{ext}"
    ruta_archivo = os.path.join(DOC_LOG_VIAJES_DIR, nombre_archivo)

    contenido = await upload.read()
    with open(ruta_archivo, "wb") as f:
        f.write(contenido)

    return nombre_archivo


def generar_numero_orden(ordenes):
    max_num = 0
    for orden in ordenes:
        nro = str(orden.get("nro_orden", ""))
        if nro.startswith("OS-"):
            try:
                valor = int(nro.split("-")[1])
                max_num = max(max_num, valor)
            except (ValueError, IndexError):
                continue
    return f"OS-{max_num + 1:06d}"

contador_id = len(db) + 1

class Viaje(BaseModel):
    solicitante: str
    area: str
    origen: str
    destino: str
    motivo: str
    fecha_salida: str
    fecha_regreso: str

@app.post("/viajes")
def crear_viaje(viaje: Viaje):
    global contador_id
    nuevo = viaje.dict()
    nuevo["id"] = contador_id
    nuevo["estado"] = "PENDIENTE"
    nuevo["fecha_creacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db.append(nuevo)
    guardar_db()
    contador_id += 1

    return {"mensaje": "Viaje creado", "id": nuevo["id"]}

@app.get("/viajes")
def listar_viajes():
    return db

class Recursos(BaseModel):
    id_viaje: int
    fecha: str = ""
    centro_costo: str = ""
    datos_solicitante: str = ""
    area_solicitante: str = ""
    partida: str = ""
    destino: str = ""
    motivo_viaje: str = ""
    fecha_salida_viaje: str = ""
    fecha_regreso_viaje: str = ""
    hora_salida: str = ""
    hora_regreso: str = ""
    duracion_jornadas: str = ""
    itinerario: str = ""
    rutas: str = ""
    paradas: str = ""
    chofer: str
    chofer_viatico: float = 0
    vehiculo: str
    vehiculo_fuera_flota: bool = False
    acompanantes: list[str] = []
    acompanantes_con_viatico: list[dict] = []
    viaticos: float = 0
    medio_pago: str = "Caja"
    alojamiento: str = "NO"
    otros_gastos: float = 0
    verificado_administracion: str = "NO"
    comprobacion_operaciones_logistica: dict = {}


@app.post("/analisis")
def analisis(data: dict):
    viajes = float(data.get("viajes", 0) or 0)
    aprobados = float(data.get("aprobados", 0) or 0)
    ordenes = float(data.get("ordenes", 0) or 0)
    flota = float(data.get("flota", 0) or 0)

    def conclusion_local():
        texto = ""
        if flota < 60:
            texto += "Baja disponibilidad de flota. "
        if aprobados < viajes:
            texto += "Hay solicitudes pendientes de aprobación. "
        if ordenes < aprobados:
            texto += "No todos los viajes aprobados tienen orden generada. "
        if flota >= 70:
            texto += "La flota se encuentra en buen estado operativo. "
        if texto == "":
            texto = "Operación estable sin alertas relevantes."
        return texto.strip()

    if client is None:
        print("USANDO FALLBACK LOCAL")
        return {
            "conclusion": conclusion_local()
        }

    prompt = f"""
    Analizar estos datos operativos de logistica:

    - Viajes totales: {viajes}
    - Viajes aprobados: {aprobados}
    - Ordenes generadas: {ordenes}
    - Flota operativa: {flota}%

    Generar una conclusion breve, clara y profesional.
    Incluir posibles riesgos operativos si los hay.
    """

    try:
        print("USANDO IA REAL")
        respuesta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Sos un analista logistico senior. Detecta problemas, riesgos y recomenda acciones concretas.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
        )

        conclusion = (respuesta.choices[0].message.content or "").strip()
        if not conclusion:
            conclusion = "No se pudo generar una conclusion automatica en este momento."

        return {"conclusion": conclusion}
    except Exception:
        print("USANDO FALLBACK LOCAL")
        return {
            "conclusion": conclusion_local()
        }


@app.post("/analisis_vehiculos")
def analisis_vehiculos(data: dict):
    total = float(data.get("total", 0) or 0)
    operativos = float(data.get("operativos", 0) or 0)
    no_operativos = float(data.get("no_operativos", 0) or 0)
    flota = float(data.get("flota", 0) or 0)
    proyecto_principal = str(data.get("proyecto_principal", "SIN DATO") or "SIN DATO")
    sector_principal = str(data.get("sector_principal", "SIN DATO") or "SIN DATO")
    pct_habilitaciones = float(data.get("pct_habilitaciones", 0) or 0)

    def conclusion_local_vehiculos():
        texto = ""
        if flota < 60:
            texto += "Baja disponibilidad de flota vehicular. "
        if no_operativos > operativos:
            texto += "Hay mas equipos no operativos que operativos. "
        if pct_habilitaciones < 50:
            texto += "El nivel de habilitaciones es bajo para la demanda operativa. "
        if not texto:
            texto = "Flota vehicular estable, con indicadores operativos controlados."
        return texto.strip()

    if client is None:
        return {"conclusion": conclusion_local_vehiculos()}

    prompt = f"""
    Analizar SOLO estos datos de flota vehicular. No mezclar con viajes, ordenes u otras interfaces.

    - Total de equipos: {total}
    - Equipos operativos: {operativos}
    - Equipos no operativos: {no_operativos}
    - Flota operativa: {flota}%
    - Proyecto con mayor concentracion: {proyecto_principal}
    - Sector con mayor concentracion: {sector_principal}
    - Nivel global de habilitaciones: {pct_habilitaciones}%

    Generar una conclusion breve, profesional y accionable (2 a 4 oraciones),
    enfocada unicamente en estado de vehiculos y habilitaciones.
    """

    try:
        respuesta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Sos un analista de flota vehicular y mantenimiento. Responde solo sobre vehiculos.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=140,
        )

        conclusion = (respuesta.choices[0].message.content or "").strip()
        if not conclusion:
            conclusion = conclusion_local_vehiculos()
        return {"conclusion": conclusion}
    except Exception:
        return {"conclusion": conclusion_local_vehiculos()}


@app.post("/analisis_personal")
def analisis_personal(data: dict):
    total = float(data.get("total", 0) or 0)
    con_alguna = float(data.get("con_alguna", 0) or 0)
    sin_habilitaciones = float(data.get("sin_habilitaciones", 0) or 0)
    pct_cobertura = float(data.get("pct_cobertura", 0) or 0)
    proyecto_principal = str(data.get("proyecto_principal", "SIN DATO") or "SIN DATO")

    def conclusion_local_personal():
        texto = ""
        if pct_cobertura < 50:
            texto += "Cobertura de habilitaciones baja para la dotacion actual. "
        if sin_habilitaciones > con_alguna:
            texto += "Predomina personal sin habilitaciones registradas. "
        if not texto:
            texto = "La dotacion presenta un nivel de habilitacion aceptable para la operacion."
        return texto.strip()

    if client is None:
        return {"conclusion": conclusion_local_personal()}

    prompt = f"""
    Analizar SOLO estos datos de gestion de personal y habilitaciones. No mezclar con viajes, ordenes ni flota vehicular.

    - Total de personal: {total}
    - Personal con al menos una habilitacion: {con_alguna}
    - Personal sin habilitaciones: {sin_habilitaciones}
    - Cobertura global de habilitaciones: {pct_cobertura}%
    - Proyecto con mayor dotacion habilitada: {proyecto_principal}

    Generar una conclusion breve, profesional y accionable (2 a 4 oraciones),
    enfocada exclusivamente en habilitaciones del personal.
    """

    try:
        respuesta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Sos un analista de RRHH operativo senior. Detecta riesgos de habilitaciones y recomienda acciones concretas.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=140,
        )

        conclusion = (respuesta.choices[0].message.content or "").strip()
        if not conclusion:
            conclusion = conclusion_local_personal()
        return {"conclusion": conclusion}
    except Exception:
        return {"conclusion": conclusion_local_personal()}

@app.post("/recursos")
def asignar_recursos(data: Recursos):
    ordenes = asegurar_lista(cargar_json(ORDENES_PATH))

    if any(o.get("id_viaje") == data.id_viaje for o in ordenes):
        return {"error": f"El viaje ID {data.id_viaje} ya tiene Orden de Salida generada"}

    vehiculo_ingresado = (data.vehiculo or "").strip()
    if not vehiculo_ingresado:
        return {"error": "Debe indicar un vehiculo"}

    vehiculos = asegurar_lista(cargar_json(VEHICULOS_PATH))
    codigo_vehiculo = vehiculo_ingresado.split(" - ")[0].strip()
    vehiculo_encontrado = next((v for v in vehiculos if str(v.get("codigo", "")).strip() == codigo_vehiculo), None)

    if vehiculo_encontrado is not None and str(vehiculo_encontrado.get("operativo", "NO")).strip().upper() != "SI":
        return {"error": f"El vehiculo {codigo_vehiculo} no esta operativo"}

    campo_habilitacion_vehiculo = obtener_campo_habilitacion(data.destino)
    if vehiculo_encontrado is not None and campo_habilitacion_vehiculo is not None:
        if not esta_habilitado(vehiculo_encontrado, campo_habilitacion_vehiculo):
            return {
                "error": (
                    f"El vehiculo {codigo_vehiculo} no esta habilitado "
                    f"para el destino {data.destino}"
                )
            }

    personal = asegurar_lista(cargar_json(PERSONAL_PATH))
    campo_habilitacion = obtener_campo_habilitacion(data.destino)

    if campo_habilitacion is not None:
        nombres_validar = [data.chofer] + (data.acompanantes or [])
        for nombre in nombres_validar:
            nombre_limpio = (nombre or "").strip()
            if not nombre_limpio:
                continue

            persona = next(
                (p for p in personal if normalizar_texto(p.get("nombre", "")) == normalizar_texto(nombre_limpio)),
                None,
            )
            if persona is None:
                return {"error": f"No se encontro en personal: {nombre_limpio}"}
            if not esta_habilitado(persona, campo_habilitacion):
                return {"error": f"{nombre_limpio} no esta habilitado para el destino {data.destino}"}

    # VALIDAR SI EL CHOFER YA ESTÁ OCUPADO
    for viaje in db:
        if "recursos" in viaje:
            if viaje["recursos"]["chofer"] == data.chofer and viaje["estado"] == "ASIGNADO":
                return {"error": f"El chofer {data.chofer} ya está asignado a otro viaje"}

    # ASIGNAR RECURSOS
    for viaje in db:
        if viaje["id"] == data.id_viaje:
            recursos_data = data.dict()
            recursos_data["vehiculo"] = vehiculo_ingresado
            recursos_data["vehiculo_fuera_flota"] = vehiculo_encontrado is None

            viaje["recursos"] = recursos_data
            viaje["estado"] = "ASIGNADO"
            viaje["orden_salida_generada"] = True

            nro_orden = generar_numero_orden(ordenes)
            viaje["nro_orden_salida"] = nro_orden
            orden = {
                "nro_orden": nro_orden,
                "fecha_orden": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "id_viaje": viaje["id"],
                "viaje": dict(viaje),
                "recursos": dict(recursos_data)
            }
            ordenes.append(orden)

            guardar_json(ORDENES_PATH, ordenes)
            guardar_db()
            return {"mensaje": "Recursos asignados", "nro_orden": nro_orden}

    return {"error": "Viaje no encontrado"}

@app.get("/", response_class=HTMLResponse)
def menu_principal():
    with open(MENU_PRINCIPAL_PATH, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/dashboard.html", response_class=HTMLResponse)
def dashboard_html():
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        return f.read()
    
@app.put("/estado/{id_viaje}")
def cambiar_estado(id_viaje: int, estado: str):
    for viaje in db:
        if viaje["id"] == id_viaje:
            viaje["estado"] = estado
            guardar_db()
            return {"mensaje": "Estado actualizado"}

    return {"error": "Viaje no encontrado"}


@app.get("/form", response_class=HTMLResponse)
def form_viaje():
    with open(FORM_VIAJE_PATH, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/recursos_form", response_class=HTMLResponse)
def form_recursos():
    with open(FORM_RECURSOS_PATH, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/ordenes")
def obtener_ordenes():
    return cargar_json(ORDENES_PATH)


@app.post("/ordenes/{nro_orden}/cierre")
async def guardar_cierre_logistico(
    nro_orden: str,
    payload: str = Form("{}"),
    checklist_mantenimiento: UploadFile | None = File(default=None),
    formulario_logistica_viaje: UploadFile | None = File(default=None),
):
    ordenes = asegurar_lista(cargar_json(ORDENES_PATH))
    orden = next((o for o in ordenes if str(o.get("nro_orden", "")) == str(nro_orden)), None)

    if orden is None:
        return {"error": f"No se encontró la orden {nro_orden}"}

    try:
        payload_data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return {"error": "Payload de cierre inválido"}

    solicitud = payload_data.get("solicitud", {}) if isinstance(payload_data, dict) else {}
    asignacion = payload_data.get("asignacion", {}) if isinstance(payload_data, dict) else {}

    viaje_data = orden.get("viaje", {}) if isinstance(orden.get("viaje", {}), dict) else {}
    recursos_data = orden.get("recursos", {}) if isinstance(orden.get("recursos", {}), dict) else {}

    if solicitud:
        viaje_data["fecha_salida"] = solicitud.get("fecha_salida", viaje_data.get("fecha_salida", ""))
        viaje_data["fecha_regreso"] = solicitud.get("fecha_llegada", viaje_data.get("fecha_regreso", ""))
        viaje_data["solicitante"] = solicitud.get("quien_solicita", viaje_data.get("solicitante", ""))
        viaje_data["area"] = solicitud.get("area_solicita", viaje_data.get("area", ""))
        viaje_data["origen"] = solicitud.get("partida", viaje_data.get("origen", ""))
        viaje_data["destino"] = solicitud.get("destino", viaje_data.get("destino", ""))
        viaje_data["motivo"] = solicitud.get("motivo", viaje_data.get("motivo", ""))

        recursos_data["fecha"] = solicitud.get("fecha_emision", recursos_data.get("fecha", ""))
        recursos_data["centro_costo"] = solicitud.get("centro_costos", recursos_data.get("centro_costo", ""))
        recursos_data["datos_solicitante"] = solicitud.get("quien_solicita", recursos_data.get("datos_solicitante", ""))
        recursos_data["area_solicitante"] = solicitud.get("area_solicita", recursos_data.get("area_solicitante", ""))
        recursos_data["partida"] = solicitud.get("partida", recursos_data.get("partida", ""))
        recursos_data["destino"] = solicitud.get("destino", recursos_data.get("destino", ""))
        recursos_data["motivo_viaje"] = solicitud.get("motivo", recursos_data.get("motivo_viaje", ""))
        recursos_data["fecha_salida_viaje"] = solicitud.get("fecha_salida", recursos_data.get("fecha_salida_viaje", ""))
        recursos_data["fecha_regreso_viaje"] = solicitud.get("fecha_llegada", recursos_data.get("fecha_regreso_viaje", ""))
        recursos_data["hora_salida"] = solicitud.get("hora_salida", recursos_data.get("hora_salida", ""))
        recursos_data["hora_regreso"] = solicitud.get("hora_regreso", recursos_data.get("hora_regreso", ""))
        recursos_data["duracion_jornadas"] = solicitud.get("duracion_jornadas", recursos_data.get("duracion_jornadas", ""))

    if asignacion:
        recursos_data["chofer_viatico"] = asignacion.get("viatico_chofer", recursos_data.get("chofer_viatico", 0))
        recursos_data["otros_gastos"] = asignacion.get("otros_gastos", recursos_data.get("otros_gastos", 0))
        recursos_data["viaticos"] = asignacion.get("viaticos_sin_otros", recursos_data.get("viaticos", 0))
        recursos_data["medio_pago"] = asignacion.get("medio_pago", recursos_data.get("medio_pago", "Caja"))
        recursos_data["alojamiento"] = asignacion.get("alojamiento", recursos_data.get("alojamiento", "NO"))
        recursos_data["acompanantes"] = asignacion.get("acompanantes", recursos_data.get("acompanantes", []))
        recursos_data["acompanantes_con_viatico"] = asignacion.get(
            "acompanantes_con_viatico", recursos_data.get("acompanantes_con_viatico", [])
        )
        recursos_data["comprobacion_operaciones_logistica"] = asignacion.get(
            "comprobaciones", recursos_data.get("comprobacion_operaciones_logistica", {})
        )

    viaje_data["recursos"] = recursos_data
    viaje_data["estado"] = "CERRADO"
    orden["viaje"] = viaje_data
    orden["recursos"] = recursos_data
    orden["estado"] = "CERRADO"

    archivo_checklist = await guardar_adjunto_logistico(
        checklist_mantenimiento,
        nro_orden,
        "Check List emitido por Mantenimiento del Equipo",
    )
    archivo_formulario = await guardar_adjunto_logistico(
        formulario_logistica_viaje,
        nro_orden,
        "Formulario Logistica de Viaje",
    )

    cierre = {
        "fecha_guardado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "datos": payload_data,
        "cerrado": True,
        "adjuntos": {
            "checklist_mantenimiento": archivo_checklist,
            "formulario_logistica_viaje": archivo_formulario,
        },
    }

    orden["cierre_logistica"] = cierre
    guardar_json(ORDENES_PATH, ordenes)

    for viaje in db:
        if str(viaje.get("id", "")) == str(orden.get("id_viaje", "")):
            viaje["estado"] = "CERRADO"
            viaje["recursos"] = recursos_data
            viaje["cierre_logistica"] = cierre
            break
    guardar_db()

    return {
        "mensaje": "Cierre logístico guardado",
        "adjuntos": cierre["adjuntos"],
        "estado": "CERRADO",
    }


@app.get("/ordenes_view", response_class=HTMLResponse)
def ordenes_view():
    with open(ORDENES_VIEW_PATH, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/print_viaje", response_class=HTMLResponse)
def print_viaje():
    with open(PRINT_VIAJE_PATH, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/personal_form", response_class=HTMLResponse)
def personal_form():
    with open(PERSONAL_FORM_PATH, "r", encoding="utf-8") as f:
        return f.read()
    
    

    
@app.get("/vehiculos")
def obtener_vehiculos():
    with open(VEHICULOS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return asegurar_lista(data)

@app.get("/choferes")
def obtener_choferes():
    with open(CHOFERES_PATH, "r") as f:
        data = json.load(f)
    return [c for c in data if c.get("estado", "") == "ACTIVO"]

@app.post("/vehiculos")
def crear_vehiculo(data: dict):
    with open(VEHICULOS_PATH, "r", encoding="utf-8") as f:
        vehiculos = asegurar_lista(json.load(f))

    vehiculos.append(data)

    with open(VEHICULOS_PATH, "w", encoding="utf-8") as f:
        json.dump(vehiculos, f, indent=4)

    return {"mensaje": "Vehículo creado"}

@app.delete("/vehiculos/{codigo}")
def eliminar_vehiculo(codigo: str):
    with open(VEHICULOS_PATH, "r", encoding="utf-8") as f:
        vehiculos = asegurar_lista(json.load(f))

    vehiculos = [v for v in vehiculos if v["codigo"] != codigo]

    with open(VEHICULOS_PATH, "w", encoding="utf-8") as f:
        json.dump(vehiculos, f, indent=4)

    return {"mensaje": "Vehículo eliminado"}

@app.get("/vehiculos_form", response_class=HTMLResponse)
def vehiculos_form():
    with open("vehiculos.html", "r", encoding="utf-8") as f:
        return f.read()

@app.put("/vehiculos/{codigo}")
def actualizar_vehiculo(codigo: str, data: dict):
    with open(VEHICULOS_PATH, "r", encoding="utf-8") as f:
        vehiculos = asegurar_lista(json.load(f))

    for v in vehiculos:
        if v["codigo"] == codigo:
            v.update(data)

    with open(VEHICULOS_PATH, "w", encoding="utf-8") as f:
        json.dump(vehiculos, f, indent=4)

    return {"mensaje": "Vehículo actualizado"}


# -------- PERSONAL --------

# cargar base
try:
    with open("personal.json", "r") as f:
        personal_db = json.load(f)
except:
    personal_db = []

def guardar_personal():
    with open("personal.json", "w") as f:
        json.dump(personal_db, f, indent=4)


@app.get("/personal")
def obtener_personal():
    return personal_db


@app.post("/personal")
def crear_personal(data: dict):

    # validación básica
    for p in personal_db:
        if p["legajo"] == data["legajo"]:
            return {"error": "El legajo ya existe"}

    personal_db.append(data)
    guardar_personal()

    return {"mensaje": "Empleado agregado"}


@app.put("/personal/{legajo}")
def actualizar_personal(legajo: str, data: dict):
    for p in personal_db:
        if str(p.get("legajo", "")) == str(legajo):
            p.update(data)
            guardar_personal()
            return {"mensaje": "Empleado actualizado"}

    return {"error": "Empleado no encontrado"}

