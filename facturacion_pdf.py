"""PDF del pedido de facturación: formulario «SOLICITUD DE EMISION» D.C. 1-9-14 Rev. 02 (A4 vertical).

Replica la disposición de la planilla «Pedido de Facturacion.xlsx» que se imprime hoy, con una mejora aprobada:
una fila de IVA por alícuota (y la alícuota en cada línea cuando hay más de una).
Dibuja solo a partir del snapshot congelado al emitir y con invariant=1: el mismo snapshot produce siempre el
mismo archivo (mismo SHA-256), así el PDF se puede regenerar sin guardarlo en el disco del servidor.
"""
import io
import os
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Imagenes", "09-smg.png")
ROJO = colors.HexColor("#E00000")
NEGRO = colors.black
MARGEN = 14 * mm
ANCHO = A4[0] - 2 * MARGEN
TITULOS = {"FACTURA": "FACTURA", "NOTA_CREDITO": "NOTA DE CREDITO", "NOTA_DEBITO": "NOTA DE DEBITO"}
ALICUOTAS = {"27": "27%", "21": "21%", "10.5": "10,5%", "0": "Exento", "NG": "No gravado"}
TASAS = {"27": Decimal("0.27"), "21": Decimal("0.21"), "10.5": Decimal("0.105")}
LEYENDA = ("UNA VEZ EMITIDA LA FACTURA EL DEPARTAMENTO CONTABLE PONDRÁ A DISPOSICIÓN LA MISMA EN LA NUBE "
           "DE LA EMPRESA PARA QUE EL SOLICITANTE TENGA ACCESO A LA MISMA")

E = ParagraphStyle("e", fontName="Helvetica", fontSize=8.5, leading=10.5)
E_B = ParagraphStyle("eb", parent=E, fontName="Helvetica-Bold")
E_C = ParagraphStyle("ec", parent=E, alignment=TA_CENTER)
E_BC = ParagraphStyle("ebc", parent=E_B, alignment=TA_CENTER)
E_R = ParagraphStyle("er", parent=E, alignment=2)
E_BR = ParagraphStyle("ebr", parent=E_B, alignment=2)
E_TIT = ParagraphStyle("tit", fontName="Helvetica-Bold", fontSize=15, leading=18, alignment=TA_CENTER)
E_TIPO = ParagraphStyle("tipo", fontName="Helvetica-Bold", fontSize=14, leading=17, alignment=TA_CENTER, textColor=ROJO)
E_ROJO = ParagraphStyle("rojo", parent=E_BC, textColor=ROJO)
E_CHICO = ParagraphStyle("chico", parent=E, fontSize=7.5, leading=9, alignment=TA_CENTER)


def _t(v):
    return escape(str(v)) if v not in (None, "") else ""


def num(v):
    d = Decimal(str(v or 0)).quantize(Decimal("0.01"))
    entero, dec = f"{abs(d):.2f}".split(".")
    entero = f"{int(entero):,}".replace(",", ".")
    return f"{'-' if d < 0 else ''}{entero},{dec}"


def fecha(texto):
    texto = str(texto or "")
    if len(texto) >= 10 and texto[4] == "-":
        return f"{int(texto[8:10])}/{int(texto[5:7])}/{texto[:4]}"
    return texto


def _campo(etq, valor, rojo=False):
    return [Paragraph(etq, E_B), Paragraph(_t(valor), E_ROJO if rojo else E_C)]


