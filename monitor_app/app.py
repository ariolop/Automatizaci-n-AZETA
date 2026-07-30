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
        from datetime import datetime, timezone
        return jsonify({"ok": True, "estado": r.get("estado"), "stock": r.get("stock"),
                        "precio_neto": r.get("precio_neto"), "coste_real": r.get("coste_real"),
                        "ultima_revision": datetime.now(timezone.utc).isoformat()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})


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
        r = mon.sincronizar_desde_prestashop()
        flash(f"Sincronizado con PrestaShop · {r['añadidos']} añadidos · "
              f"{r['actualizados']} actualizados (EAN/nombre).", "ok")
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
