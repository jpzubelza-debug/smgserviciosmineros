"""PDF "Certificado de Servicio" SMG (A4 apaisado, multipágina), con el diseño aprobado en la maqueta.

Recibe el snapshot que main.py guarda al emitir (_cert_snapshot) y lo dibuja sin consultar la base: así un PDF
se puede regenerar idéntico (copia sellada de un anulado) y los emitidos no dependen de datos que cambien después.
El documento conserva su diseño propio, independiente del tema de la aplicación.
"""
import html
import io
import os
from xml.sax.saxutils import escape
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.graphics import renderPDF
from reportlab.platypus.flowables import Flowable, ImageAndFlowables
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Imagenes", "09-smg.png")
AZUL = colors.HexColor("#2F6DB5")          # encabezados (azul corporativo, imprime bien)
AZUL_CLARO = colors.HexColor("#E6EEF8")    # totales / bloques suaves
CEBRA = colors.HexColor("#F6F8FB")         # filas alternadas
GRIS_COL = colors.HexColor("#EEF1F5")      # columnas de valores contractuales
LINEA = colors.HexColor("#A9B6C6")
TEXTO = colors.HexColor("#1B2733")
PAGINA = landscape(A4)
MARGEN = 12 * mm
ANCHO_UTIL = PAGINA[0] - 2 * MARGEN
MARCA_BORRADOR = "BORRADOR – SIN VALIDEZ"

E_CELDA = ParagraphStyle("celda", fontName="Helvetica", fontSize=7, leading=8.4, textColor=TEXTO)
E_CELDA_B = ParagraphStyle("celdab", parent=E_CELDA, fontName="Helvetica-Bold")
E_CAB = ParagraphStyle("cab", fontName="Helvetica-Bold", fontSize=6.6, leading=7.6, textColor=colors.white, alignment=TA_CENTER)
E_LBL = ParagraphStyle("lbl", fontName="Helvetica-Bold", fontSize=6.6, leading=7.8, textColor=colors.white)
E_VAL = ParagraphStyle("val", fontName="Helvetica-Bold", fontSize=7.8, leading=9.2, textColor=TEXTO)
E_TIT = ParagraphStyle("tit", fontName="Helvetica-Bold", fontSize=12, leading=14, textColor=AZUL, alignment=TA_CENTER)
E_OBS = ParagraphStyle("obs", fontName="Helvetica", fontSize=7, leading=8.8, textColor=TEXTO, alignment=TA_LEFT)
E_MONTO = ParagraphStyle("monto", parent=E_VAL, fontSize=10, leading=12, alignment=2)

# Se guarda el código ISO de la OC (ARS, USD, EUR); el documento muestra el símbolo. Nunca se convierte ni se mezcla.
MONEDAS = {"ARS": ("$", "pesos argentinos"), "USD": ("US$", "dólares estadounidenses"), "EUR": ("€", "euros")}
CONCEPTOS = {"RECUPERO_ANTICIPO": "Recupero de anticipo", "FONDO_REPARO": "Fondo de reparo", "MULTA": "Multa", "OTRO": "Otro"}
BASES_DESCUENTO = {"EJECUCION": "ejecución", "EJECUCION_REAJUSTADA": "ejecución + reajuste",
                   "TOTAL_CON_RETROACTIVOS": "ejecución + reajuste + retroactivos"}


def simbolo(moneda):
    return MONEDAS.get(moneda, (moneda, moneda))[0]


def leyenda_moneda(moneda):
    sim, nombre = MONEDAS.get(moneda, (moneda, moneda))
    return f"Importes en {nombre} ({sim})" if sim != moneda else f"Importes en {moneda}"


def _t(valor):
    """Textos ingresados por usuarios: se escapan antes de pasar a Paragraph (un '&' o '<' rompería el PDF)."""
    return escape(str(valor)) if valor not in (None, "") else ""


