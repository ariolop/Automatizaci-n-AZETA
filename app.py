"""
app.py — AZETA Manager
----------------------
App web local (Flask) con dos funciones:
  1) Buscar / Publicar : busca un EAN en AZETA, muestra la ficha y (opcional)
     la sube a PrestaShop como producto DESACTIVADO.
  2) Monitor           : vigila a diario que los productos sigan disponibles.

Arranque:
    pip install -r requirements.txt
    python app.py
    -> abre http://127.0.0.1:5000
"""

from __future__ import annotations

import io
import os
import zipfile
from urllib.parse import urlparse

import requests
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   jsonify, send_file)

import config
import db
import monitor_comun          # monitor unificado (Supabase); las altas van aquí
from azeta_login import AzetaError
from azeta_producto import buscar_producto

app = Flask(__name__)
app.secret_key = "azeta-manager-local"

db.init_db()


def prestashop_configurado() -> bool:
    return config.prestashop_configurado()


@app.route("/")
def index():
    return redirect(url_for("buscar"))


# --------------------------------------------------------------------------- #
# 1) Buscar / Publicar
# --------------------------------------------------------------------------- #
@app.route("/buscar", methods=["GET", "POST"])
def buscar():
    datos = None
    if request.method == "POST":
        patron = (request.form.get("patron") or "").strip()
        subir = request.form.get("subir_prestashop") == "on"
        vigilar = request.form.get("añadir_vigilado") == "on"

        if not patron:
            flash("Introduce un EAN, ISBN o texto.", "error")
            return redirect(url_for("buscar"))

        try:
            datos = buscar_producto(patron)
        except AzetaError as e:
            flash(f"Error de AZETA: {e}", "error")
            return render_template("buscar.html", datos=None,
                                   ps_ok=prestashop_configurado(), ya_ps=None)

        if not datos["encontrado"]:
            flash(f"No se encontró ningún producto para «{patron}».", "error")
        else:
            # Añadir a vigilados si se pidió (monitor unificado, proveedor AZETA)
            if vigilar and datos.get("ean"):
                monitor_comun.añadir(datos["ean"], "AZETA", nombre=datos.get("nombre"), origen="manual")
                flash("Añadido al monitor unificado.", "ok")

            # Subir a PrestaShop si se pidió
            if subir:
                if not prestashop_configurado():
                    flash("PrestaShop no está configurado (rellena .env).", "error")
                else:
                    try:
                        from prestashop_client import PrestashopClient
                        res = PrestashopClient().crear_producto(datos)
                        flash(
                            f"Producto creado en PrestaShop (desactivado). "
                            f"ID: {res.get('id_product')}",
                            "ok",
                        )
                        if datos.get("ean"):
                            monitor_comun.añadir(
                                datos["ean"], "AZETA", nombre=datos.get("nombre"),
                                origen="prestashop", id_prestashop=res.get("id_product"),
                            )
                    except Exception as e:
                        flash(f"Error subiendo a PrestaShop: {e}", "error")

    # ¿Ya existe el producto en PrestaShop? (se comprueba aunque NO se suba)
    ya_ps = None
    if datos and datos.get("encontrado") and datos.get("ean") and prestashop_configurado():
        try:
            from prestashop_client import PrestashopClient
            existentes = PrestashopClient().comprobar_eans([datos["ean"]])
            ya_ps = existentes.get(datos["ean"])
        except Exception:
            ya_ps = None  # si PrestaShop no responde, no bloqueamos la búsqueda

    return render_template("buscar.html", datos=datos,
                           ps_ok=prestashop_configurado(), ya_ps=ya_ps)


# --------------------------------------------------------------------------- #
# 2) Monitor de disponibilidad
# --------------------------------------------------------------------------- #
@app.route("/monitor")
def monitor():
    # El monitor ahora es unificado (AZETA + Liderpapel) a nivel de hub.
    return redirect("/monitor")


@app.route("/monitor/comprobar", methods=["POST"])
def monitor_comprobar():
    from monitor import comprobar_todos
    sync = request.form.get("sync_prestashop") == "on"
    try:
        res = comprobar_todos(sincronizar_prestashop=sync)
        msg = (f"Comprobados {res['comprobados']} · disponibles {res['disponibles']} · "
               f"nuevas incidencias {len(res['incidencias'])}")
        flash(msg, "ok" if not res["incidencias"] else "warn")
        for d in res["incidencias"]:
            flash(f"⚠ {d['estado'].capitalize()}: {d['ean']} — {d.get('nombre') or ''}", "warn")
    except AzetaError as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("monitor"))


@app.route("/monitor/añadir", methods=["POST"])
def monitor_añadir():
    ean = (request.form.get("ean") or "").strip()
    if ean:
        db.añadir_vigilado(ean, origen="manual")
        flash(f"EAN {ean} añadido al monitor.", "ok")
    return redirect(url_for("monitor"))


@app.route("/monitor/quitar/<ean>", methods=["POST"])
def monitor_quitar(ean):
    db.quitar_vigilado(ean)
    flash(f"EAN {ean} eliminado del monitor.", "ok")
    return redirect(url_for("monitor"))


