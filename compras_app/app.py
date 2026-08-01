"""
compras_app/app.py — Compras
============================
Registro de todo lo que compramos a los proveedores (AZETA / Liderpapel) para:
  - comparar el precio pagado con el precio ACTUAL del proveedor (scraping local),
  - ver la evolución de nuestras propias compras (más caro/barato que antes),
  - detectar productos pendientes (sin enlazar a EAN o sin publicar en la tienda).

- Consulta del histórico y alta de compras: en cualquier sitio (lee/escribe Supabase).
- Comprobación de precio actual (scraping): SOLO en local (como competencia_app).

Identificación del producto:
  - AZETA trae EAN en la factura.
  - Liderpapel NO trae EAN (solo código + referencia); el EAN se resuelve por
    scraping y se guarda para dejar el producto "enlazado".
"""
from __future__ import annotations

import io
import os
import csv

from flask import Flask, jsonify, render_template, request

import compras_comun as cc
import busqueda_comun as bc

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Helpers de formato para la plantilla (Jinja)
# --------------------------------------------------------------------------- #
def _eur(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "—"


def _var(p) -> str:
    """Variación % con flecha y color (HTML). >0 rojo (subió), <0 verde (bajó)."""
    if p is None or p == "":
        return '<span class="flat">—</span>'
    try:
        p = float(p)
    except (TypeError, ValueError):
        return '<span class="flat">—</span>'
    signo = "+" if p > 0 else ""
    cls = "up" if p > 0 else ("down" if p < 0 else "flat")
    arr = "▲" if p > 0 else ("▼" if p < 0 else "•")
    return f'<span class="{cls}">{arr} {signo}{p}%</span>'


app.jinja_env.globals.update(eur=_eur, var=_var)


# --------------------------------------------------------------------------- #
# Local vs Render (el scraping solo tiene sentido en local)
# --------------------------------------------------------------------------- #
def es_local() -> bool:
    if os.environ.get("COMPRAS_LOCAL") == "1":
        return True
    return os.environ.get("RENDER") is None


# --------------------------------------------------------------------------- #
# Lectura de ficheros subidos (CSV / TXT / Excel) -> (cabeceras, filas)
# --------------------------------------------------------------------------- #
def _leer_tabla(nombre: str, contenido: bytes) -> tuple[list[str], list[list]]:
    nombre = (nombre or "").lower()
    if nombre.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        ws = wb.active
        filas = [[("" if c is None else c) for c in fila]
                 for fila in ws.iter_rows(values_only=True)]
        wb.close()
    else:  # CSV / TXT
        texto = contenido.decode("utf-8-sig", errors="replace")
        # Detecta separador (; típico en España; , en inglés; tab).
        muestra = texto[:4096]
        sep = ";" if muestra.count(";") >= muestra.count(",") else ","
        if muestra.count("\t") > muestra.count(sep):
            sep = "\t"
        filas = [fila for fila in csv.reader(io.StringIO(texto), delimiter=sep)]
    filas = [f for f in filas if any(str(c).strip() for c in f)]  # sin filas vacías
    if not filas:
        return [], []
    cabeceras = [str(c).strip() for c in filas[0]]
    return cabeceras, filas[1:]


# Sinónimos de cabecera -> campo interno.
_ALIAS = {
    "ean": ["ean", "ean13", "codigo de barras", "código de barras", "barcode"],
    "codigo": ["codigo", "código", "cod", "cod.", "codigo articulo", "código artículo", "art", "articulo", "artículo"],
    "referencia": ["referencia", "ref", "ref.", "reference"],
    "nombre": ["nombre", "descripcion", "descripción", "producto", "concepto", "articulo", "artículo", "detalle"],
    "cantidad": ["cantidad", "cant", "cant.", "uds", "unidades", "qty"],
    "precio_pagado": ["precio", "precio unitario", "precio neto", "precio ud", "p.unit", "importe unitario", "coste", "precio compra"],
    "importe_total": ["importe", "importe total", "total", "subtotal", "total linea", "total línea"],
    "fecha_compra": ["fecha", "fecha compra", "fecha factura", "fecha albaran", "fecha albarán"],
    "factura": ["factura", "nº factura", "num factura", "n factura", "albaran", "albarán", "documento", "pedido"],
}


def _mapear_columnas(cabeceras: list[str]) -> dict:
    """Auto-detecta qué columna corresponde a cada campo (por nombre de cabecera)."""
    norm = [(_slug(h), i) for i, h in enumerate(cabeceras)]
    mapa: dict[str, int] = {}
    for campo, alias in _ALIAS.items():
        alias_slug = [_slug(a) for a in alias]
        # 1) coincidencia exacta; 2) la cabecera contiene el alias.
        idx = next((i for s, i in norm if s in alias_slug), None)
        if idx is None:
            idx = next((i for s, i in norm
                        if any(a and a in s for a in alias_slug)), None)
        if idx is not None:
            mapa[campo] = idx
    return mapa


def _slug(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


# --------------------------------------------------------------------------- #
# Páginas
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    resumen = cc.resumen_por_producto()
    return render_template(
        "compras.html",
        local=es_local(),
        supabase_ok=cc.activo(),
        ps_ok=bc.prestashop_configurado(),
        productos=resumen,
    )


# --------------------------------------------------------------------------- #
# Alta manual
# --------------------------------------------------------------------------- #
@app.route("/anadir", methods=["POST"])
def anadir():
    if not cc.activo():
        return jsonify({"error": "Supabase no está configurado."}), 400
    d = request.get_json(silent=True) or request.form.to_dict()
    if not (d.get("proveedor") or "").strip():
        return jsonify({"error": "Falta el proveedor."}), 400
    if not any((d.get(k) or "").strip() for k in ("ean", "codigo", "referencia", "nombre")):
        return jsonify({"error": "Indica al menos EAN, código, referencia o nombre."}), 400
    if not cc.anadir_compra(d):
        return jsonify({"error": "No se pudo guardar en Supabase."}), 500
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Importación CSV / Excel
# --------------------------------------------------------------------------- #
@app.route("/previsualizar", methods=["POST"])
def previsualizar():
    """Lee el fichero, auto-detecta columnas y devuelve cabeceras + muestra para
    que el usuario confirme el mapeo antes de importar."""
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "No se recibió ningún fichero."}), 400
    cabeceras, filas = _leer_tabla(archivo.filename, archivo.read())
    if not cabeceras:
        return jsonify({"error": "El fichero está vacío o no se pudo leer."}), 400
    mapa = _mapear_columnas(cabeceras)
    muestra = [[("" if c is None else str(c)) for c in f] for f in filas[:8]]
    return jsonify({"ok": True, "cabeceras": cabeceras, "mapa": mapa,
                    "muestra": muestra, "n_filas": len(filas)})


