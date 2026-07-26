"""
app.py — App web local (Flask) para CS Papelería / Liderpapel.

Pestañas: Buscar (individual), Lote (masivo con sondeo), Monitor (vigilancia de
stock/precio) y Config (editar .env en caliente). Sin integración PrestaShop.

Arranque:
    pip install -r requirements.txt flask
    python app.py
    -> http://127.0.0.1:5001
"""
import io

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, Response)

import config
import db
import lote
import monitor
from cs_login import CSSession, CSError
from cs_producto import buscar_producto

app = Flask(__name__)
app.secret_key = "cs-papeleria-local"

_sesion = {"ses": None}


def get_sesion():
    if _sesion["ses"] is None:
        _sesion["ses"] = CSSession()
    return _sesion["ses"]


@app.route("/")
def index():
    return redirect(url_for("buscar"))


@app.route("/buscar", methods=["GET", "POST"])
def buscar():
    resultado = None
    if request.method == "POST":
        patron = (request.form.get("patron") or "").strip()
        if patron:
            try:
                resultado = buscar_producto(patron, sesion=get_sesion())
            except CSError as e:
                _sesion["ses"] = None
                flash(f"Error contra el portal: {e}", "error")
    return render_template("buscar.html", r=resultado)


@app.route("/lote")
def lote_form():
    return render_template("lote.html")


@app.route("/lote/iniciar", methods=["POST"])
def lote_iniciar():
    vigilar = request.form.get("vigilar") == "on"
    eans = []
    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        eans = lote.extraer_eans_de_archivo(
            archivo.filename, archivo.read(),
            request.form.get("columna"), request.form.get("fila_inicial") or 1)
    else:
        eans = lote.extraer_eans(request.form.get("texto") or "")
    if not eans:
        return jsonify({"error": "No se detectaron EANs"}), 400
    jid = lote.crear_job(eans, vigilar=vigilar)
    return jsonify({"job_id": jid, "total": len(eans)})


@app.route("/lote/estado/<jid>")
def lote_estado(jid):
    return jsonify(lote.obtener_job(jid))


@app.route("/lote/csv/<jid>")
def lote_csv(jid):
    return Response(lote.csv_de_job(jid), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=lote_{jid}.csv"})


@app.route("/monitor")
def monitor_ver():
    db.init_db()
    return render_template("monitor.html", vigilados=db.listar_vigilados())


@app.route("/monitor/add", methods=["POST"])
def monitor_add():
    for ean in lote.extraer_eans(request.form.get("eans") or ""):
        db.add_vigilado(ean)
    return redirect(url_for("monitor_ver"))


@app.route("/monitor/rm/<ean>")
def monitor_rm(ean):
    db.quitar_vigilado(ean)
    return redirect(url_for("monitor_ver"))


@app.route("/monitor/run", methods=["POST"])
def monitor_run():
    try:
        res = monitor.comprobar_todos(sesion=get_sesion())
        flash(f"Comprobados {res['total']} · {len(res['cambios'])} cambios · "
              f"{len(res['errores'])} errores", "ok")
    except CSError as e:
        _sesion["ses"] = None
        flash(f"Error: {e}", "error")
    return redirect(url_for("monitor_ver"))


@app.route("/config", methods=["GET", "POST"])
def config_ver():
    if request.method == "POST":
        config.guardar_config({k: request.form.get(k) for k in config.CLAVES})
        _sesion["ses"] = None  # forzar re-login con nuevas credenciales
        flash("Configuración guardada.", "ok")
        return redirect(url_for("config_ver"))
    return render_template("config.html", cfg=config.leer_config())


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=5001, debug=True)
