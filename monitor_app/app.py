"""
monitor_app/app.py — Monitor unificado (AZETA + Liderpapel)
===========================================================
Una sola lista de productos vigilados, con etiqueta de proveedor. El mismo EAN
puede vigilarse en AZETA y en Liderpapel a la vez. Persistencia en Supabase.
"""
from __future__ import annotations

from flask import (Flask, jsonify, redirect, render_template, request, url_for, flash)

import monitor_comun as mon
import busqueda_comun as bc

app = Flask(__name__)
app.secret_key = "monitor-unificado"


@app.route("/")
def index():
    proveedor = request.args.get("proveedor") or None
    estado = request.args.get("estado") or None
    vigilados = mon.listar(proveedor=proveedor, estado=estado)
    return render_template(
        "monitor.html",
        vigilados=vigilados,
        resumen=mon.resumen(),
        filtro_proveedor=proveedor or "",
        filtro_estado=estado or "",
        supabase_ok=mon.activo(),
        ps_ok=bc.prestashop_configurado(),
    )


@app.route("/add", methods=["POST"])
def add():
    ean = (request.form.get("ean") or "").strip()
    provs = request.form.getlist("proveedores")  # ['AZETA','Liderpapel']
    if not ean:
        flash("Introduce un EAN.", "error")
    elif not provs:
        flash("Elige al menos un proveedor.", "error")
    else:
        n = sum(1 for p in provs if mon.añadir(ean, p, origen="manual"))
        flash(f"Añadido «{ean}» a {n} proveedor(es).", "ok" if n else "error")
    return redirect(url_for("index"))


@app.route("/quitar", methods=["POST"])
def quitar():
    ean = (request.form.get("ean") or "").strip()
    proveedor = request.form.get("proveedor") or ""
    if ean and proveedor:
        mon.quitar(ean, proveedor)
        flash(f"Quitado «{ean}» ({proveedor}).", "ok")
    return redirect(url_for("index"))


@app.route("/comprobar", methods=["POST"])
def comprobar():
    proveedor = request.form.get("proveedor") or None
    try:
        res = mon.comprobar_todos(proveedor=proveedor)
        inc = res["incidencias"]
        msg = (f"Comprobados {res['comprobados']} · disponibles {res['disponibles']} · "
               f"incidencias {len(inc)} · errores {len(res['errores'])}")
        flash(msg, "ok" if not inc else "warn")
    except Exception as e:  # noqa: BLE001
        flash(f"Error al comprobar: {e}", "error")
    return redirect(url_for("index"))


@app.route("/sincronizar", methods=["POST"])
def sincronizar():
    try:
        n = mon.sincronizar_desde_prestashop()
        flash(f"Sincronizados {n} productos desde PrestaShop (como AZETA).", "ok")
    except Exception as e:  # noqa: BLE001
        flash(f"No se pudo sincronizar con PrestaShop: {e}", "error")
    return redirect(url_for("index"))


@app.route("/api/historico")
def api_historico():
    ean = (request.args.get("ean") or "").strip()
    proveedor = request.args.get("proveedor") or ""
    return jsonify({"ok": True, "historico": mon.historico_de(ean, proveedor, 100)})
