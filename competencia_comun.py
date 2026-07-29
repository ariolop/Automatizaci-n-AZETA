"""
competencia_comun.py — Persistencia en Supabase de los precios de la competencia
================================================================================
El scraping se ejecuta en LOCAL (competencia_scraper) y va volcando aquí los
resultados; la consulta del histórico se hace desde cualquier sitio (incluido
Render), leyendo de Supabase.

Tablas (ver supabase_competencia_schema.sql):
  - precios_competencia_runs : metadatos de cada revisión.
  - precios_competencia      : histórico largo (EAN + tienda + run) con tu PVP.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
TABLA = os.environ.get("SUPABASE_COMPETENCIA_TABLE", "precios_competencia").strip() or "precios_competencia"
TABLA_RUNS = os.environ.get("SUPABASE_COMPETENCIA_RUNS_TABLE", "precios_competencia_runs").strip() or "precios_competencia_runs"
_TIMEOUT = 12


def activo() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers(extra: dict | None = None) -> dict:
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _url(tabla: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{tabla}"


# --------------------------------------------------------------------------- #
# Ejecuciones (runs)
# --------------------------------------------------------------------------- #
def crear_run(run_id: str, tiendas: list[str], n_eans: int = 0) -> bool:
    if not activo():
        return False
    fila = {"run_id": run_id, "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "n_eans": n_eans, "n_tiendas": len(tiendas),
            "tiendas": ", ".join(tiendas), "estado": "en_curso"}
    try:
        r = requests.post(_url(TABLA_RUNS), json=fila,
                        headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        return r.status_code < 300
    except Exception as e:  # noqa: BLE001
        print(f"[competencia] no se pudo crear run: {e}")
    return False


def actualizar_run(run_id: str, **campos) -> bool:
    if not activo():
        return False
    try:
        r = requests.patch(_url(TABLA_RUNS), params={"run_id": f"eq.{run_id}"},
                         json=campos, headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        return r.status_code < 300
    except Exception as e:  # noqa: BLE001
        print(f"[competencia] no se pudo actualizar run: {e}")
    return False


def listar_runs(limite: int = 50) -> list[dict]:
    if not activo():
        return []
    try:
        r = requests.get(_url(TABLA_RUNS),
                        params={"select": "*", "order": "ts.desc", "limit": str(limite)},
                        headers=_headers(), timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else []
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# Filas de precios
# --------------------------------------------------------------------------- #
def _fila_norm(run_id: str, f: dict) -> dict:
    """Normaliza una fila del scraper (claves EAN/Tienda/…) al esquema de la tabla."""
    return {
        "run_id": run_id,
        "ean": str(f.get("EAN") or f.get("ean") or ""),
        "tienda": f.get("Tienda") or f.get("tienda"),
        "nombre": f.get("Nombre") or f.get("nombre"),
        "precio": f.get("PrecioNum") if f.get("PrecioNum") is not None else None,
        "estado": f.get("Estado") or f.get("estado"),
        "url": f.get("URL") or f.get("url"),
        "pvp": f.get("PVP") if f.get("PVP") is not None else f.get("pvp"),
        "nombre_ps": f.get("NombrePS") or f.get("nombre_ps"),
    }


def guardar_filas(run_id: str, filas: list[dict]) -> bool:
    """Inserta filas (en lotes). Todas con las mismas claves (requisito PostgREST)."""
    if not activo() or not filas:
        return False
    payload = [_fila_norm(run_id, f) for f in filas]
    LOTE = 200
    ok = True
    for i in range(0, len(payload), LOTE):
        trozo = payload[i:i + LOTE]
        try:
            r = requests.post(_url(TABLA), json=trozo,
                            headers=_headers({"Prefer": "return=minimal"}), timeout=30)
            if r.status_code >= 300:
                print(f"[competencia] guardar_filas {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:  # noqa: BLE001
            print(f"[competencia] no se pudieron guardar filas: {e}")
            ok = False
    return ok


def resultados_de_run(run_id: str, limite: int = 5000) -> list[dict]:
    if not activo():
        return []
    try:
        r = requests.get(_url(TABLA),
                        params={"select": "*", "run_id": f"eq.{run_id}",
                                "order": "ean.asc,tienda.asc", "limit": str(limite)},
                        headers=_headers(), timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else []
    except Exception:  # noqa: BLE001
        return []


def historico_de_ean(ean: str, limite: int = 500) -> list[dict]:
    """Todas las observaciones de un EAN a lo largo del tiempo (para el gráfico)."""
    if not activo():
        return []
    try:
        r = requests.get(_url(TABLA),
                        params={"select": "ts,tienda,precio,pvp,estado,url",
                                "ean": f"eq.{ean}", "order": "ts.asc", "limit": str(limite)},
                        headers=_headers(), timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else []
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# Comparativa (tu PVP vs competencia) a partir de las filas de un run
# --------------------------------------------------------------------------- #
def comparativa(filas: list[dict]) -> list[dict]:
    """Agrupa por EAN: tu PVP, el competidor más barato y la diferencia %."""
    por_ean: dict[str, dict] = {}
    for f in filas:
        ean = f.get("ean")
        if not ean:
            continue
        g = por_ean.setdefault(ean, {"ean": ean, "pvp": f.get("pvp"),
                                     "nombre_ps": f.get("nombre_ps"), "precios": []})
        if f.get("pvp") is not None and g["pvp"] is None:
            g["pvp"] = f.get("pvp")
        if f.get("estado") == "OK" and f.get("precio") is not None:
            g["precios"].append({"tienda": f.get("tienda"), "precio": f.get("precio"), "url": f.get("url")})
    salida = []
    for g in por_ean.values():
        mas_barato = min(g["precios"], key=lambda x: x["precio"]) if g["precios"] else None
        dif_pct = None
        if mas_barato and g.get("pvp"):
            try:
                dif_pct = round((g["pvp"] - mas_barato["precio"]) / g["pvp"] * 100, 1)
            except (TypeError, ZeroDivisionError):
                dif_pct = None
        salida.append({
            "ean": g["ean"], "nombre_ps": g.get("nombre_ps"), "pvp": g.get("pvp"),
            "n_tiendas": len(g["precios"]),
            "mas_barato": mas_barato["precio"] if mas_barato else None,
            "tienda_mas_barata": mas_barato["tienda"] if mas_barato else None,
            "diferencia_pct": dif_pct,  # >0: tú más caro; <0: tú más barato
            "precios": sorted(g["precios"], key=lambda x: x["precio"]),
        })
    salida.sort(key=lambda x: (x["diferencia_pct"] is None, -(x["diferencia_pct"] or 0)))
    return salida