@app.route("/importar", methods=["POST"])
def importar():
    """Importa el fichero usando el mapeo de columnas confirmado por el usuario.
    Espera: archivo, proveedor, y un índice por campo (col_<campo>)."""
    if not cc.activo():
        return jsonify({"error": "Supabase no está configurado."}), 400
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "No se recibió ningún fichero."}), 400
    proveedor = (request.form.get("proveedor") or "").strip()
    if not proveedor:
        return jsonify({"error": "Elige el proveedor del fichero."}), 400
    fecha_defecto = (request.form.get("fecha_compra") or "").strip()
    factura_defecto = (request.form.get("factura") or "").strip()

    _, filas = _leer_tabla(archivo.filename, archivo.read())
    if not filas:
        return jsonify({"error": "El fichero no tiene filas de datos."}), 400

    # Mapa de columnas: col_<campo>=<indice> (viene del front tras confirmar).
    campos = ["ean", "codigo", "referencia", "nombre", "cantidad",
              "precio_pagado", "importe_total", "fecha_compra", "factura"]
    mapa = {}
    for c in campos:
        v = request.form.get(f"col_{c}")
        if v not in (None, "", "-1"):
            try:
                mapa[c] = int(v)
            except ValueError:
                pass

    registros = []
    for fila in filas:
        d = {"proveedor": proveedor}
        for campo, idx in mapa.items():
            if 0 <= idx < len(fila):
                d[campo] = fila[idx]
        d.setdefault("fecha_compra", fecha_defecto)
        if factura_defecto and not (d.get("factura") or "").strip():
            d["factura"] = factura_defecto
        # Descarta filas sin ninguna identificación útil.
        if any((str(d.get(k) or "")).strip() for k in ("ean", "codigo", "referencia", "nombre")):
            registros.append(d)

    if not registros:
        return jsonify({"error": "Ninguna fila tenía datos utilizables con ese mapeo."}), 400
    if not cc.anadir_compras(registros):
        return jsonify({"error": "No se pudieron guardar las compras en Supabase."}), 500
    return jsonify({"ok": True, "importadas": len(registros)})


