"""
lote_comun.py — Lote unificado (AZETA + Liderpapel)
===================================================
Procesa una lista de EANs (de texto o de fichero CSV/Excel) contra los
proveedores elegidos, en segundo plano y con sondeo de estado. Opciones:
  - vigilar        : añade los encontrados al monitor unificado.
  - publicar_azeta : publica en PrestaShop los encontrados en AZETA.
  - publicar_cs    : publica en PrestaShop los encontrados en Liderpapel.

Reutiliza busqueda_comun (búsqueda/comparación/publicación), monitor_comun
(vigilar) y las funciones de extracción de EANs del lote de AZETA.
"""
from __future__ import annotations

import io
import threading
import time
import uuid

import busqueda_comun as bc
import monitor_comun as mon
# Reutilizamos la extracción de EANs ya probada (texto / CSV / Excel por columna).
from lote import extraer_eans, extraer_eans_de_archivo  # noqa: F401

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
PAUSA = 0.4   # pequeña pausa entre EANs para no saturar los portales


def crear_job(eans: list[str], opciones: dict) -> str:
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "estado": "en_curso", "total": len(eans), "procesados": 0,
           "items": [], "opciones": opciones,
           "resumen": {"encontrados": 0, "vigilados": 0, "publicados": 0, "errores": 0}}
    with _LOCK:
        _JOBS[jid] = job
    threading.Thread(target=_procesar, args=(jid, eans, opciones), daemon=True).start()
    return jid


def obtener_job(jid: str) -> dict | None:
    with _LOCK:
        return _JOBS.get(jid)


def _procesar(jid: str, eans: list[str], op: dict):
    job = _JOBS[jid]
    con_azeta = bool(op.get("azeta"))
    con_cs = bool(op.get("cs"))
    for ean in eans:
        try:
            res = bc.buscar(ean, con_azeta=con_azeta, con_cs=con_cs)
        except Exception as e:  # noqa: BLE001
            res = {"ean": ean, "encontrado": False, "azeta": None, "cs": None,
                   "comparacion": {}, "error": str(e)}
        azeta, cs = res.get("azeta"), res.get("cs")
        publicados = []

        # Vigilar (monitor unificado)
        if op.get("vigilar"):
            if azeta and azeta.get("encontrado"):
                mon.añadir(ean, "AZETA", nombre=azeta.get("nombre"), origen="manual")
                job["resumen"]["vigilados"] += 1
            if cs and cs.get("encontrado"):
                mon.añadir(ean, "Liderpapel", nombre=cs.get("nombre"), origen="manual",
                           stock=cs.get("stock"), precio_neto=cs.get("precio_neto"),
                           coste_real=cs.get("coste_real"))
                job["resumen"]["vigilados"] += 1

        # Publicar en PrestaShop (opcional, por proveedor)
        for flag, prov, lado in (("publicar_azeta", "azeta", azeta), ("publicar_cs", "cs", cs)):
            if op.get(flag) and lado and lado.get("encontrado"):
                try:
                    if bc.ya_en_prestashop(ean):
                        publicados.append({"prov": prov, "estado": "ya_existe"})
                    else:
                        r = bc.publicar(prov, ean)
                        if r.get("ok"):
                            publicados.append({"prov": prov, "estado": "publicado", "id": r.get("id_product")})
                            job["resumen"]["publicados"] += 1
                        else:
                            publicados.append({"prov": prov, "estado": "error", "error": r.get("error")})
                            job["resumen"]["errores"] += 1
                except Exception as e:  # noqa: BLE001
                    publicados.append({"prov": prov, "estado": "error", "error": str(e)})
                    job["resumen"]["errores"] += 1

        if res.get("encontrado"):
            job["resumen"]["encontrados"] += 1

        item = {"ean": ean, "azeta": azeta, "cs": cs,
                "mas_barato": (res.get("comparacion") or {}).get("mas_barato"),
                "publicados": publicados}
        with _LOCK:
            job["items"].append(item)
            job["procesados"] += 1
        time.sleep(PAUSA)
    job["estado"] = "terminado"


def csv_de_job(jid: str) -> str:
    import csv
    job = obtener_job(jid) or {"items": []}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["EAN", "AZETA_encontrado", "AZETA_nombre", "AZETA_precio_neto", "AZETA_coste_real",
                "Lider_encontrado", "Lider_nombre", "Lider_precio_neto", "Lider_coste_real",
                "mas_barato"])
    for it in job["items"]:
        a = it.get("azeta") or {}
        c = it.get("cs") or {}
        w.writerow([
            it.get("ean", ""),
            "si" if a.get("encontrado") else "no", a.get("nombre") or "",
            a.get("precio_neto") if a.get("precio_neto") is not None else "",
            a.get("coste_real") if a.get("coste_real") is not None else "",
            "si" if c.get("encontrado") else "no", c.get("nombre") or "",
            c.get("precio_neto") if c.get("precio_neto") is not None else "",
            c.get("coste_real") if c.get("coste_real") is not None else "",
            it.get("mas_barato") or "",
        ])
    return buf.getvalue()
