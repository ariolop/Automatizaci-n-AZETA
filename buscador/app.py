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

    # Forzar Liderpapel: mostrar el resultado aunque el EAN no coincida.
    forzar_cs = _verdad(request.args.get("forzar_cs", "0"))

    # La ficha de PrestaShop se pide EN PARALELO con los proveedores (antes iba
    # después, en serie, y con PrestaShop lento la búsqueda se alargaba).
    f_ps = bc._pool.submit(bc.ficha_prestashop, ean)
    res = bc.buscar(ean, con_azeta=con_azeta, con_cs=con_cs, forzar_cs=forzar_cs)
    try:
        ps = f_ps.result(timeout=20)   # cómo está en PrestaShop (nombre/precio/imagen)
    except Exception:  # noqa: BLE001
        ps = None

    # No está en algún proveedor pero SÍ en PrestaShop: se reintenta con su EAN
    # expositor (y con el de tienda, si se buscó por el expositor), porque a
    # veces el proveedor lo vende con ese otro código.
    if ps:
        bc.buscar_eans_alternativos(res, [ps.get("ean_expositor"), ps.get("ean13")])

    # ¿Ya existe en PrestaShop? Todos los EAN encontrados en UNA sola consulta.
    eans = [lado["ean"] for lado in (res.get("azeta"), res.get("cs"))
            if lado and lado.get("encontrado") and lado.get("ean")]
    res["ok"] = True
    res["ya_prestashop"] = bc.ya_en_prestashop_varios(eans)
    res["ps"] = ps
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
        precio_con_iva = data.get("precio_con_iva")
        id_impuestos = data.get("id_impuestos")
        activo = data.get("activo")
        ean_publicar = data.get("ean_publicar")
    else:
        proveedor = request.form.get("proveedor")
        ean = (request.form.get("ean") or "").strip()
        stock = request.form.get("stock")
        modo_venta = request.form.get("modo_venta")
        precio_con_iva = request.form.get("precio_con_iva")
        id_impuestos = request.form.get("id_impuestos")
        activo = request.form.get("activo")
        ean_publicar = request.form.get("ean_publicar")

    if not ean or not proveedor:
        return jsonify({"ok": False, "error": "Faltan 'proveedor' y/o 'ean'."}), 400

    res = bc.publicar(proveedor, ean, stock=stock, modo_venta=modo_venta,
                      precio_con_iva=precio_con_iva, id_impuestos=id_impuestos, activo=activo,
                      ean_publicar=ean_publicar)
    codigo = 200 if res.get("ok") else 400
    return jsonify(res), codigo


@app.route("/actualizar", methods=["POST"])
def actualizar():
    """Actualiza precio (con IVA) y/o stock de un producto ya existente en PrestaShop."""
    data = request.get_json(silent=True) or {}
    ean = (data.get("ean") or "").strip()
    if not ean:
        return jsonify({"ok": False, "error": "Falta 'ean'."}), 400
    res = bc.actualizar_prestashop(ean=ean, precio=data.get("precio"),
                                   stock=data.get("stock"), ean13=data.get("ean13"),
                                   id_impuestos=data.get("id_impuestos"), activo=data.get("activo"),
                                   ean_expositor=data.get("ean_expositor"))
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/expositor", methods=["POST"])
def expositor():
    """Vincula el EAN del proveedor como EAN de expositor de un producto que ya
    está en PrestaShop con otro EAN (localizado por 'ean_tienda' o 'id_product').
    Si llega 'proveedor', además lo deja vigilado en el monitor con sus datos."""
    data = request.get_json(silent=True) or {}
    res = bc.asignar_expositor(data.get("ean_expositor"), ean_tienda=data.get("ean_tienda"),
                               id_product=data.get("id_product"))
    if res.get("ok") and data.get("proveedor") and res.get("ean_expositor") and mon.activo():
        v = bc.vigilar(data["proveedor"], res["ean_expositor"])
        res["vigilado"] = bool(v.get("ok"))
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/opciones")
def opciones():
    """Listas auxiliares de PrestaShop (impuestos, proveedores) para los desplegables."""
    return jsonify({"ok": True, **bc.opciones_prestashop()})


@app.route("/monitor", methods=["POST"])
def add_monitor():
    """Añade el producto al monitor unificado (proveedor según la ficha),
    consultando antes al proveedor para guardar ya coste, precio y stock."""
    data = request.get_json(silent=True) or {}
    proveedor = data.get("proveedor")
    ean = (data.get("ean") or "").strip()
    nombre = data.get("nombre")
    if not ean or not proveedor:
        return jsonify({"ok": False, "error": "Faltan 'proveedor' y/o 'ean'."}), 400
    if not mon.activo():
        return jsonify({"ok": False, "error": "El monitor (Supabase) no está configurado."}), 400
    res = bc.vigilar(proveedor, ean, nombre=nombre)
    return jsonify(res), (200 if res.get("ok") else 400)