@app.route("/monitor/sincronizar", methods=["POST"])
def monitor_sincronizar():
    from monitor import sincronizar_desde_prestashop
    try:
        n = sincronizar_desde_prestashop()
        flash(f"Sincronizados {n} productos desde PrestaShop.", "ok")
    except Exception as e:
        flash(f"Error sincronizando: {e}", "error")
    return redirect(url_for("monitor"))


@app.route("/historico/<ean>")
def historico(ean):
    return jsonify(db.historico_de(ean))


# --------------------------------------------------------------------------- #
# Descarga de imágenes (zip si hay varias, archivo individual si hay una)
# --------------------------------------------------------------------------- #
def _nombre_imagen(url: str, ean: str, idx: int) -> str:
    ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
    sufijo = "" if idx == 0 else f"_{idx + 1}"
    return f"{ean or 'imagen'}{sufijo}{ext.lower()}"


@app.route("/descargar")
def descargar():
    patron = (request.args.get("patron") or "").strip()
    if not patron:
        flash("Falta el producto a descargar.", "error")
        return redirect(url_for("buscar"))
    try:
        datos = buscar_producto(patron)
    except AzetaError as e:
        flash(f"Error de AZETA: {e}", "error")
        return redirect(url_for("buscar"))

    imgs = datos.get("imagenes") or []
    if not imgs:
        flash("Este producto no tiene imágenes para descargar.", "warn")
        return redirect(url_for("buscar"))

    ean = datos.get("ean") or patron
    descargas = []
    for i, url in enumerate(imgs):
        try:
            r = requests.get(url, timeout=30)
            if r.ok and r.content:
                descargas.append((_nombre_imagen(url, ean, i), r.content))
        except requests.RequestException:
            continue

    if not descargas:
        flash("No se pudieron descargar las imágenes.", "error")
        return redirect(url_for("buscar"))

    if len(descargas) == 1:
        nombre, contenido = descargas[0]
        return send_file(io.BytesIO(contenido), as_attachment=True, download_name=nombre)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, contenido in descargas:
            z.writestr(nombre, contenido)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{ean}_imagenes.zip",
                     mimetype="application/zip")


# --------------------------------------------------------------------------- #
# Configuración (editar .env desde la app, en caliente)
# --------------------------------------------------------------------------- #
@app.route("/config", methods=["GET", "POST"])
def configuracion():
    # La configuración ahora es central a nivel de hub.
    return redirect("/config")


# --------------------------------------------------------------------------- #
# Lote (v2): procesar muchos EANs en segundo plano con seguimiento por sondeo
# --------------------------------------------------------------------------- #
@app.route("/lote")
def lote():
    # El lote ahora es unificado (AZETA + Liderpapel) a nivel de hub.
    return redirect("/lote")


@app.route("/lote/iniciar", methods=["POST"])
def lote_iniciar():
    import lote as lote_mod

    eans = lote_mod.extraer_eans(request.form.get("eans", ""))

    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        columna = request.form.get("columna", "")
        fila_inicial = request.form.get("fila_inicial", "1")
        eans += lote_mod.extraer_eans_de_archivo(
            archivo.filename, archivo.read(),
            columna=columna, fila_inicial=fila_inicial,
        )

    eans = list(dict.fromkeys(eans))  # quitar duplicados conservando orden
    if not eans:
        return jsonify({"error": "No se encontró ningún EAN válido en el texto o archivo."}), 400

    opciones = {
        "subir": request.form.get("subir_prestashop") == "on" and prestashop_configurado(),
        "vigilar": request.form.get("añadir_vigilado") == "on",
        "csv": request.form.get("generar_csv") == "on",
    }
    jid = lote_mod.crear_job(eans, opciones)
    return jsonify({"job_id": jid, "total": len(eans)})


@app.route("/lote/estado/<jid>")
def lote_estado(jid):
    import lote as lote_mod
    job = lote_mod.obtener_job(jid)
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    return jsonify(job)


@app.route("/lote/csv/<jid>")
def lote_csv(jid):
    """CSV con los productos disponibles en AZETA que NO están en PrestaShop."""
    import csv
    import lote as lote_mod
    job = lote_mod.obtener_job(jid)
    if not job:
        flash("Lote no encontrado.", "error")
        return redirect(url_for("lote"))

    filas = [it for it in job["items"] if it.get("estado") == "disponible"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["EAN_buscado", "EAN", "Nombre", "Precio_unidad_sin_IVA", "Coste_real", "Dropshipping"])
    for it in filas:
        w.writerow([
            it.get("ean", ""),
            it.get("ean_real", "") or "",
            it.get("nombre", "") or "",
            it.get("precio", "") if it.get("precio") is not None else "",
            it.get("coste", "") if it.get("coste") is not None else "",
            "si" if it.get("dropshipping") else "no",
        ])
    datos = buf.getvalue().encode("utf-8-sig")  # BOM para que Excel respete acentos
    return send_file(io.BytesIO(datos), as_attachment=True,
                     download_name=f"no_en_prestashop_{jid}.csv", mimetype="text/csv")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
