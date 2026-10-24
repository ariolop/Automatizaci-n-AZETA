"""
monitor_app/app.py — Monitor unificado (AZETA + Liderpapel)
===========================================================
Una sola lista de productos vigilados, con etiqueta de proveedor. El mismo EAN
puede vigilarse en AZETA y en Liderpapel a la vez. Persistencia en Supabase.

Un producto puede estar en PrestaShop con un EAN y en el proveedor con otro (el
del expositor). La fila guarda los dos: 'ean' (PrestaShop, clave y columna de
PVP) y 'ean_expositor' (el que se usa para consultar al proveedor).
"""
from __future__ import annotations

from flask import (Flask, jsonify, redirect, render_template, request, url_for, flash)

import monitor_comun as mon
import busqueda_comun as bc

app = Flask(__name__)
app.secret_key = "monitor-unificado"


POR_PAGINA = 50


@app.route("/")
def index():
    proveedor = request.args.get("proveedor") or None
    estado = request.args.get("estado") or None
    buscar = (request.args.get("q") or "").strip() or None
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except ValueError:
        pagina = 1
    datos = mon.listar_pagina(proveedor=proveedor, estado=estado, buscar=buscar,
                              pagina=pagina, por_pagina=POR_PAGINA)

    # ¿Cuáles de esta página existen en PrestaShop y a qué PVP (IVA incl.)?
    # (una sola consulta por página).
    en_ps = None
    precios_ps: dict = {}
    if bc.prestashop_configurado() and datos["filas"]:
        try:
            from prestashop_client import PrestashopClient
            eans = [f["ean"] for f in datos["filas"] if f.get("ean")]
            existentes = PrestashopClient().comprobar_eans(eans) or {}
            en_ps = set(existentes.keys())
            precios_ps = {e: v.get("precio") for e, v in existentes.items()
                          if isinstance(v, dict) and v.get("precio") is not None}
        except Exception:  # noqa: BLE001  (PrestaShop caído/mantenimiento: columna neutra)
            en_ps = None

    return render_template(
        "monitor.html",
        vigilados=datos["filas"],
        pag=datos,
        resumen=mon.resumen(),
        filtro_proveedor=proveedor or "",
        filtro_estado=estado or "",
        q=buscar or "",
        supabase_ok=mon.activo(),
        ps_ok=bc.prestashop_configurado(),
        en_ps=en_ps,
        precios_ps=precios_ps,
        job=mon.estado_comprobacion(),
    )


@app.route("/comprobar-uno", methods=["POST"])
def comprobar_uno():
    """Recomprueba una fila en segundo plano y devuelve el nuevo estado (JSON)."""
    data = request.get_json(silent=True) or {}
    ean = (data.get("ean") or request.form.get("ean") or "").strip()
    proveedor = data.get("proveedor") or request.form.get("proveedor") or ""
    if not ean or not proveedor:
        return jsonify({"ok": False, "error": "Faltan datos."}), 400
    try:
        r = mon.comprobar_uno(ean, proveedor)
        if r.get("error"):
            return jsonify({"ok": False, "error": r["error"]})

        # PrestaShop: PVP actual de este EAN (para refrescar columna y margen en vivo).
        # ps_en_tienda = True/False si PrestaShop respondió; None si no está disponible.
        ps_pvp = None
        ps_en_tienda = None
        if bc.prestashop_configurado():
            try:
                from prestashop_client import PrestashopClient
                existentes = PrestashopClient().comprobar_eans([ean]) or {}
                ps_en_tienda = ean in existentes
                if ps_en_tienda and isinstance(existentes.get(ean), dict):
                    ps_pvp = existentes[ean].get("precio")
            except Exception:  # noqa: BLE001  (PrestaShop caído/mantenimiento)
                ps_en_tienda = None

        from datetime import datetime, timezone
        return jsonify({"ok": True, "estado": r.get("estado"), "stock": r.get("stock"),
                        "ean_consultado": r.get("ean_consultado"),
                        "en_otro": (r.get("en_otro") or {}).get("proveedor"),
                        "precio_neto": r.get("precio_neto"), "coste_real": r.get("coste_real"),
                        "ps_pvp": ps_pvp, "ps_en_tienda": ps_en_tienda,
                        "ultima_revision": datetime.now(timezone.utc).isoformat()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})


@app.route("/actualizar-precio", methods=["POST"])
def actualizar_precio():
    """Cambia el PVP (con IVA) de un producto en PrestaShop, localizado por EAN."""
    data = request.get_json(silent=True) or {}
    ean = (data.get("ean") or "").strip()
    precio = data.get("precio")
    if not ean or precio in (None, ""):
        return jsonify({"ok": False, "error": "Faltan 'ean' y/o 'precio'."}), 400
    res = bc.actualizar_prestashop(ean=ean, precio=precio)
    if not res.get("ok"):
        return jsonify(res), 400
    try:
        pvp = round(float(str(precio).replace(",", ".")), 2)
    except (TypeError, ValueError):
        pvp = None
    return jsonify({"ok": True, "precio": pvp})


