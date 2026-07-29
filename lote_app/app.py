"""
lote_app/app.py — Lote unificado (AZETA + Liderpapel)
"""
from __future__ import annotations

from flask import Flask, Response, jsonify, render_template, request

import lote_comun as lc
import busqueda_comun as bc

app = Flask(__name__)


def _verdad(v) -> bool:
    return (v or "") not in ("", "0", "false", "no", None) and v is not False


@app.route("/")
def index():
    return render_template("lote_unificado.html", ps_ok=bc.prestashop_configurado())


@app.route("/iniciar", methods=["POST"])
def iniciar():
    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        eans = lc.extraer_eans_de_archivo(
            archivo.filename, archivo.read(),
            request.form.get("columna", ""), request.form.get("fila_inicial") or 1)
    else:
        eans = lc.extraer_eans(request.form.get("texto") or "")
    if not eans:
        return jsonify({"error": "No se detectaron EANs."}), 400

    opciones = {
        "azeta": request.form.get("azeta") == "on",
        "cs": request.form.get("cs") == "on",
        "vigilar": request.form.get("vigilar") == "on",
        "publicar_azeta": request.form.get("publicar_azeta") == "on",
        "publicar_cs": request.form.get("publicar_cs") == "on",
    }
    if not opciones["azeta"] and not opciones["cs"]:
        return jsonify({"error": "Elige al menos un proveedor."}), 400

    jid = lc.crear_job(eans, opciones)
    return jsonify({"job_id": jid, "total": len(eans)})


@app.route("/estado/<jid>")
def estado(jid):
    job = lc.obtener_job(jid)
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    return jsonify(job)


@app.route("/csv/<jid>")
def csv(jid):
    return Response(lc.csv_de_job(jid), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=lote_{jid}.csv"})
