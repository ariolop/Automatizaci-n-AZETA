"""
cs_producto.py — Búsqueda y extracción de datos de un producto en el portal B2B
de Comercial del Sur de Papelería (Liderpapel).

Uso CLI:
    python cs_producto.py <EAN_o_texto>
    python cs_producto.py 3086123594197 --json salida.json

Flujo:
  1) Busca el patrón (o=searchprdb2b). Una búsqueda por EAN devuelve el producto.
  2) Toma el primer codProduct del resultado y abre la ficha (o=productb2b).
  3) Extrae nombre, EAN, marca, precio neto/unidad, escalas de precio, stock,
     disponibilidad, imágenes y descripción.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import urljoin
from html import unescape

from .cs_login import CSSession, CSError

# --- Modelo de coste (IVA + recargo de equivalencia), igual que AZETA --------
# OJO: el portal B2B de CS Papelería NO publica el % de IVA ni el PVP en la
# ficha (solo el precio neto mayorista). Por eso el IVA se toma de un valor por
# defecto configurable (papelería = 21% habitualmente).
IVA_DEFAULT = float(os.environ.get("CS_IVA_DEFAULT", 21))
APLICAR_RECARGO = os.environ.get("CS_APLICAR_RECARGO", "1") not in ("0", "false", "False", "")
RECARGO_POR_IVA = {21: 5.2, 10: 1.4, 5: 0.62, 4: 0.5, 0: 0.0}


def calcular_coste(precio_neto, iva=None):
    """coste_real = precio_neto * (1 + (IVA + recargo)/100)."""
    if precio_neto is None:
        return None, None, None
    iva = IVA_DEFAULT if iva is None else float(iva)
    recargo = RECARGO_POR_IVA.get(int(iva), 0.0) if APLICAR_RECARGO else 0.0
    coste = round(precio_neto * (1 + (iva + recargo) / 100), 4)
    return coste, iva, recargo


# --------------------------------------------------------------------------- #
# Utilidades de parseo
# --------------------------------------------------------------------------- #
def _txt(html_fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_fragment or "")).strip()


def _num_es(texto):
    """Convierte '1.562' -> 1562 ; '0,22' -> 0.22 ; '1.234,56' -> 1234.56."""
    if texto is None:
        return None
    m = re.search(r"[\d\.\,]+", texto)
    if not m:
        return None
    s = m.group(0)
    if "," in s:  # formato ES con decimales
        s = s.replace(".", "").replace(",", ".")
    else:  # solo miles
        s = s.replace(".", "")
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


def _buscar_clase(html, clase):
    m = re.search(r'class="' + re.escape(clase) + r'"[^>]*>(.*?)</', html, re.S | re.I)
    return _txt(m.group(1)) if m else None


def _codigos_resultado(html):
    return list(dict.fromkeys(re.findall(r"o=productb2b&p=1&codProduct=(\d+)", html)))


# --------------------------------------------------------------------------- #
# Parseo de la ficha de producto
# --------------------------------------------------------------------------- #
def parsear_ficha(html, cod_product, base):
    d = {"codProduct": str(cod_product)}

    # Nombre (va en un <h3 style="color:#adadad ..."> al inicio de la ficha)
    m = re.search(r'<h3[^>]*color:\s*#adadad[^>]*>(.*?)</h3>', html, re.S | re.I)
    if not m:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    d["nombre"] = unescape(_txt(m.group(1))) if m else None

    # Marca (fila "marca</td><td>bic")
    m = re.search(r"marca\s*</td>\s*<td[^>]*>(.*?)</td>", html, re.S | re.I)
    d["marca"] = unescape(_txt(m.group(1))) if m else None

    # EANs (pestaña Logística): distingue el EAN de la UNIDAD del EAN de la
    # CAJA/embalaje (compra recomendada, CRC). Por eso a veces hay varios.
    log = re.search(r'id="contentTabLogistica".*?(?=id="contentTab|Comprados juntos|<footer)', html, re.S | re.I)
    zona = log.group(0) if log else ""
    eans_detalle, eans = [], []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", zona, re.S | re.I):
        celdas = [unescape(_txt(c)) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        celdas = [c for c in celdas if c]
        g = next((re.search(r"\b\d{13}\b", c) for c in celdas if re.search(r"\b\d{13}\b", c)), None)
        if not g:
            continue
        etiqueta = celdas[0].rstrip(":").strip() if celdas else "EAN"
        el = etiqueta.lower()
        if "unitar" in el or "unidad" in el:
            tipo = "Unidad"
        elif "crc" in el or "recomendada" in el or "caja" in el:
            tipo = "Caja"
        else:
            tipo = etiqueta
        cantidad = int(celdas[1]) if len(celdas) >= 2 and celdas[1].isdigit() else None
        eans_detalle.append({"tipo": tipo, "cantidad": cantidad, "ean": g.group(0)})
        eans.append(g.group(0))
    if not eans:  # fallback si cambia la maquetación
        eans = list(dict.fromkeys(re.findall(r"\b(\d{13})\b", zona or html)))
    d["eans"] = eans
    d["eans_detalle"] = eans_detalle
    unidad = next((e["ean"] for e in eans_detalle if e["tipo"] == "Unidad"), None)
    d["ean"] = unidad or (eans[0] if eans else None)

    # Precios
    precio_neto = _buscar_clase(html, "textoDestacado3 precio-resalta precioNeto") \
        or _buscar_clase(html, "precioNeto")
    d["precio_neto"] = _num_es(precio_neto)

    unidad = _buscar_clase(html, "precioUnidad")
    d["precio_unidad_txt"] = unidad

    # Coste real (IVA + recargo). IVA no publicado por CS -> valor por defecto.
    coste, iva, recargo = calcular_coste(d["precio_neto"])
    d["coste_real"] = coste
    d["iva"] = iva
    d["recargo"] = recargo

    # Stock disponible (clase stockDisp; fallback al primer stock-value)
    stock = _buscar_clase(html, "stockDisp")
    if stock is None:
        sv = re.findall(r'class="stock-value"[^>]*>(.*?)</', html, re.S | re.I)
        stock = _txt(sv[0]) if sv else None
    d["stock"] = _num_es(stock) if stock is not None else None
    d["disponible"] = bool(d["stock"]) and d["stock"] > 0

    # Imágenes (patrón por código de producto)
    imgs = []
    m = re.search(r'src="([^"]*resources/img/products/' + re.escape(str(cod_product)) + r'g\.jpg)"', html, re.I)
    if m:
        imgs.append(urljoin(base + "/", m.group(1)))
    for mm in re.findall(r'([^"\']*resources/img/products/multi/' + re.escape(str(cod_product)) + r'_s\d+_[a-f0-9]+\.jpg)', html, re.I):
        u = urljoin(base + "/", mm)
        if u not in imgs:
            imgs.append(u)
    d["imagenes"] = imgs

    # Descripción (div#contentTabDescripcion)
    m = re.search(r'id="contentTabDescripcion"[^>]*>(.*?)</div>\s*</div>', html, re.S | re.I)
    if not m:
        m = re.search(r'id="contentTabDescripcion"[^>]*>(.*?)</div>', html, re.S | re.I)
    d["descripcion"] = unescape(_txt(m.group(1)))[:1500] if m else None

    return d


# --------------------------------------------------------------------------- #
# Función principal
# --------------------------------------------------------------------------- #
def buscar_producto(patron, sesion=None, exacto=True):
    ses = sesion or CSSession()
    es_ean = bool(re.fullmatch(r"\d{8,14}", str(patron).strip()))
    r = ses.buscar(patron, exacto=exacto and es_ean)
    codigos = _codigos_resultado(r.text)
    if not codigos:
        return {"patron": patron, "encontrado": False, "disponible": False}

    cod = codigos[0]
    rp = ses.get_producto(cod)
    ficha = parsear_ficha(rp.text, cod, ses.BASE)
    ficha["patron"] = str(patron)
    ficha["n_resultados"] = len(codigos)

    # Verificación anti-falso-positivo: si busco por EAN, el resultado debe
    # contener ese EAN en su ficha (la búsqueda hace fallback y puede devolver
    # productos que NO corresponden al código buscado).
    if es_ean:
        patron_ean = str(patron).strip()
        if patron_ean not in ficha.get("eans", []):
            return {"patron": patron, "encontrado": False, "disponible": False,
                    "motivo": "el resultado no coincide con el EAN buscado"}
    ficha["encontrado"] = True
    return ficha


def main():
    ap = argparse.ArgumentParser(description="Buscar producto en el B2B de CS Papelería (Liderpapel).")
    ap.add_argument("patron", help="EAN o texto a buscar")
    ap.add_argument("--json", help="Guardar el resultado en un fichero JSON")
    ap.add_argument("--no-exacto", action="store_true", help="No forzar búsqueda exacta por EAN")
    args = ap.parse_args()
    try:
        res = buscar_producto(args.patron, exacto=not args.no_exacto)
    except CSError as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
    salida = json.dumps(res, ensure_ascii=False, indent=2)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(salida)
        print("Guardado en", args.json)
    else:
        print(salida)


if __name__ == "__main__":
    main()