@app.route("/cambiar-proveedor", methods=["POST"])
def cambiar_proveedor():
    ean = (request.form.get("ean") or "").strip()
    actual = request.form.get("proveedor") or ""
    nuevo = request.form.get("nuevo") or ""
    if ean and actual and nuevo and actual != nuevo:
        if mon.cambiar_proveedor(ean, actual, nuevo):
            flash(f"«{ean}»: {actual} → {nuevo} (reasignado y recomprobado).", "ok")
        else:
            flash("No se pudo cambiar el proveedor.", "error")
    return redirect(request.referrer or url_for("index"))


@app.route("/pasar-otro-proveedor", methods=["POST"])
def pasar_otro_proveedor():
    """Pasa un lote de filas 'otro_proveedor' a su proveedor correcto (JSON).
    La página lo llama en bucle hasta que no quedan."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(mon.pasar_al_otro(proveedor=data.get("proveedor") or None))
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e), "movidos": 0})


@app.route("/add", methods=["POST"])
def add():
    ean = (request.form.get("ean") or "").strip()
    ean_exp = (request.form.get("ean_expositor") or "").strip() or None
    provs = request.form.getlist("proveedores")  # ['AZETA','Liderpapel']
    if not ean:
        flash("Introduce un EAN.", "error")
    elif not provs:
        flash("Elige al menos un proveedor.", "error")
    else:
        n = sum(1 for p in provs
                if mon.añadir(ean, p, origen="manual", ean_expositor=ean_exp))
        extra = f" (se consultará con {ean_exp})" if ean_exp else ""
        flash(f"Añadido «{ean}» a {n} proveedor(es){extra}.", "ok" if n else "error")
    return redirect(request.referrer or url_for("index"))


@app.route("/quitar", methods=["POST"])
def quitar():
    ean = (request.form.get("ean") or "").strip()
    proveedor = request.form.get("proveedor") or ""
    if ean and proveedor:
        mon.quitar(ean, proveedor)
        flash(f"Quitado «{ean}» ({proveedor}).", "ok")
    return redirect(request.referrer or url_for("index"))


@app.route("/comprobar", methods=["POST"])
def comprobar():
    """Lanza la comprobación EN SEGUNDO PLANO (la página muestra el progreso)."""
    proveedor = request.form.get("proveedor") or None
    estado = request.form.get("estado") or None
    r = mon.iniciar_comprobacion(proveedor=proveedor, estado=estado)
    if r.get("ok"):
        flash(f"Comprobación iniciada: {r['total']} productos. Puedes seguir usando la app; "
              f"el progreso se ve aquí arriba.", "ok")
    else:
        flash(r.get("error") or "No se pudo iniciar la comprobación.", "warn")
    return redirect(request.referrer or url_for("index"))


@app.route("/comprobacion/estado")
def comprobacion_estado():
    return jsonify({"ok": True, **mon.estado_comprobacion()})


@app.route("/comprobacion/cancelar", methods=["POST"])
def comprobacion_cancelar():
    return jsonify({"ok": mon.cancelar_comprobacion()})


@app.route("/sincronizar", methods=["POST"])
def sincronizar():
    try:
        r = mon.sincronizar_desde_prestashop()
        flash(f"Sincronizado con PrestaShop · {r['añadidos']} añadidos · "
              f"{r['actualizados']} actualizados · "
              f"{r.get('desactivados', 0)} desactivados (ya no están en la tienda).", "ok")
    except Exception as e:  # noqa: BLE001
        flash(f"No se pudo sincronizar con PrestaShop: {e}", "error")
    return redirect(url_for("index"))


@app.route("/editar", methods=["GET", "POST"])
def editar():
    if request.method == "POST":
        ean = (request.form.get("ean") or "").strip()
        proveedor = request.form.get("proveedor") or ""
        res = mon.editar(
            ean, proveedor,
            nuevo_nombre=request.form.get("nombre"),
            nuevo_ean=request.form.get("nuevo_ean"),
            nuevo_ean_expositor=request.form.get("ean_expositor"),
            empujar_ps=request.form.get("empujar_ps") == "on",
        )
        if res.get("ok"):
            flash("Cambios guardados" + (" y enviados a PrestaShop." if request.form.get("empujar_ps") == "on" else "."), "ok")
        else:
            flash(res.get("error") or "No se pudo guardar.", "error")
        return redirect(url_for("index"))

    ean = (request.args.get("ean") or "").strip()
    proveedor = request.args.get("proveedor") or ""
    v = mon.obtener(ean, proveedor) if ean and proveedor else None
    if not v:
        flash("Producto no encontrado.", "error")
        return redirect(url_for("index"))
    return render_template("editar.html", v=v)


@app.route("/api/historico")
def api_historico():
    ean = (request.args.get("ean") or "").strip()
    proveedor = request.args.get("proveedor") or ""
    return jsonify({"ok": True, "historico": mon.historico_de(ean, proveedor, 100)})
