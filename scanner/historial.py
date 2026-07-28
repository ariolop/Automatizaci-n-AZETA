"""
historial.py — Registro de escaneos en Supabase (opcional).

Usa la API REST de Supabase (PostgREST); solo necesita dos variables de entorno:
    SUPABASE_URL   ->  https://xxxxxxxx.supabase.co
    SUPABASE_KEY   ->  la API key (usa la 'service_role' — es de servidor, secreta)
    SUPABASE_TABLE ->  (opcional) nombre de la tabla; por defecto 'escaneos'

Si esas variables no están definidas, el registro queda desactivado y la app
funciona igual (no guarda nada). El envío se hace en segundo plano y nunca
interrumpe ni ralentiza la búsqueda.

La tabla se crea en Supabase con el SQL de 'supabase_schema.sql'.
"""
from __future__ import annotations

import os
import threading

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
TABLA = os.environ.get("SUPABASE_TABLE", "escaneos").strip() or "escaneos"
_TIMEOUT = 8


def activo() -> bool:
    """True si Supabase está configurado (URL + KEY)."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _fila(ean: str, azeta: dict, cs: dict, comparacion: dict) -> dict:
    """Construye la fila a insertar a partir de los dicts normalizados.

    Además de los campos "planos" (para el historial y consultas), guarda un
    'payload' con el resultado completo, que sirve de CACHÉ: al reescanear el
    mismo EAN se devuelve tal cual, sin volver a consultar a los proveedores.
    """
    encontrado = bool(azeta.get("encontrado") or cs.get("encontrado"))
    return {
        "ean": ean,
        "encontrado": encontrado,
        "azeta_encontrado": bool(azeta.get("encontrado")),
        "azeta_nombre": azeta.get("nombre"),
        "azeta_precio_neto": azeta.get("precio_neto"),
        "azeta_coste": azeta.get("coste_real"),
        "azeta_pvp": azeta.get("pvp"),
        "azeta_disponible": azeta.get("disponible"),
        "cs_encontrado": bool(cs.get("encontrado")),
        "cs_nombre": cs.get("nombre"),
        "cs_precio_neto": cs.get("precio_neto"),
        "cs_coste": cs.get("coste_real"),
        "cs_disponible": cs.get("disponible"),
        "mas_barato": comparacion.get("mas_barato"),
        # Snapshot completo para la caché (columna jsonb)
        "payload": {"encontrado": encontrado, "azeta": azeta, "cs": cs,
                    "comparacion": comparacion},
    }


def registrar_async(ean: str, azeta: dict, cs: dict, comparacion: dict) -> None:
    """Inserta el escaneo en segundo plano (best-effort). No lanza excepciones."""
    if not activo():
        return
    fila = _fila(ean, azeta, cs, comparacion)
    threading.Thread(target=_enviar, args=(fila,), daemon=True).start()


def buscar_cache(ean: str) -> dict | None:
    """Devuelve el último escaneo guardado de ese EAN que fue ENCONTRADO, como
    {'payload': {...}, 'creado_at': ...}, o None si no hay caché o falla."""
    if not activo():
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABLA}",
            params={
                "select": "payload,creado_at",
                "ean": f"eq.{ean}",
                "encontrado": "eq.true",
                "order": "creado_at.desc",
                "limit": "1",
            },
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if r.status_code < 300:
            filas = r.json()
            if filas and filas[0].get("payload"):
                return filas[0]
        else:
            print(f"[historial] Supabase (cache) {r.status_code}: {r.text[:200]}")
    except Exception as e:                       # noqa: BLE001
        print(f"[historial] no se pudo leer la caché: {e}")
    return None


def _enviar(fila: dict) -> None:
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{TABLA}",
            json=fila,
            headers=_headers({"Prefer": "return=minimal"}),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 300:
            print(f"[historial] Supabase respondió {r.status_code}: {r.text[:200]}")
    except Exception as e:                       # noqa: BLE001
        print(f"[historial] no se pudo registrar el escaneo: {e}")


def ultimos(limite: int = 100) -> list[dict]:
    """Devuelve los últimos escaneos (más recientes primero). [] si falla."""
    if not activo():
        return []
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABLA}",
            params={"select": "*", "order": "creado_at.desc", "limit": str(limite)},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if r.status_code < 300:
            return r.json()
        print(f"[historial] Supabase respondió {r.status_code}: {r.text[:200]}")
    except Exception as e:                       # noqa: BLE001
        print(f"[historial] no se pudo leer el historial: {e}")
    return []
