"""Verificación pública de certificados de cliente (página a la que lleva el QR del PDF).

Muestra solo los datos aprobados para cotejar el documento: número, fecha de emisión, período, cliente, obra, OC,
importe neto (total a facturar, sin IVA) y estado (vigente, anulado o reemplazado). Los datos salen del registro
congelado al emitir (snapshot), nunca del documento que presenta quien consulta: un PDF alterado que reutiliza un
código válido se detecta porque sus datos no coinciden con los que muestra esta página.

Protección contra enumeración: el código tiene 12 caracteres hexadecimales (16^12 ≈ 2,8·10^14 combinaciones), las
consultas se limitan por IP y en total, y un código mal escrito o inexistente recibe exactamente la misma respuesta.
"""
import hashlib
import json
import re
import threading
import time
from collections import deque
from html import escape

FORMATO_CODIGO = re.compile(r"^[0-9A-F]{12}$")
# Campos que se agregan al snapshot después de calcular el código (no forman parte del cálculo).
CAMPOS_FUERA_DEL_CODIGO = ("codigo_verificacion", "historico", "url_verificacion")

# Límites (por proceso). Por IP: consultas totales y consultas fallidas; en total: consultas fallidas de todas las IP.
LIMITE_IP = (30, 600)             # 30 consultas cada 10 minutos
LIMITE_FALLIDAS_IP = (10, 900)    # 10 códigos inexistentes cada 15 minutos
LIMITE_FALLIDAS_TOTAL = (300, 3600)  # 300 códigos inexistentes por hora entre todas las IP
BLOQUEO_SEGUNDOS = 900


def normalizar_codigo(texto):
    codigo = str(texto or "").strip().upper()
    return codigo if FORMATO_CODIGO.match(codigo) else None


def codigo_integro(snapshot):
    """Recalcula el código desde el registro congelado: si no coincide, el registro no es verificable."""
    datos = {k: v for k, v in snapshot.items() if k not in CAMPOS_FUERA_DEL_CODIGO}
    calculado = hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12].upper()
    return calculado == snapshot.get("codigo_verificacion")


class LimitadorConsultas:
    """Ventanas deslizantes en memoria. Seguro para hilos; se reinicia con el proceso (los límites son por instancia)."""

    def __init__(self, reloj=time.monotonic):
        self._reloj = reloj
        self._lock = threading.Lock()
        self._consultas, self._fallidas, self._bloqueadas = {}, {}, {}
        self._fallidas_total = deque()

    @staticmethod
    def _recortar(cola, ahora, ventana):
        while cola and cola[0] <= ahora - ventana:
            cola.popleft()

    def permitir(self, ip):
        """Registra la consulta y devuelve False si la IP o el sistema superaron un límite."""
        with self._lock:
            ahora = self._reloj()
            if self._bloqueadas.get(ip, 0) > ahora:
                return False
            self._recortar(self._fallidas_total, ahora, LIMITE_FALLIDAS_TOTAL[1])
            if len(self._fallidas_total) >= LIMITE_FALLIDAS_TOTAL[0]:
                return False
            cola = self._consultas.setdefault(ip, deque())
            self._recortar(cola, ahora, LIMITE_IP[1])
            if len(cola) >= LIMITE_IP[0]:
                self._bloqueadas[ip] = ahora + BLOQUEO_SEGUNDOS
                return False
            cola.append(ahora)
            return True

    def registrar_fallida(self, ip):
        with self._lock:
            ahora = self._reloj()
            cola = self._fallidas.setdefault(ip, deque())
            self._recortar(cola, ahora, LIMITE_FALLIDAS_IP[1])
            cola.append(ahora)
            self._fallidas_total.append(ahora)
            if len(cola) >= LIMITE_FALLIDAS_IP[0]:
                self._bloqueadas[ip] = ahora + BLOQUEO_SEGUNDOS


