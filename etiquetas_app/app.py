"""
etiquetas_app/app.py — Exportar códigos de barras (catálogo PrestaShop → PDF)
=============================================================================
Una sola pantalla: se carga el catálogo de PrestaShop (id, EAN13, nombre,
activo, PVP), se buscan/filtran los artículos, se seleccionan los que se
quieran y se genera un PDF imprimible en cuadrícula de etiquetas, con el
código de barras y el nombre de cada producto.

- Los EAN de 12/13 dígitos se dibujan como EAN-13; cualquier otro código
  (referencias, EAN inválidos) cae a Code128 para que siga siendo escaneable.
- No depende de Supabase: la fuente es el listado del módulo de PrestaShop.
"""
from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO

from flask import (Flask, jsonify, render_template, request, send_file)

import busqueda_comun as bc  # reutiliza prestashop_configurado()

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Catálogo
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("etiquetas.html", ps_ok=bc.prestashop_configurado())


@app.route("/api/catalogo")
def api_catalogo():
    """Devuelve el catálogo de PrestaShop para la selección (JSON)."""
    if not bc.prestashop_configurado():
        return jsonify({"ok": False, "error": "PrestaShop no está configurado."}), 400
    try:
        from prestashop_client import PrestashopClient
        productos = PrestashopClient().listar_productos() or []
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"No se pudo leer el catálogo: {e}"}), 400

    # Normaliza a lo que necesita el front (id, ean13, nombre, activo, precio).
    salida = []
    for p in productos:
        salida.append({
            "id": p.get("id"),
            "ean13": (p.get("ean13") or "").strip(),
            "nombre": p.get("nombre") or "",
            "fabricante": (p.get("fabricante") or "").strip(),
            "activo": bool(p.get("activo")),
            "precio": p.get("precio"),
        })
    return jsonify({"ok": True, "total": len(salida), "productos": salida})


# --------------------------------------------------------------------------- #
# Generación del PDF de etiquetas
# --------------------------------------------------------------------------- #
_EAN_RE = re.compile(r"^\d{12,13}$")


def _es_ean13(codigo: str) -> bool:
    return bool(_EAN_RE.match((codigo or "").strip()))


