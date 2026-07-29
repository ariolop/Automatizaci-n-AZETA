"""
buscador/app.py — Búsqueda + publicación unificada
==================================================
Una sola pantalla: buscas un EAN y ves la ficha de AZETA y de Liderpapel a la
vez (con el más barato marcado), y publicas en PrestaShop el que elijas.

Reutiliza toda la lógica de busqueda_comun (búsqueda paralela, normalización,
comparación y publicación AZETA/Liderpapel).
"""
from __future__ import annotations

from flask import Flask, jsonify, render_template, request

import busqueda_comun as bc

app = Flask(__name__)


def _verdad(v: str | None) -> bool:
    return (v or "").lower() not in ("", "0", "false", "no", "off")


@app.route("/")
def index():
    return render_template("buscar_unificado.html",
                           ps_ok=bc.prestashop_configurado())


@app.route("/api/buscar")
def api_buscar():
    ean = (request.args.get("ean") or "").strip()
    if not ean:
        return jsonify({"ok": False, "error": "Falta el parámetro 'ean'."}), 400

    # Por defecto se consultan los dos; se pueden desmarcar desde el front.
    con_azeta = _verdad(request.args.get("azeta", "1"))
    con_cs = _verdad(request.args.get("cs", "1"))
    if not con_azeta and not con_cs:
        return jsonify({"ok": False, "error": "Elige al menos un proveedor."}), 400

    res = bc.buscar(ean, con_azeta=con_azeta, con_cs=con_cs)

    # ¿Ya existe en PrestaShop? (por cada EAN encontrado, sin duplicar consulta)
    ya = {}
    for lado in (res.get("azeta"), res.get("cs")):
        if lado and lado.get("encontrado") and lado.get("ean") and lado["ean"] not in ya:
            ya[lado["ean"]] = bc.ya_en_prestashop(lado["ean"])
    res["ok"] = True
    res["ya_prestashop"] = ya
    res["ps_ok"] = bc.prestashop_configurado()
    return jsonify(res)


@app.route("/publicar", methods=["POST"])
def publicar():
    # Aceptar tanto formulario como JSON.
    if request.is_json:
        data = request.get_json(silent=True) or {}
        proveedor = data.get("proveedor")
        ean = (data.get("ean") or "").strip()
    else:
        proveedor = request.form.get("proveedor")
        ean = (request.form.get("ean") or "").strip()

    if not ean or not proveedor:
        return jsonify({"ok": False, "error": "Faltan 'proveedor' y/o 'ean'."}), 400

    res = bc.publicar(proveedor, ean)
    codigo = 200 if res.get("ok") else 400
    return jsonify(res), codigo