def fecha(texto):
    """AAAA-MM-DD[ HH:MM[:SS]] → DD/MM/AAAA[ HH:MM]; otros formatos se muestran tal cual."""
    texto = str(texto or "")
    if len(texto) >= 10 and texto[4] == "-" and texto[7] == "-":
        resultado = f"{texto[8:10]}/{texto[5:7]}/{texto[:4]}"
        return resultado + (f" {texto[11:16]}" if len(texto) >= 16 else "")
    return texto


def num(valor, decimales=2):
    if valor is None:
        return "—"
    texto = f"{Decimal(str(valor)):,.{decimales}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def importe(valor, moneda):
    """Importe con símbolo; el signo va delante del símbolo (-$ 1.234,56).
    Símbolo y número van unidos por un espacio que no se corta, para que nunca queden en renglones distintos."""
    d = Decimal(str(valor))
    return f"{'-' if d < 0 else ''}{simbolo(moneda)} {num(abs(d))}"


def cant(valor):
    """Cantidades: 2 decimales, o hasta 4 si el valor los tiene."""
    if valor is None:
        return "—"
    d = Decimal(str(valor))
    return num(d, 4 if d != d.quantize(Decimal("0.01")) else 2)


def pct(parte, total):
    if not total:
        return "—"
    return f"{num(Decimal(str(parte)) / Decimal(str(total)) * 100, 1)}%"


