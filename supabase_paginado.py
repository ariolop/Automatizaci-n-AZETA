"""
supabase_paginado.py — Lecturas completas de Supabase (PostgREST) por bloques
=============================================================================
Supabase devuelve como máximo 1000 filas por petición (ajuste "Max rows" del
proyecto) aunque se pida `limit=5000`, y lo hace SIN avisar. Este ayudante pide
bloques con la cabecera `Range` hasta traer todas las filas (o `maximo`).

    filas = get_todos(url, {"select": "*", "order": "id.asc"}, headers)

- El `order` debe ser determinista (termina en una columna única, p. ej. `id`),
  si no, al paginar se pueden repetir o saltar filas.
- Devuelve la lista de filas, o None si alguna petición falla (para que quien
  llama distinga "no hay filas" de "no se pudo leer").
"""
from __future__ import annotations

import requests

BLOQUE = 1000


def get_todos(url: str, params: dict, headers: dict, maximo: int | None = None,
              timeout: int = 15, bloque: int = BLOQUE) -> list[dict] | None:
    params = {k: v for k, v in params.items() if k != "limit"}
    filas: list[dict] = []
    desde = 0
    total = None
    while True:
        tam = bloque if maximo is None else min(bloque, maximo - len(filas))
        if tam <= 0:
            break
        hasta = desde + tam - 1
        h = dict(headers)
        h.update({"Range-Unit": "items", "Range": f"{desde}-{hasta}"})
        if desde == 0:
            h["Prefer"] = "count=exact"     # para saber el total en el 1er bloque
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            print(f"[supabase] lectura fallida ({url}): {e}")
            return None
        if r.status_code == 416:          # rango fuera del total: no hay más
            break
        if r.status_code >= 300:
            print(f"[supabase] lectura {r.status_code} ({url}): {r.text[:200]}")
            return None
        trozo = r.json() or []
        filas.extend(trozo)
        # Menos filas de las pedidas -> no hay más. (Si el servidor tiene un
        # "Max rows" menor que el bloque, también devuelve menos: lo detectamos
        # por Content-Range y seguimos.)
        if desde == 0:
            total = _total(r.headers.get("Content-Range", ""))
        desde += len(trozo)
        if not trozo:
            break
        if total is not None:
            if desde >= total:
                break
        elif len(trozo) < tam:
            break
    return filas


def _total(content_range: str) -> int | None:
    # "0-999/8234" o "0-999/*"
    if "/" not in content_range:
        return None
    t = content_range.split("/")[-1]
    return int(t) if t.isdigit() else None