def _envolver(texto: str, fuente: str, tam: float, ancho_max: float, max_lineas: int) -> list[str]:
    """Parte 'texto' en líneas que caben en 'ancho_max' (word-wrap).

    Si no cabe entero en 'max_lineas', recorta la última con '…'.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    palabras = (texto or "").split()
    lineas: list[str] = []
    actual = ""
    i = 0
    while i < len(palabras):
        palabra = palabras[i]
        prueba = (actual + " " + palabra).strip()
        if stringWidth(prueba, fuente, tam) <= ancho_max or not actual:
            # Palabra suelta más ancha que la celda: se parte por caracteres.
            if not actual and stringWidth(palabra, fuente, tam) > ancho_max:
                while len(palabra) > 1 and stringWidth(palabra, fuente, tam) > ancho_max:
                    palabra = palabra[:-1]
                actual = palabra
                i += 1
            else:
                actual = prueba
                i += 1
        else:
            lineas.append(actual)
            actual = ""
            if len(lineas) >= max_lineas:
                break
    if actual and len(lineas) < max_lineas:
        lineas.append(actual)
        i = len(palabras)

    # ¿Quedó texto fuera? Marca la última línea con '…'.
    if i < len(palabras) and lineas:
        ult = lineas[-1]
        while ult and stringWidth(ult + "…", fuente, tam) > ancho_max:
            ult = ult[:-1]
        lineas[-1] = ult + "…"
    return lineas


def _barcode_drawing(codigo: str, ancho_max: float, alto: float):
    """Devuelve un Drawing con el código de barras, escalado para caber en ancho_max."""
    from reportlab.graphics.barcode import createBarcodeDrawing

    codigo = (codigo or "").strip()
    if _es_ean13(codigo):
        d = createBarcodeDrawing("EAN13", value=codigo, barHeight=alto,
                                 humanReadable=True, quiet=True)
    else:
        d = createBarcodeDrawing("Code128", value=codigo or "SIN-CODIGO", barHeight=alto,
                                 humanReadable=True, quiet=True)
    if d.width > ancho_max:
        esc = ancho_max / d.width
        d.scale(esc, esc)
        d.width *= esc
        d.height *= esc
    return d


def _dibujar_celda(c, p: dict, x: float, y: float, cel_w: float, cel_h: float, borde: bool):
    from reportlab.lib.units import mm
    from reportlab.graphics import renderPDF

    if borde:
        c.setStrokeGray(0.8)
        c.setLineWidth(0.5)
        c.rect(x, y, cel_w, cel_h)
    cx = x + cel_w / 2
    # Código de barras centrado en la parte superior de la celda.
    d = _barcode_drawing(p.get("ean") or p.get("ean13"), cel_w - 6 * mm, 13 * mm)
    renderPDF.draw(d, c, x + (cel_w - d.width) / 2, y + cel_h - d.height - 2 * mm)
    barra_bottom = y + cel_h - d.height - 2 * mm

    # Nombre: ajustado en varias líneas, aprovechando el hueco libre.
    fuente = "Helvetica-Bold"
    tam = 8.5
    leading = tam * 1.18
    margen_lat = 2.5 * mm
    zona_top = barra_bottom - 1.5 * mm
    zona_bottom = y + 1.5 * mm
    max_lineas = max(1, int((zona_top - zona_bottom) / leading))
    lineas = _envolver((p.get("nombre") or "").strip(), fuente, tam,
                       cel_w - 2 * margen_lat, max_lineas)
    bloque_h = len(lineas) * leading
    inicio = zona_top - (zona_top - zona_bottom - bloque_h) / 2
    c.setFont(fuente, tam)
    for idx, linea in enumerate(lineas):
        base = inicio - (idx + 1) * leading + (leading - tam) * 0.5
        c.drawCentredString(cx, base, linea)


def _generar_pdf(items: list[dict], columnas: int = 3, borde: bool = True,
                 por_marca: bool = True) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    columnas = max(1, min(5, int(columnas or 3)))
    W, H = A4
    margen = 10 * mm
    gap = 4 * mm
    cel_w = (W - 2 * margen - (columnas - 1) * gap) / columnas
    cel_h = 32 * mm

    # Agrupar por marca (fabricante) o un único bloque sin título.
    if por_marca:
        grupos: dict[str, list] = {}
        for it in items:
            marca = (it.get("fabricante") or "").strip() or "Sin marca"
            grupos.setdefault(marca, []).append(it)
        # Marcas alfabéticas; "Sin marca" al final.
        orden = sorted(grupos.keys(), key=lambda s: (s == "Sin marca", s.lower()))
        bloques = [(m, grupos[m]) for m in orden]
    else:
        bloques = [(None, list(items))]

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    for titulo, lista in bloques:
        if not lista:
            continue
        i = 0
        n = len(lista)
        while i < n:
            # Cabecera de la hoja con el nombre de la marca.
            if titulo:
                c.setFont("Helvetica-Bold", 15)
                c.setFillGray(0.0)
                base_titulo = H - margen - 5 * mm
                c.drawString(margen, base_titulo, titulo.upper())
                c.setStrokeGray(0.55)
                c.setLineWidth(0.9)
                c.line(margen, base_titulo - 2.5 * mm, W - margen, base_titulo - 2.5 * mm)
                top_grid = base_titulo - 7 * mm
            else:
                top_grid = H - margen
            filas = max(1, int((top_grid - margen + gap) // (cel_h + gap)))

            for r in range(filas):
                for col in range(columnas):
                    if i >= n:
                        break
                    p = lista[i]
                    i += 1
                    x = margen + col * (cel_w + gap)
                    y = top_grid - (r + 1) * cel_h - r * gap
                    _dibujar_celda(c, p, x, y, cel_w, cel_h, borde)
                if i >= n:
                    break
            c.showPage()
    c.save()
    return buf.getvalue()


@app.route("/pdf", methods=["POST"])
def pdf():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"ok": False, "error": "No se han seleccionado artículos."}), 400
    try:
        columnas = int(data.get("columnas") or 3)
    except (TypeError, ValueError):
        columnas = 3
    borde = data.get("borde", True) is not False
    por_marca = data.get("por_marca", True) is not False

    pdf_bytes = _generar_pdf(items, columnas=columnas, borde=borde, por_marca=por_marca)
    nombre = f"etiquetas_{datetime.now():%Y%m%d_%H%M}.pdf"
    return send_file(BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=nombre)