class CanvasNumerado(rl_canvas.Canvas):
    """Agrega 'Página X de Y' en una segunda pasada."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._paginas = []

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self.setFont("Helvetica", 6.8)
            self.setFillColor(colors.HexColor("#4B5563"))
            self.drawRightString(PAGINA[0] - MARGEN, 7 * mm, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


ALTO_FILA_ENCABEZADO = 12.5
# Pasos para que un valor entre en una línea: negrita habitual, luego letra normal (más angosta) achicándose hasta
# 6,6 pt. Si aun así no entra, se parte en renglones con letra normal de 6,6 pt y la fila crece.
PASOS_VALOR = (("Helvetica-Bold", 7.8), ("Helvetica", 7.8), ("Helvetica", 7.2), ("Helvetica", 6.6))
PASOS_FIRMA = (("Helvetica-Bold", 6.6), ("Helvetica", 6.6), ("Helvetica", 6.0))

# Verificación pública: el QR lleva solo la URL con el código de verificación (sin importes ni datos del cliente).
LADO_QR = 14 * mm


def url_verificacion(s):
    """URL congelada en el snapshot al emitir (CERT_URL_VERIFICACION). Sin ella el PDF no lleva QR, solo el código."""
    return s.get("url_verificacion") if s.get("codigo_verificacion") else None


class _BloqueQR(Flowable):
    """QR de verificación con el código alfanumérico debajo (alternativa si no se puede escanear)."""

    def __init__(self, url, codigo, lado=LADO_QR):
        super().__init__()
        self.url, self.codigo, self.lado = url, codigo, lado
        self.ancho = max(lado, stringWidth(codigo, "Helvetica-Bold", 5.8))
        self.alto = lado + 8

    def wrap(self, *_):
        return self.ancho, self.alto

    # Interfaz que ImageAndFlowables espera de una imagen: el bloque tiene tamaño fijo.
    def _restrictSize(self, *_):
        return self.ancho, self.alto

    def _unRestrictSize(self):
        pass

    def draw(self):
        widget = QrCodeWidget(self.url, barLevel="M", barBorder=0)
        x0, y0, x1, y1 = widget.getBounds()
        dibujo = Drawing(self.lado, self.lado, transform=[self.lado / (x1 - x0), 0, 0, self.lado / (y1 - y0), 0, 0])
        dibujo.add(widget)
        renderPDF.draw(dibujo, self.canv, (self.ancho - self.lado) / 2, self.alto - self.lado)
        self.canv.setFont("Helvetica-Bold", 5.8)
        self.canv.setFillColor(TEXTO)
        self.canv.drawCentredString(self.ancho / 2, 1, self.codigo)


def _valor_encabezado(texto, ancho):
    """Valor de la cabecera que nunca desborda su celda: una línea si es posible, o varias líneas con la fila más alta."""
    texto = texto or ""
    disponible = ancho - 8  # LEFTPADDING + RIGHTPADDING
    plano = html.unescape(texto)
    for fuente, tamano in PASOS_VALOR:
        if stringWidth(plano, fuente, tamano) <= disponible:
            break
    return Paragraph(texto, ParagraphStyle(f"val_{fuente}_{tamano}", parent=E_VAL, fontName=fuente, fontSize=tamano,
                                           leading=round(tamano * 1.18, 2)))


def _encabezado_contrato(s):
    cert, obra, cli = s["certificado"], s["obra"], s["cliente"]
    oc = (s["ordenes"][0]["orden_compra"] or "") + (f" {s['ordenes'][0]['revision']}" if s["ordenes"][0].get("revision") else "") \
        if len(s["ordenes"]) == 1 else "Varias (ver detalle)"
    L = lambda t: Paragraph(t, E_LBL)
    anchos = [64 * mm, 34 * mm, 82 * mm, 36 * mm, ANCHO_UTIL - (64 + 34 + 82 + 36) * mm]
    V2 = lambda t: _valor_encabezado(t, anchos[2])
    V4 = lambda t: _valor_encabezado(t, anchos[4])
    marca = Image(LOGO, width=40 * mm, height=20 * mm) if os.path.exists(LOGO) else Paragraph("SMG", E_TIT)
    logo = [Paragraph("CERTIFICADO DE SERVICIO", E_TIT), Spacer(1, 3), marca]
    filas = [
        [logo, L("NOMBRE DE OBRA"), V2(_t(obra.get("nombre"))), L("Nº COTIZACIÓN"), V4(_t(s["seguimiento"].get("cotizacion")))],
        ["", L("Nº ORDEN DE COMPRA"), V2(_t(oc)), L("CLIENTE"), V4(_t(cli.get("razon_social")))],
        ["", L("MONTO DE OC"), V2(importe(s["monto_oc_total"], cert["moneda"])), L("FECHA"), V4(fecha(cert["fecha_certificado"]))],
        ["", L("PROJECT MANAGER"), V2(_t(obra.get("project_manager"))), L("CERTIFICADO Nº"), V4(cert["numero_texto"] or "Borrador")],
        ["", L("LOCALIZACIÓN"), V2(_t(obra.get("localizacion"))), L("PERÍODO (DESDE – HASTA)"),
         V4(f"{fecha(cert['periodo_desde'])} – {fecha(cert['periodo_hasta'])}")],
        ["", L("PROVINCIA"), V2(_t(obra.get("provincia"))), L("PERÍODO A CERTIFICAR"), V4(cert["periodo_texto"])],
        ["", Paragraph(f"Modalidad: {'por ítems' if cert['modalidad'] == 'ITEMS' else 'global'} · Alcance: {cert['alcance']} · "
                       f"Tipo: {cert['tipo'].lower()} · {leyenda_moneda(cert['moneda'])}", E_OBS), "", "", ""],
    ]
    # Cada fila de datos crece si un valor ocupa más de un renglón (mínimo: la altura habitual).
    altos = [max(ALTO_FILA_ENCABEZADO, *(fila[c].wrap(anchos[c] - 8, 1000)[1] + 3 for c in (2, 4))) for fila in filas[:6]]
    t = Table(filas, colWidths=anchos, rowHeights=altos + [11])
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (0, 6)), ("SPAN", (1, 6), (4, 6)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 6), "CENTER"),
        ("BACKGROUND", (1, 0), (1, 5), AZUL), ("BACKGROUND", (3, 0), (3, 5), AZUL),
        ("GRID", (0, 0), (-1, -1), 0.4, LINEA), ("BOX", (0, 0), (-1, -1), 0.9, TEXTO),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _tabla_detalle(s):
    cert = s["certificado"]
    items = cert["modalidad"] == "ITEMS"
    H = lambda t: Paragraph(t, E_CAB)
    filas = [
        [H("Ítem"), H("Descripción"), H("VALORES SEGÚN ORDEN DE COMPRA"), "", "", "", "", H("ACUMULADO"), "", "",
         H("ANTERIOR"), "", "", H("ACTUAL"), "", "", H("Observaciones")],
        ["", "", H("Unid."), H("Cant."), H("Precio unit."), H("Precio total"), H("%"),
         H("Cant."), H("Importe"), H("Avance"), H("Cant."), H("Importe"), H("Avance"), H("Cant."), H("Importe"), H("Avance"), ""],
    ]
    base_total = sum(Decimal(str(l["base_linea"])) for l in s["lineas"])
    estilos = []
    oc_actual = None
    varias = len(s["ordenes"]) > 1
    for l in s["lineas"]:
        if varias and l["orden_compra_cliente_id"] != oc_actual:
            oc_actual = l["orden_compra_cliente_id"]
            oc = next(o for o in s["ordenes"] if o["id"] == oc_actual)
            filas.append(["", Paragraph(f"Orden de compra {_t(oc['orden_compra'])} {_t(oc.get('revision'))}", E_CELDA_B)] + [""] * 15)
            estilos += [("SPAN", (1, len(filas) - 1), (16, len(filas) - 1)), ("BACKGROUND", (0, len(filas) - 1), (-1, len(filas) - 1), AZUL_CLARO)]
        base = l["base_linea"]
        filas.append([
            str(l["item_numero"] or ""), Paragraph(_t(l["descripcion"]), E_CELDA), l["unidad"] or "",
            cant(l["cantidad_contratada"]), num(l["precio_unitario"]) if items else "—", num(base), pct(base, base_total),
            cant(l["cantidad_acumulada"]), num(l["importe_acumulado"]), pct(l["importe_acumulado"], base),
            cant(l["cantidad_anterior"]), num(l["importe_anterior"]), pct(l["importe_anterior"], base),
            cant(l["cantidad_periodo"]), num(l["importe_periodo"]), pct(l["importe_periodo"], base),
            Paragraph(_t(l.get("observacion")), E_CELDA),
        ])
    sumar = lambda k: sum(Decimal(str(l[k])) for l in s["lineas"])
    filas.append(["", Paragraph("TOTALES", E_CELDA_B), "", "", "", num(base_total), "100,0%",
                  "", num(sumar("importe_acumulado")), pct(sumar("importe_acumulado"), base_total),
                  "", num(sumar("importe_anterior")), pct(sumar("importe_anterior"), base_total),
                  "", num(sumar("importe_periodo")), pct(sumar("importe_periodo"), base_total), ""])
    anchos = [22, 138, 28, 36, 48, 54, 28, 36, 52, 30, 36, 52, 30, 36, 52, 30, 0]
    anchos[-1] = ANCHO_UTIL - sum(anchos)
    assert anchos[-1] >= 60, f"Columnas más anchas que la página ({anchos[-1]:.0f} pt para observaciones)"
    t = Table(filas, colWidths=anchos, repeatRows=2)
    ultima = len(filas) - 1
    estilo = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7), ("TEXTCOLOR", (0, 0), (-1, -1), TEXTO),
        ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)), ("SPAN", (2, 0), (6, 0)), ("SPAN", (7, 0), (9, 0)),
        ("SPAN", (10, 0), (12, 0)), ("SPAN", (13, 0), (15, 0)), ("SPAN", (16, 0), (16, 1)),
        ("BACKGROUND", (0, 0), (-1, 1), AZUL), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (3, 2), (15, -1), "RIGHT"), ("ALIGN", (0, 2), (0, -1), "CENTER"), ("ALIGN", (2, 2), (2, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINEA), ("BOX", (0, 0), (-1, -1), 0.9, TEXTO),
        ("LINEAFTER", (6, 0), (6, -1), 0.9, TEXTO), ("LINEAFTER", (9, 0), (9, -1), 0.9, TEXTO),
        ("LINEAFTER", (12, 0), (12, -1), 0.9, TEXTO), ("LINEAFTER", (15, 0), (15, -1), 0.9, TEXTO),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 2), (-1, ultima - 1), [colors.white, CEBRA]),
        ("BACKGROUND", (5, 2), (6, ultima - 1), GRIS_COL),
        ("BACKGROUND", (0, ultima), (-1, ultima), AZUL_CLARO), ("FONT", (0, ultima), (-1, ultima), "Helvetica-Bold", 7),
        ("LINEABOVE", (0, ultima), (-1, ultima), 0.9, TEXTO),
    ]
    t.setStyle(TableStyle(estilo + estilos))
    return t


def _texto_descuento(d, moneda, con_reajuste):
    concepto = CONCEPTOS.get(d["concepto"], d["concepto"])
    detalle = f" – {_t(d['descripcion'])}" if d.get("descripcion") else ""
    if d.get("base") in BASES_DESCUENTO and d.get("porcentaje") is not None:
        # Sin reajuste en el certificado, todas las bases equivalen a la ejecución: se informa así para no inducir a error.
        base = BASES_DESCUENTO[d["base"]] if con_reajuste else BASES_DESCUENTO["EJECUCION"]
        calculo = f" ({num(d['porcentaje'])} % s/ {base} {importe(d['base_importe'], moneda)})"
    else:
        calculo = ""
    return f"{concepto}{detalle}{calculo}: {importe(d['importe'], moneda)}"


def _bloque_final(s):
    cert = s["certificado"]
    mon = cert["moneda"]
    monto = lambda v: Paragraph(f"<b>{importe(v, mon)}</b>", E_MONTO)
    L = lambda t: Paragraph(t, E_LBL)
    obs = []
    if s["descuentos"]:
        obs.append("<b>Descuentos:</b> " + "; ".join(_texto_descuento(d, mon, bool(s.get("reajuste"))) for d in s["descuentos"]) + ".")
    if cert.get("reemplaza_a"):
        obs.append(f"Reemplaza al certificado {_t(cert['reemplaza_a'])}, anulado.")
    if cert.get("observaciones"):
        obs.append(_t(cert["observaciones"]))
    if cert.get("exceso_importe"):
        obs.append(f"<b>Incluye exceso de saldo</b> de {importe(cert['exceso_importe'], mon)}, autorizado por "
                   f"{_t(cert.get('exceso_autorizado_por')) or '—'} el {fecha(cert.get('exceso_autorizado_en')) or '—'}.")
    if cert.get("exceso_prevision"):
        obs.append(f"<b>Supera la previsión de reajustes</b> en {importe(cert['exceso_prevision'], mon)}, autorizado por "
                   f"{_t(cert.get('exceso_autorizado_por')) or '—'} el {fecha(cert.get('exceso_autorizado_en')) or '—'}.")
    if s.get("historico"):
        h = s["historico"]
        obs.append(f"<b>Registro histórico</b>: certificado emitido fuera del sistema e incorporado por {_t(h.get('incorporado_por'))} "
                   f"el {fecha(h.get('incorporado_en'))}. {_t(h.get('justificacion'))}")
    if s.get("anulacion"):
        a = s["anulacion"]
        obs.append(f"<b>Certificado ANULADO</b> el {fecha(a['fecha'])} por {_t(a['usuario'])}. Motivo: {_t(a['motivo'])}")
    obs.append("Total a facturar expresado <b>sin IVA</b>: el IVA se determina al facturar.")
    anchos = [42 * mm, 46 * mm, 88 * mm, 50 * mm, ANCHO_UTIL - (42 + 46 + 88 + 50) * mm]
    textos = [Paragraph(o, E_OBS) for o in obs]
    url = url_verificacion(s) if cert.get("numero_texto") else None  # un borrador no lleva QR: no tiene validez
    if url:
        # QR en la esquina superior derecha, a la altura del título, con el código alfanumérico debajo. El texto lo rodea:
        # angosto a su lado y con el ancho completo debajo. El título se centra sobre todo el ancho (como sin QR).
        qr = _BloqueQR(url, s["codigo_verificacion"])
        separacion = 2 * mm
        titulo = Paragraph("<b>OBSERVACIONES</b>", ParagraphStyle("ot", parent=E_OBS, alignment=TA_CENTER,
                                                                  rightIndent=-(qr.ancho + separacion)))
        observaciones = [ImageAndFlowables(qr, [titulo, Spacer(1, 3)] + textos, imageSide="right", imageLeftPadding=separacion,
                                           imageRightPadding=0, imageTopPadding=1.2 * mm, imageBottomPadding=1)]
    else:
        observaciones = [Paragraph("<b>OBSERVACIONES</b>", ParagraphStyle("ot", parent=E_OBS, alignment=TA_CENTER)), Spacer(1, 3)] + textos

    def titulo_firma(titulo, nombre, ancho):
        # El nombre usa negrita si entra en una línea; si no, letra normal (más angosta) y luego más chica antes de partirse.
        disponible = ancho - 8 - 12  # columna menos sus márgenes y los del recuadro azul
        plano = html.unescape(nombre)
        for fuente, tamano in PASOS_FIRMA:
            if stringWidth(plano, fuente, tamano) <= disponible:
                break
        estilo = ParagraphStyle(f"firma_{fuente}_{tamano}", parent=E_CAB, fontName=fuente, fontSize=tamano, leading=round(tamano * 1.15, 2))
        cab = Table([[Paragraph(f"CONFORMIDAD DEL {titulo}", E_CAB)], [Paragraph(nombre, estilo)]], colWidths=[ancho - 8])
        cab.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AZUL), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (0, 0), 2),
                                 ("BOTTOMPADDING", (0, -1), (0, -1), 2)]))
        return cab
    pie_firma = lambda: Paragraph("Firma, aclaración y sello", ParagraphStyle("f", parent=E_OBS, alignment=TA_CENTER, fontName="Helvetica-Bold"))
    titulos = [titulo_firma("CONTRATISTA", "SMG S.R.L.", anchos[3]), titulo_firma("CLIENTE", _t(s["cliente"].get("razon_social") or ""), anchos[4])]
    altos_titulo = [c.wrap(a - 8, PAGINA[1])[1] for c, a in zip(titulos, anchos[3:])]
    # Las dos líneas de firma quedan a la misma altura aunque un recuadro sea más alto; el espacio para firmar nunca baja de 40 pt.
    firmas = [[c, Spacer(1, 40 + max(altos_titulo) - h), pie_firma()] for c, h in zip(titulos, altos_titulo)]
    rj = s.get("reajuste")
    importes = [(cert["acumulado"], "TOTAL CERTIFICADO ACUMULADO"), (cert["importe_periodo"], "CERTIFICADO NETO ACTUAL")]
    if rj:
        importes += [(rj["reajuste_periodo"], f"REAJUSTE · COEF. {num(rj['coeficiente'], 6)}" if rj.get("coeficiente") else "REAJUSTE DEL PERÍODO"),
                     (rj["retroactivo"], "DIFERENCIAS RETROACTIVAS")]
    importes += [(cert["descuentos_netos"], "DESCUENTOS NETOS"), (cert["total_a_facturar"], "TOTAL A FACTURAR (NETO, SIN IVA)")]
    filas = [[monto(v), L(t), "", "", ""] for v, t in importes]
    filas[0][2:] = [observaciones] + firmas
    n = len(filas) - 1
    # Alto mínimo por fila; si las observaciones o las firmas necesitan más, todas las filas crecen por igual (nunca desborda).
    medida = rl_canvas.Canvas(io.BytesIO())  # el texto que rodea al QR necesita un lienzo para medirse
    alto_obs = sum(f.wrapOn(medida, anchos[2] - 8, PAGINA[1])[1] for f in observaciones) + 6
    alto_firmas = max(sum(f.wrap(a - 8, PAGINA[1])[1] for f in col) for col, a in zip(firmas, anchos[3:])) + 6
    alto_fila = max(16 if rj else 19, alto_obs / len(filas), alto_firmas / len(filas), 58 / len(filas))
    t = Table(filas, colWidths=anchos, rowHeights=[alto_fila] * len(filas))
    t.setStyle(TableStyle([
        ("SPAN", (2, 0), (2, n)), ("SPAN", (3, 0), (3, n)), ("SPAN", (4, 0), (4, n)),
        ("BACKGROUND", (1, 0), (1, n), AZUL), ("VALIGN", (0, 0), (1, n), "MIDDLE"), ("VALIGN", (2, 0), (4, 0), "TOP"),
        ("BACKGROUND", (0, n), (1, n), AZUL_CLARO), ("BACKGROUND", (1, n), (1, n), AZUL),
        ("GRID", (0, 0), (-1, -1), 0.4, LINEA), ("BOX", (0, 0), (-1, -1), 0.9, TEXTO),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _detalle_reajustes(s):
    rj, sim = s["reajuste"], simbolo(s["certificado"]["moneda"])
    H = lambda t: Paragraph(t, E_CAB)
    titulo = Table([[Paragraph(f"DETALLE DE REAJUSTES · {_t(rj['formula_texto'])}", E_LBL)]], colWidths=[ANCHO_UTIL])
    titulo.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AZUL), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
    filas = [[H("Tipo"), H("Certificado de origen"), H("OC"), H("Período"), H("Componente"), H(f"Base ({sim})"), H("Coef. aplicado antes"),
              H("Coef. vigente"), H(f"Reconocido antes ({sim})"), H(f"Corresponde ({sim})"), H(f"Importe ({sim})")]]
    for l in rj["lineas"]:
        filas.append([l["tipo"], _t(l["origen"]), Paragraph(_t(l["oc"]), E_CELDA), l["periodo"], l["componente"], num(l["base"]),
                      num(l["coef_anterior"], 6) if l.get("coef_anterior") else "—", num(l["coef"], 6) if l.get("coef") else "—",
                      num(l["reconocido_previo"]), num(l["esperado"]), num(l["importe"])])
    filas.append(["", "", "", "", "", "", "", "", "", "Total reajustes", num(Decimal(str(rj["reajuste_periodo"])) + Decimal(str(rj["retroactivo"])))])
    anchos = [20 * mm, 32 * mm, 20 * mm, 24 * mm, 22 * mm, 28 * mm, 26 * mm, 24 * mm, 28 * mm, 26 * mm, 0]
    anchos[-1] = ANCHO_UTIL - sum(anchos)
    lineas = Table(filas, colWidths=anchos)
    lineas.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7), ("TEXTCOLOR", (0, 0), (-1, -1), TEXTO), ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (5, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINEA), ("BOX", (0, 0), (-1, -1), 0.9, TEXTO),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO), ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 7),
    ]))
    ind = [[H("Período"), H("Componente"), H("Ponderación"), H("Valor base"), H("Índice"), H("Versión"), H("Estado"), H("Relación"),
            H("Motivo de la versión")]]
    for i in rj["indices"]:
        ind.append([i["periodo"], _t(i["nombre"]), num(i["ponderacion"], 2), num(i["valor_base"]), num(i["valor"]), f"v{i['version']}",
                    i["estado"].capitalize(), num(i["relacion"], 6), Paragraph(_t(i.get("motivo")) or "—", E_CELDA)])
    anchos_i = [26 * mm, 30 * mm, 22 * mm, 26 * mm, 26 * mm, 16 * mm, 22 * mm, 24 * mm, 0]
    anchos_i[-1] = ANCHO_UTIL - sum(anchos_i)
    indices = Table(ind, colWidths=anchos_i)
    indices.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7), ("TEXTCOLOR", (0, 0), (-1, -1), TEXTO), ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (2, 1), (7, -1), "RIGHT"), ("ALIGN", (5, 1), (6, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINEA), ("BOX", (0, 0), (-1, -1), 0.9, TEXTO),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CEBRA]),
    ]))
    partes = [titulo, lineas]
    if rj["indices"]:
        partes += [Spacer(1, 3), Paragraph("<b>Índices utilizados</b> – valores vigentes al emitir; cada corrección queda registrada como una "
                                           "versión nueva.", E_OBS), Spacer(1, 2), indices]
    else:
        partes += [Spacer(1, 3), Paragraph("Reajustes efectivamente pagados según el certificado original (registro histórico).", E_OBS)]
    return KeepTogether(partes)


def generar_pdf(s, marca=None):
    buffer = io.BytesIO()
    cert = s["certificado"]
    alto_franja = 9 * mm

    def fondo(c, doc, primera):
        c.saveState()
        if not primera:
            # Franja compacta en páginas siguientes: identifica el certificado y la moneda sin repetir todo el encabezado.
            y = PAGINA[1] - MARGEN - alto_franja + 2
            c.setFillColor(AZUL)
            c.rect(MARGEN, y, ANCHO_UTIL, alto_franja - 2, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(MARGEN + 6, y + 8, f"CERTIFICADO DE SERVICIO · {cert['numero_texto'] or 'BORRADOR'}")
            c.setFont("Helvetica", 7.5)
            c.drawRightString(PAGINA[0] - MARGEN - 6, y + 8, f"{s['obra'].get('nombre') or ''} · {s['cliente'].get('razon_social') or ''} · "
                              f"Período {cert['periodo_texto']} · {leyenda_moneda(cert['moneda'])} (continuación)")
        e = s["emision"]
        c.setFont("Helvetica", 6.8)
        c.setFillColor(colors.HexColor("#4B5563"))
        verificacion = s.get("codigo_verificacion") if cert.get("numero_texto") else None
        c.drawString(MARGEN, 7 * mm, f"Documento emitido desde SMG Dashboard Operaciones – Usuario: {e.get('usuario') or '—'} – "
                                     f"Fecha: {fecha(e.get('fecha'))} – Verificación: {verificacion or 'vista previa sin validez'}")
        c.restoreState()

    def sello(c, doc):
        # La marca se dibuja al cerrar la página, sobre el contenido, semitransparente.
        if not marca:
            return
        c.saveState()
        c.setFont("Helvetica-Bold", 60 if len(marca) <= 24 else 48)
        c.setFillColor(colors.Color(0.80, 0.10, 0.10, alpha=0.16) if marca == "ANULADO" else colors.Color(0.35, 0.40, 0.50, alpha=0.16))
        c.translate(PAGINA[0] / 2, PAGINA[1] / 2)
        c.rotate(24)
        c.drawCentredString(0, -20, marca)
        c.restoreState()

    doc = BaseDocTemplate(buffer, pagesize=PAGINA, leftMargin=MARGEN, rightMargin=MARGEN, topMargin=MARGEN,
                          bottomMargin=11 * mm, title=cert["numero_texto"] or "Borrador de certificado", author="SMG Dashboard Operaciones")
    alto = PAGINA[1] - MARGEN - 11 * mm
    doc.addPageTemplates([
        PageTemplate("primera", [Frame(MARGEN, 11 * mm, ANCHO_UTIL, alto, 0, 0, 0, 0)], onPage=lambda c, d: fondo(c, d, True), onPageEnd=sello),
        PageTemplate("resto", [Frame(MARGEN, 11 * mm, ANCHO_UTIL, alto - alto_franja, 0, 0, 0, 0)], onPage=lambda c, d: fondo(c, d, False), onPageEnd=sello),
    ])
    historia = [NextPageTemplate("resto"), _encabezado_contrato(s), Spacer(1, 5), _tabla_detalle(s), Spacer(1, 6)]
    if s.get("reajuste"):
        historia += [_detalle_reajustes(s), Spacer(1, 6)]
    historia.append(KeepTogether([_bloque_final(s)]))
    doc.build(historia, canvasmaker=CanvasNumerado)
    return buffer.getvalue()
