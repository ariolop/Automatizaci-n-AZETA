"""
competencia_app/app.py — Precios de la competencia
==================================================
- Histórico (runs, comparativa tu-PVP-vs-competencia, evolución de precios):
  se consulta desde CUALQUIER sitio (lee de Supabase).
- Ejecución (scraping de cada EAN): SOLO en local (donde no está la variable
  RENDER, o con COMPETENCIA_LOCAL=1). En el servidor los controles se ocultan.
"""
from __future__ import annotations

import os
import re
import threading
import uuid

from flask import Flask, jsonify, render_template, request

import competencia_scraper as scr
import competencia_comun as cc
import busqueda_comun as bc

app = Flask(__name__)

_JOB: dict = {"running": False}
_LOCK = threading.Lock()


def es_local() -> bool:
    """Local si se fuerza con COMPETENCIA_LOCAL=1 o si NO estamos en Render."""
    if os.environ.get("COMPETENCIA_LOCAL") == "1":
        return True
    return os.environ.get("RENDER") is None


def _tiendas_por_nombre(nombres):
    if not nombres:
        return scr.TIENDAS
    s = set(nombres)
    return [t for t in scr.TIENDAS if t["nombre"] in s]


def _mapa_pvp() -> dict:
    """ean -> {pvp, nombre} desde el listado de PrestaShop (incluye PVP)."""
    m = {}
    if not bc.prestashop_configurado():
        return m
    try:
        from prestashop_client import PrestashopClient
        for p in PrestashopClient().listar_productos():
            ean = str(p.get("ean13") or "").strip()
            if ean:
                m[ean] = {"pvp": p.get("precio"), "nombre": p.get("nombre")}
    except Exception:  # noqa: BLE001
        pass
    return m


@app.route("/")
def index():
    return render_template(
        "competencia.html",
        local=es_local(),
        runs=cc.listar_runs(30),
        supabase_ok=cc.activo(),
        ps_ok=bc.prestashop_configurado(),
        tiendas=[t["nombre"] for t in scr.TIENDAS],
        job_running=_JOB.get("running", False),
    )


# --------------------------------------------------------------------------- #
# Ejecución (solo local)
# --------------------------------------------------------------------------- #
@app.route("/iniciar", methods=["POST"])
def iniciar():
    if not es_local():
        return jsonify({"error": "La ejecución solo está disponible en local."}), 403
    with _LOCK:
        if _JOB.get("running"):
            return jsonify({"error": "Ya hay una revisión en curso."}), 409

    fuente = request.form.get("fuente", "lista")
    try:
        limite = int(request.form.get("limite") or 0)
    except ValueError:
        limite = 0
    try:
        pausa = float(request.form.get("pausa") or scr.PAUSA_ENTRE)
    except ValueError:
        pausa = scr.PAUSA_ENTRE
    tiendas = _tiendas_por_nombre(request.form.getlist("tiendas"))

    mapa = _mapa_pvp()
    if fuente == "catalogo":
        eans = list(mapa.keys())
    elif fuente == "vigilados":
        import monitor_comun as mon
        vig = {str(v.get("ean")) for v in mon.listar(limite=10000) if v.get("ean")}
        eans = [e for e in mapa.keys() if e in vig] if mapa else list(vig)
    else:  # lista pegada
        eans = list(dict.fromkeys(re.findall(r"\d{8,14}", request.form.get("texto") or "")))

    if limite > 0:
        eans = eans[:limite]
    if not eans:
        return jsonify({"error": "No hay EANs que revisar."}), 400

    run_id = uuid.uuid4().hex[:12]
    threading.Thread(target=_ejecutar, args=(run_id, eans, mapa, tiendas, pausa), daemon=True).start()
    return jsonify({"run_id": run_id, "total": len(eans)})


def _ejecutar(run_id, eans, mapa, tiendas, pausa):
    import requests as rq
    with _LOCK:
        _JOB.clear()
        _JOB.update(running=True, cancel=False, total=len(eans), done=0, ok=0,
                    run_id=run_id, current="")
    cc.crear_run(run_id, [t["nombre"] for t in tiendas], len(eans))
    ses = rq.Session()
    n_ok = 0
    for ean in eans:
        with _LOCK:
            if _JOB.get("cancel"):
                break
            _JOB["current"] = ean
        try:
            filas = scr.buscar_ean(ean, session=ses, tiendas=tiendas, pausa=pausa)
        except Exception:  # noqa: BLE001
            filas = []
        info = mapa.get(str(ean), {})
        for f in filas:
            f["PVP"] = info.get("pvp")
            f["NombrePS"] = info.get("nombre")
        cc.guardar_filas(run_id, filas)
        okc = sum(1 for f in filas if f.get("Estado") == "OK")
        n_ok += okc
        with _LOCK:
            _JOB["done"] += 1
            _JOB["ok"] += okc
    estado = "parado" if _JOB.get("cancel") else "terminado"
    cc.actualizar_run(run_id, n_ok=n_ok, estado=estado)
    with _LOCK:
        _JOB["running"] = False
        _JOB["estado"] = estado


@app.route("/estado")
def estado():
    with _LOCK:
        return jsonify(dict(_JOB))


@app.route("/parar", methods=["POST"])
def parar():
    with _LOCK:
        _JOB["cancel"] = True
    return jsonify({"ok": True})


@app.route("/lookup", methods=["POST"])
def lookup():
    """Consulta rápida de un EAN (solo local; no guarda salvo que se pida)."""
    if not es_local():
        return jsonify({"error": "Solo disponible en local."}), 403
    data = request.get_json(silent=True) or {}
    ean = (data.get("ean") or request.form.get("ean") or "").strip()
    if not ean:
        return jsonify({"error": "Falta el EAN."}), 400
    filas = scr.buscar_ean(ean, tiendas=scr.TIENDAS, pausa=0)
    info = _mapa_pvp().get(ean, {})
    return jsonify({"ok": True, "ean": ean, "pvp": info.get("pvp"),
                    "nombre_ps": info.get("nombre"), "filas": filas})


# --------------------------------------------------------------------------- #
# Consulta del histórico (en cualquier sitio)
# --------------------------------------------------------------------------- #
@app.route("/run/<run_id>")
def ver_run(run_id):
    filas = cc.resultados_de_run(run_id)
    return jsonify({"ok": True, "comparativa": cc.comparativa(filas), "n_filas": len(filas)})


@app.route("/api/historico")
def api_historico():
    ean = (request.args.get("ean") or "").strip()
    if not ean:
        return jsonify({"ok": False, "error": "Falta ean"}), 400
    return jsonify({"ok": True, "ean": ean, "historico": cc.historico_de_ean(ean)})
