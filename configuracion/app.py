"""
configuracion/app.py — Configuración central
=============================================
Un solo sitio para toda la configuración:
  - PrestaShop (compartida por la búsqueda unificada y AZETA): URL, token,
    categoría, proveedor AZETA, proveedor Liderpapel, impuestos, recargo.
  - Credenciales de AZETA.
  - Credenciales de Liderpapel (CS Papelería).

Cada bloque se guarda en su .env: los de PrestaShop/AZETA en el de la raíz
(config.py), los de Liderpapel en cspapeleria/.env (cspapeleria.config).
"""
from __future__ import annotations

from flask import Flask, flash, redirect, render_template, request, url_for

import config as cfg_root
from cspapeleria import config as cfg_cs

app = Flask(__name__)
app.secret_key = "config-central"

# Claves que van a cada .env
CLAVES_ROOT = ["AZETA_USER", "AZETA_PASSWORD", "PRESTASHOP_URL", "PRESTASHOP_TOKEN",
               "PRESTASHOP_CATEGORIA_DEFECTO", "PRESTASHOP_PROVEEDOR_AZETA",
               "PRESTASHOP_PROVEEDOR_CS", "PRESTASHOP_IMPUESTOS", "APLICAR_RECARGO"]
CLAVES_CS = ["CS_USER", "CS_PASS", "CS_IMPERSONATE", "CS_IVA_DEFAULT", "CS_APLICAR_RECARGO"]


def _leer_todo() -> dict:
    import os
    c = dict(cfg_root.leer_config())
    cs = dict(cfg_cs.leer_config())     # claves CS_* (del fichero)
    # Fallback a variables de entorno para las CS (útil en la nube sin fichero .env)
    for k in CLAVES_CS:
        if not cs.get(k) and os.environ.get(k):
            cs[k] = os.environ[k]
    c.update(cs)
    return c


def _opciones_prestashop():
    """(proveedores, impuestos, aviso) desde el módulo; degradación elegante."""
    if not cfg_root.prestashop_configurado():
        return [], [], None
    try:
        from prestashop_client import PrestashopClient
        op = PrestashopClient().obtener_opciones()
        return op.get("proveedores", []), op.get("impuestos", []), None
    except Exception as e:  # noqa: BLE001
        return [], [], str(e)


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Bloque raíz (AZETA + PrestaShop)
        cambios_root = {}
        for k in CLAVES_ROOT:
            if k == "APLICAR_RECARGO":
                cambios_root[k] = "1" if request.form.get("APLICAR_RECARGO") == "on" else "0"
            elif k == "PRESTASHOP_URL":
                cambios_root[k] = (request.form.get(k) or "").strip().rstrip("/")
            else:
                cambios_root[k] = (request.form.get(k) or "").strip()
        cfg_root.guardar_config(cambios_root)

        # Bloque Liderpapel (CS)
        cambios_cs = {}
        for k in CLAVES_CS:
            if k == "CS_APLICAR_RECARGO":
                cambios_cs[k] = "1" if request.form.get("CS_APLICAR_RECARGO") == "on" else "0"
            else:
                cambios_cs[k] = (request.form.get(k) or "").strip()
        cfg_cs.guardar_config(cambios_cs)

        flash("Configuración guardada y aplicada.", "ok")
        return redirect(url_for("index"))

    proveedores, impuestos, aviso_ps = _opciones_prestashop()
    return render_template("config.html", cfg=_leer_todo(),
                           ps_ok=cfg_root.prestashop_configurado(),
                           proveedores=proveedores, impuestos=impuestos, aviso_ps=aviso_ps)


@app.route("/probar", methods=["POST"])
def probar():
    try:
        from prestashop_client import PrestashopClient
        productos = PrestashopClient().listar_productos()
        flash(f"Conexión correcta con PrestaShop. {len(productos)} productos en la tienda.", "ok")
    except Exception as e:  # noqa: BLE001
        flash(f"No se pudo conectar con PrestaShop: {e}", "error")
    return redirect(url_for("index"))