def ip_cliente(request, confiar_en_proxy):
    """Con proxy de confianza (Render), la IP real es la última de X-Forwarded-For: las anteriores las puede inventar el cliente."""
    if confiar_en_proxy:
        reenviada = [p.strip() for p in str(request.headers.get("x-forwarded-for", "")).split(",") if p.strip()]
        if reenviada:
            return reenviada[-1]
    return request.client.host if request.client else "desconocida"


def datos_publicos(snapshot, fila, reemplazo):
    """Solo los campos aprobados. `fila` es el registro actual (estado); `reemplazo`, el número del certificado que lo reemplaza."""
    import certificado_pdf

    cert = snapshot["certificado"]
    ordenes = ", ".join(" ".join(p for p in (o.get("orden_compra"), o.get("revision")) if p) for o in snapshot.get("ordenes", []))
    if fila["estado"] == "EMITIDO":
        estado = "VIGENTE"
    else:
        estado = "REEMPLAZADO" if reemplazo else "ANULADO"
    return {
        "estado": estado,
        "reemplazado_por": reemplazo,
        "numero": cert.get("numero_texto"),
        "fecha_emision": certificado_pdf.fecha((snapshot.get("emision") or {}).get("fecha"))[:10],
        "periodo": cert.get("periodo_texto"),
        "cliente": (snapshot.get("cliente") or {}).get("razon_social"),
        "obra": (snapshot.get("obra") or {}).get("nombre"),
        "orden_compra": ordenes,
        "importe_neto": certificado_pdf.importe(cert.get("total_a_facturar"), cert.get("moneda")),
        "codigo": snapshot.get("codigo_verificacion"),
    }


ESTADOS = {
    "VIGENTE": ("vigente", "✓", "Registro encontrado: certificado vigente",
                "SMG registra este certificado como emitido y no anulado. El documento es auténtico solo si todos sus datos "
                "coinciden con los de abajo: un código válido por sí solo no prueba la autenticidad."),
    "ANULADO": ("anulado", "✕", "Certificado ANULADO",
                "SMG registra este certificado como anulado: no tiene validez."),
    "REEMPLAZADO": ("reemplazado", "↻", "Certificado reemplazado",
                    "Este certificado fue anulado y reemplazado. Solo el certificado que lo reemplaza tiene validez; solicítelo a SMG."),
    "NO_ENCONTRADO": ("noexiste", "?", "Código no encontrado",
                      "No hay ningún certificado con ese código. Revise que esté escrito tal como figura en el documento "
                      "(12 caracteres: letras de la A a la F y números)."),
    "NO_VERIFICABLE": ("anulado", "!", "No se puede verificar",
                       "El registro de este certificado no supera el control de integridad. No lo acepte y consulte a SMG."),
    "LIMITE": ("noexiste", "…", "Demasiadas consultas",
               "Se alcanzó el límite de consultas. Intente nuevamente más tarde."),
}