# --------------------------------------------------------------------------- #
# Precio actual (scraping, solo local) + enlace de EAN
# --------------------------------------------------------------------------- #
def _scrapear_precio(proveedor: str, ean: str | None, referencia: str | None,
                     codigo: str | None) -> dict:
    """Devuelve {precio_neto, ean, nombre, error} del proveedor para un producto."""
    prov = (proveedor or "").strip().lower()
    if prov == "azeta":
        if not ean:
            return {"error": "AZETA necesita EAN para consultar."}
        raw = bc.norm_azeta(bc.buscar_azeta(ean))
    elif prov in ("liderpapel", "cs"):
        patron = ean or referencia or codigo
        if not patron:
            return {"error": "Liderpapel necesita EAN, referencia o código."}
        raw = bc.norm_cs(bc.buscar_cs(patron))
    else:
        return {"error": f"Proveedor no reconocido: {proveedor}"}
    if not raw.get("encontrado"):
        return {"error": raw.get("error") or "No se encontró el producto."}
    return {"precio_neto": raw.get("precio_neto"), "ean": raw.get("ean"),
            "nombre": raw.get("nombre")}


@app.route("/precio_actual", methods=["POST"])
def precio_actual():
    if not es_local():
        return jsonify({"error": "La comprobación de precios solo está disponible en local."}), 403
    d = request.get_json(silent=True) or {}
    clave = (d.get("clave") or "").strip()
    proveedor = (d.get("proveedor") or "").strip()
    ean = (d.get("ean") or d.get("ean_resuelto") or "").strip() or None
    ref = (d.get("referencia") or "").strip() or None
    cod = (d.get("codigo") or "").strip() or None
    if not clave:
        return jsonify({"error": "Falta la clave del producto."}), 400

    res = _scrapear_precio(proveedor, ean, ref, cod)
    if res.get("error"):
        return jsonify({"ok": False, "error": res["error"]}), 200

    precio = res.get("precio_neto")
    cc.guardar_precio_actual(clave, precio)
    # Si Liderpapel resolvió un EAN y no lo teníamos, lo enlazamos.
    ean_nuevo = (res.get("ean") or "").strip()
    if ean_nuevo and not ean:
        cc.enlazar_ean(clave, ean_nuevo)

    return jsonify({"ok": True, "precio_actual": precio, "ean_resuelto": ean_nuevo or None,
                    "nombre": res.get("nombre")})


@app.route("/enlazar", methods=["POST"])
def enlazar():
    d = request.get_json(silent=True) or {}
    clave = (d.get("clave") or "").strip()
    ean = (d.get("ean") or "").strip()
    if not (clave and ean):
        return jsonify({"error": "Falta clave o EAN."}), 400
    if not cc.enlazar_ean(clave, ean):
        return jsonify({"error": "No se pudo enlazar."}), 500
    return jsonify({"ok": True})


@app.route("/borrar", methods=["POST"])
def borrar():
    d = request.get_json(silent=True) or {}
    try:
        id_ = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Falta el id."}), 400
    if not cc.borrar(id_):
        return jsonify({"error": "No se pudo borrar."}), 500
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Histórico de un producto + pendientes
# --------------------------------------------------------------------------- #
@app.route("/historico/<path:clave>")
def historico(clave):
    return jsonify({"ok": True, "clave": clave, "historico": cc.historico_de_clave(clave)})


@app.route("/pendientes")
def pendientes():
    """Dos listas:
       - sin_enlazar: compras (Liderpapel) sin EAN conocido.
       - no_publicados: productos con EAN que NO están en PrestaShop.
    """
    resumen = cc.resumen_por_producto()
    sin_enlazar = [p for p in resumen if not p["enlazado"]]

    no_publicados = []
    eans = [(p["ean"] or p["ean_resuelto"]) for p in resumen if (p["ean"] or p["ean_resuelto"])]
    if eans and bc.prestashop_configurado():
        try:
            from prestashop_client import PrestashopClient
            existentes = PrestashopClient().comprobar_eans(eans)
            for p in resumen:
                e = p["ean"] or p["ean_resuelto"]
                if e and not existentes.get(e):
                    no_publicados.append(p)
        except Exception:  # noqa: BLE001
            pass

    return jsonify({"ok": True, "sin_enlazar": sin_enlazar,
                    "no_publicados": no_publicados,
                    "ps_ok": bc.prestashop_configurado()})
