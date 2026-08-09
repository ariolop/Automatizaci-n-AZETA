"""
etiquetas_comun.py — Persistencia en Supabase del catálogo de etiquetas
========================================================================
La pantalla /etiquetas guarda aquí los productos que se van seleccionando,
para ir construyendo un "catálogo" (sobre todo de los que NO tienen EAN13).

La tabla es un ESPEJO de la selección:
  - marcar un producto  -> upsert (guardar_muchos)  por id_prestashop
  - desmarcar           -> delete (quitar_muchos)   por id_prestashop
  - al abrir la pantalla la selección se carga desde aquí (listar)

Tabla (ver supabase_catalogo_sin_ean_schema.sql):
  - catalogo_sin_ean : id_prestashop (único), ean13, tiene_ean, nombre,
                       fabricante, precio, activo_ps, creado, actualizado.

Reutiliza SUPABASE_URL / SUPABASE_KEY (mismas credenciales que el resto del
proyecto). Si no están configuradas, activo() es False y todo es no-op.
"""
from __future__ import annotations

import os
import re

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
TABLA = os.environ.get("SUPABASE_CATALOGO_TABLE", "catalogo_sin_ean").strip() or "catalogo_sin_ean"
_TIMEOUT = 12

_EAN_RE = re.compile(r"^\d{12,13}$")


def activo() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers(extra: dict | None = None) -> dict:
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _url() -> str:
    return f"{SUPABASE_URL}/rest/v1/{TABLA}"


def _es_ean13(codigo) -> bool:
    return bool(_EAN_RE.match(str(codigo or "").strip()))


def _fila_norm(p: dict) -> dict | None:
    """Normaliza un producto del front al esquema de la tabla.

    Devuelve None si no hay id_prestashop (no se puede identificar la fila).
    """
    try:
        idp = int(p.get("id") if p.get("id") is not None else p.get("id_prestashop"))
    except (TypeError, ValueError):
        return None
    ean = str(p.get("ean13") or p.get("ean") or "").strip()
    precio = p.get("precio")
    try:
        precio = float(precio) if precio is not None and precio != "" else None
    except (TypeError, ValueError):
        precio = None
    return {
        "id_prestashop": idp,
        "ean13": ean or None,
        "tiene_ean": _es_ean13(ean),
        "nombre": p.get("nombre") or None,
        "fabricante": (p.get("fabricante") or "").strip() or None,
        "precio": precio,
        "activo_ps": bool(p.get("activo")) if p.get("activo") is not None else None,
    }


def guardar_muchos(productos: list[dict]) -> bool:
    """Upsert (insert/update) por id_prestashop de una lista de productos."""
    if not activo() or not productos:
        return False
    payload = [f for f in (_fila_norm(p) for p in productos) if f]
    if not payload:
        return False
    ok = True
    LOTE = 200
    for i in range(0, len(payload), LOTE):
        trozo = payload[i:i + LOTE]
        try:
            r = requests.post(
                _url(),
                params={"on_conflict": "id_prestashop"},
                json=trozo,
                headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                timeout=_TIMEOUT,
            )
            if r.status_code >= 300:
                print(f"[etiquetas] guardar_muchos {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:  # noqa: BLE001
            print(f"[etiquetas] no se pudieron guardar productos: {e}")
            ok = False
    return ok


def quitar_muchos(ids: list) -> bool:
    """Borra del catálogo las filas cuyos id_prestashop se indican."""
    if not activo():
        return False
    limpios = []
    for i in ids or []:
        try:
            limpios.append(int(i))
        except (TypeError, ValueError):
            continue
    if not limpios:
        return False
    ok = True
    LOTE = 200
    for i in range(0, len(limpios), LOTE):
        trozo = limpios[i:i + LOTE]
        en = ",".join(str(x) for x in trozo)
        try:
            r = requests.delete(
                _url(),
                params={"id_prestashop": f"in.({en})"},
                headers=_headers({"Prefer": "return=minimal"}),
                timeout=_TIMEOUT,
            )
            if r.status_code >= 300:
                print(f"[etiquetas] quitar_muchos {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:  # noqa: BLE001
            print(f"[etiquetas] no se pudieron quitar productos: {e}")
            ok = False
    return ok


def listar(limite: int = 10000) -> list[dict]:
    """Devuelve el catálogo guardado (para precargar la selección)."""
    if not activo():
        return []
    try:
        r = requests.get(
            _url(),
            params={"select": "id_prestashop,ean13,tiene_ean,nombre,fabricante,precio,activo_ps",
                    "order": "fabricante.asc,nombre.asc", "limit": str(limite)},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        return r.json() if r.status_code < 300 else []
    except Exception:  # noqa: BLE001
        return []