ESTILOS = """
:root{--navy:#1F3A5F;--azul:#2F6DB5;--texto:#1F2937;--gris:#6B7280;--linea:#E5E7EB;--fondo:#F3F5F8;--ok:#0F7B3E;--okf:#E7F6EC;
--err:#B42318;--errf:#FDECEA;--warn:#9A5B00;--warnf:#FFF4E0}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--texto);background:var(--fondo)}
header{background:var(--navy);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:12px}
header img{height:34px;background:#fff;border-radius:6px;padding:3px 6px}header .t{font-weight:600;font-size:15px;line-height:1.2}
header .t small{display:block;font-weight:400;opacity:.8;font-size:12px}main{max-width:560px;margin:0 auto;padding:16px}
.card{background:#fff;border:1px solid var(--linea);border-radius:12px;overflow:hidden}
.estado{padding:18px 16px;display:flex;gap:12px;align-items:flex-start}
.estado .ic{flex:none;width:40px;height:40px;border-radius:50%;display:grid;place-items:center;font-size:22px;font-weight:700;color:#fff}
.estado h1{margin:0 0 4px;font-size:19px}.estado p{margin:0;font-size:14px;line-height:1.4}
.vigente{background:var(--okf)}.vigente .ic{background:var(--ok)}.vigente h1{color:var(--ok)}
.anulado{background:var(--errf)}.anulado .ic{background:var(--err)}.anulado h1{color:var(--err)}
.reemplazado{background:var(--warnf)}.reemplazado .ic{background:var(--warn)}.reemplazado h1{color:var(--warn)}
.noexiste{background:#F1F2F4}.noexiste .ic{background:var(--gris)}
dl{margin:0;padding:6px 16px 12px;display:grid;grid-template-columns:minmax(110px,38%) 1fr}
dt,dd{margin:0;padding:9px 0;border-bottom:1px solid var(--linea);font-size:14px}dt{color:var(--gris);padding-right:10px}
dd{font-weight:600;overflow-wrap:anywhere}dl>:nth-last-child(-n+2){border-bottom:0}
.codigo{font-family:Consolas,"Courier New",monospace;letter-spacing:1px}
.aviso{font-size:13px;color:var(--gris);padding:12px 16px;border-top:1px solid var(--linea);line-height:1.45}
form{margin-top:16px;background:#fff;border:1px solid var(--linea);border-radius:12px;padding:14px 16px}
form label{font-size:13px;color:var(--gris);display:block;margin-bottom:6px}form .fila{display:flex;gap:8px}
form input{flex:1;min-width:0;font:inherit;font-family:Consolas,monospace;text-transform:uppercase;letter-spacing:1px;padding:9px 10px;
border:1px solid #CBD2DC;border-radius:8px}form button{font:inherit;font-weight:600;background:var(--navy);color:#fff;border:0;border-radius:8px;padding:9px 14px}
footer{text-align:center;font-size:12px;color:var(--gris);padding:18px 16px 26px}
"""


def pagina(estado=None, datos=None):
    """Página completa. Sin estado: solo el formulario para ingresar el código."""
    cuerpo = ""
    if estado:
        clase, icono, titulo, texto = ESTADOS[estado]
        cuerpo = f'<div class="card"><div class="estado {clase}"><div class="ic">{icono}</div><div><h1>{titulo}</h1><p>{escape(texto)}</p></div></div>'
        if datos:
            filas = [("Certificado", datos["numero"]), ("Fecha de emisión", datos["fecha_emision"]), ("Período", datos["periodo"]),
                     ("Cliente", datos["cliente"]), ("Obra", datos["obra"]), ("Orden de compra", datos["orden_compra"]),
                     ("Importe neto (total a facturar, sin IVA)", datos["importe_neto"])]
            if datos["reemplazado_por"]:
                filas.append(("Reemplazado por", datos["reemplazado_por"]))
            dl = "".join(f"<dt>{escape(k)}</dt><dd>{escape(str(v or '—'))}</dd>" for k, v in filas)
            dl += f'<dt>Código de verificación</dt><dd class="codigo">{escape(datos["codigo"])}</dd>'
            cuerpo += (f"<dl>{dl}</dl><div class=\"aviso\">Compare cada dato con el documento. Si alguno no coincide, el documento "
                       "fue alterado: no lo acepte y consulte a SMG.</div>")
        cuerpo += "</div>"
    formulario = ('<form method="get" action="/verificar"><label for="codigo">¿No puede escanear el QR? Ingrese el código de '
                  'verificación impreso en el certificado</label><div class="fila"><input id="codigo" name="codigo" maxlength="12" '
                  'autocomplete="off" spellcheck="false" placeholder="12 caracteres"><button type="submit">Verificar</button></div></form>')
    return ("<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta name=\"robots\" content=\"noindex, nofollow\"><title>Verificación de certificado · SMG</title>"
            f"<style>{ESTILOS}</style></head><body><header><img src=\"/Imagenes/09-smg.png\" alt=\"SMG\"><div class=\"t\">Verificación de "
            "certificados<small>SMG Servicios Mineros</small></div></header>"
            f"<main>{cuerpo}{formulario}</main><footer>Esta consulta muestra el estado actual registrado por SMG. No reemplaza al "
            "documento original.</footer></body></html>")


CABECERAS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'self'; frame-ancestors 'none'",
}