def generar_pdf(s, marca=None):
    """s: snapshot del pedido (fac_pedidos + 'lineas'). marca: texto de agua (BORRADOR / ANULADO) o None."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGEN, rightMargin=MARGEN, topMargin=MARGEN, bottomMargin=MARGEN,
                            title=f"Solicitud de emisión {s.get('numero_solicitud') or 'borrador'}", author="SMG S.R.L.",
                            invariant=1)
    mon = s.get("moneda") or ""
    hist = []

    logo = Image(LOGO, width=34 * mm, height=17 * mm) if os.path.exists(LOGO) else Paragraph("SMG", E_TIT)
    control = Table([["Código", "D.C. 1-9-14"], ["Rev.", "02"], ["Vigencia", "24/8/2017"], ["Páginas", "1 de 1"]],
                    colWidths=[18 * mm, 20 * mm], rowHeights=5 * mm)
    control.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                 ("GRID", (0, 0), (-1, -1), 0.5, NEGRO), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    cab = Table([[logo, [Paragraph("SOLICITUD DE EMISION", E_TIT), Spacer(1, 3),
                         Paragraph(TITULOS.get(s.get("tipo_solicitado"), ""), E_TIPO)], control]],
                colWidths=[42 * mm, ANCHO - 42 * mm - 40 * mm, 40 * mm], rowHeights=22 * mm)
    cab.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, NEGRO), ("LINEAFTER", (0, 0), (1, 0), 0.8, NEGRO),
                             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "CENTER")]))
    hist += [cab, Spacer(1, 4 * mm)]

    oc = s.get("oc_texto") or ""
    filas = [
        _campo("SOLICITUD Nº", s.get("numero_solicitud") or "BORRADOR") + _campo("FECHA DE EMISION:", fecha(s.get("fecha_emision"))),
        _campo("DEPTO. ORIGEN:", s.get("depto_origen") or "15") + ["", ""],
        [Paragraph("PARA: DEPARTAMENTO CONTABLE", E_B), ""] + _campo("COTIZACION :", s.get("cotizacion")),
        _campo("CLIENTE:", s.get("cliente_razon_social")) + _campo("CUIT:", s.get("cliente_cuit")),
        _campo("DOMICILIO:", s.get("cliente_domicilio") or ".-") + _campo("MONEDA:", mon),
        _campo("CONDICIÓN DE PAGO:", s.get("condicion_pago"), rojo=True) + [Paragraph(f"ORDEN DE COMPRA Nº: {_t(oc)}", E_B), ""],
    ]
    if s.get("tipo_cambio_informar"):
        filas.append([Paragraph("TIPO DE CAMBIO:", E_B), Paragraph(_t(s["tipo_cambio_informar"]), E_C), "", ""])
    datos = Table(filas, colWidths=[38 * mm, ANCHO / 2 - 38 * mm, 38 * mm, ANCHO / 2 - 38 * mm], rowHeights=7 * mm)
    estilo = [("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("SPAN", (2, 5), (3, 5)), ("SPAN", (0, 2), (1, 2))]
    for i in range(len(filas)):
        estilo += [("LINEBELOW", (0, i), (1, i), 0.6, NEGRO)]
        if i != 1:
            estilo += [("LINEBELOW", (2, i), (3, i), 0.6, NEGRO)]
    datos.setStyle(TableStyle(estilo))
    hist += [datos, Spacer(1, 5 * mm),
             Paragraph("Se solicita la Facturación y gestión de cobro del siguiente servicio realizado al cliente de referencia.", E_BC),
             Spacer(1, 4 * mm)]

    lineas = s.get("lineas") or []
    varias = len({l.get("alicuota_iva") for l in lineas}) > 1
    det = [[Paragraph("CANTIDAD", E_BC), Paragraph("DETALLE", E_BC), Paragraph("PRECIO UNITARIO", E_BC), Paragraph("SUBTOTAL", E_BC)]]
    netos = {}
    for l in lineas:
        alic = str(l.get("alicuota_iva"))
        netos[alic] = netos.get(alic, Decimal("0")) + Decimal(str(l.get("subtotal") or 0))
        texto = _t(l.get("detalle")).replace("\n", "<br/>")
        if varias:
            texto += f"<br/><font size=7>IVA {ALICUOTAS.get(alic, alic)}</font>"
        cant = Decimal(str(l.get("cantidad") or 0))
        det.append([Paragraph(f"{cant.normalize():f}".replace(".", ","), E_C), Paragraph(f"<b>{texto}</b>", E),
                    Paragraph(f"{mon}&nbsp;&nbsp;{num(l.get('precio_unitario'))}", E_R), Paragraph(f"{mon}&nbsp;&nbsp;{num(l.get('subtotal'))}", E_R)])
    alto_min = max(0, 45 * mm - 7 * mm * len(lineas))
    if alto_min:
        det.append(["", "", "", ""])
    anchos = [24 * mm, 70 * mm, 36 * mm, 36 * mm]
    tabla = Table(det, colWidths=anchos, rowHeights=[8 * mm] + [None] * len(lineas) + ([alto_min] if alto_min else []))
    tabla.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, NEGRO), ("LINEBELOW", (0, 0), (-1, 0), 0.8, NEGRO),
                               ("LINEAFTER", (0, 0), (-2, -1), 0.6, NEGRO), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    hist.append(Table([[tabla]], colWidths=[ANCHO]))

    tot = [["", Paragraph("", E_BR), Paragraph(f"{mon}&nbsp;&nbsp;{num(s.get('subtotal'))}", E_R)]]
    for alic in ("27", "21", "10.5"):
        if alic in netos:
            iva = (netos[alic] * TASAS[alic]).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
            tot.append(["", Paragraph(f"IVA {ALICUOTAS[alic]}", E_BR), Paragraph(f"{mon}&nbsp;&nbsp;{num(iva)}", E_R)])
    for alic, nombre in (("0", "Exento"), ("NG", "No gravado")):
        if alic in netos and len(netos) > 1:
            tot.append(["", Paragraph(f"{nombre} (incluido)", E_R), Paragraph(f"{mon}&nbsp;&nbsp;{num(netos[alic])}", E_R)])
    tot.append(["", Paragraph("TOTAL", E_BR), Paragraph(f"<b>{mon}&nbsp;&nbsp;{num(s.get('total'))}</b>", E_R)])
    t = Table(tot, colWidths=[ANCHO - 48 * mm - 36 * mm - 8 * mm, 48 * mm, 36 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    hist += [t, Spacer(1, 5 * mm)]

    obs = Table([[Paragraph("Observaciones:", E_C), Paragraph(_t(s.get("observaciones")).replace("\n", "<br/>"), E_ROJO)]],
                colWidths=[40 * mm, ANCHO - 40 * mm], rowHeights=None)
    obs.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, NEGRO), ("LINEAFTER", (0, 0), (0, 0), 0.6, NEGRO),
                             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    hist += [obs, Spacer(1, 7 * mm)]

    def caja(titulo, texto):
        c = Table([[Paragraph(titulo, E), ""], [Paragraph(texto, E_C), ""], [Paragraph("Firma y Aclaración", E_C), ""]],
                  colWidths=[30 * mm, 48 * mm], rowHeights=[5 * mm, 20 * mm, 5 * mm])
        c.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, NEGRO), ("LINEBELOW", (0, 0), (-1, 0), 0.6, NEGRO),
                               ("LINEAFTER", (0, 0), (0, 0), 0.6, NEGRO), ("LINEABOVE", (0, 2), (-1, 2), 0.6, NEGRO),
                               ("SPAN", (0, 1), (1, 1)), ("SPAN", (0, 2), (1, 2)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        return c

    solicito = _t(s.get("solicitante_texto") or "").replace("\n", "<br/>")
    firmas = Table([[caja("SOLICITÓ", solicito), "", caja("RECIBE", _t(s.get("recibe_texto") or ""))]],
                   colWidths=[78 * mm, ANCHO - 156 * mm, 78 * mm])
    hist += [firmas, Spacer(1, 5 * mm)]
    ley = Table([[Paragraph(f"<b>{LEYENDA}</b>", E_CHICO)]], colWidths=[ANCHO])
    ley.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, NEGRO), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    hist.append(ley)

    def pagina(c, _doc):
        c.saveState()
        c.setLineWidth(1.2)
        c.rect(MARGEN - 4 * mm, MARGEN - 4 * mm, ANCHO + 8 * mm, A4[1] - 2 * MARGEN + 8 * mm)
        if marca:
            c.setFillColor(colors.Color(0.85, 0, 0, alpha=0.18))
            c.setFont("Helvetica-Bold", 60)
            c.translate(A4[0] / 2, A4[1] / 2)
            c.rotate(40)
            c.drawCentredString(0, 0, marca)
        c.restoreState()

    doc.build(hist, onFirstPage=pagina, onLaterPages=pagina)
    return buf.getvalue()
