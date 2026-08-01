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
import monitor_comun as mon

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
    res["ps"] = bc.ficha_prestashop(ean)   # cómo está en PrestaShop (nombre/precio/imagen)
    res["ps_ok"] = bc.prestashop_configurado()
    return jsonify(res)


@app.route("/publicar", methods=["POST"])
def publicar():
    # Aceptar tanto formulario como JSON.
    if request.is_json:
        data = request.get_json(silent=True) or {}
        proveedor = data.get("proveedor")
        ean = (data.get("ean") or "").strip()
        stock = data.get("stock")
        modo_venta = data.get("modo_venta")
    else:
        proveedor = request.form.get("proveedor")
        ean = (request.form.get("ean") or "").strip()
        stock = request.form.get("stock")
        modo_venta = request.form.get("modo_venta")

    if not ean or not proveedor:
        return jsonify({"ok": False, "error": "Faltan 'proveedor' y/o 'ean'."}), 400

    res = bc.publicar(proveedor, ean, stock=stock, modo_venta=modo_venta)
    codigo = 200 if res.get("ok") else 400
    return jsonify(res), codigo


@app.route("/monitor", methods=["POST"])
def add_monitor():
    """Añade el producto al monitor unificado (proveedor según la ficha)."""
    data = request.get_json(silent=True) or {}
    proveedor = data.get("proveedor")
    ean = (data.get("ean") or "").strip()
    nombre = data.get("nombre")
    if not ean or not proveedor:
        return jsonify({"ok": False, "error": "Faltan 'proveedor' y/o 'ean'."}), 400
    if not mon.activo():
        return jsonify({"ok": False, "error": "El monitor (Supabase) no está configurado."}), 400
    ok = mon.añadir(ean, proveedor, nombre=nombre, origen="manual")
    return (jsonify({"ok": True}), 200) if ok else (jsonify({"ok": False, "error": "No se pudo guardar."}), 400)
