"""
monitor_comun.py — Monitor unificado (AZETA + Liderpapel) sobre Supabase
========================================================================
Un único monitor para los dos proveedores. Cada fila vigilada se identifica por
la pareja (ean, proveedor), de modo que el MISMO EAN puede estar vigilado en
AZETA y en Liderpapel a la vez, cada uno con su estado/stock/precio.

Persistencia: Supabase (PostgREST), mismo patrón que el historial del escáner.
Variables de entorno:
    SUPABASE_URL, SUPABASE_KEY  (service_role)
    SUPABASE_VIGILADOS_TABLE       (opcional, por defecto 'vigilados')
    SUPABASE_VIGILADOS_HIST_TABLE  (opcional, por defecto 'vigilados_historico')

Crea las tablas con 'supabase_monitor_schema.sql'.
La comprobación de estado reutiliza busqueda_comun (mismos scrapers y normalizado).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import requests

import busqueda_comun as bc

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
TABLA = os.environ.get("SUPABASE_VIGILADOS_TABLE", "vigilados").strip() or "vigilados"
TABLA_HIST = os.environ.get("SUPABASE_VIGILADOS_HIST_TABLE", "vigilados_historico").strip() or "vigilados_historico"
_TIMEOUT = 10

PROVEEDORES = ("AZETA", "Liderpapel")


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


def _url(tabla: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{tabla}"


# --------------------------------------------------------------------------- #
# Normalización de proveedor y cálculo de estado
# --------------------------------------------------------------------------- #
def normalizar_proveedor(p: str) -> str:
    p = (p or "").strip().lower()
    if p in ("azeta",):
        return "AZETA"
    if p in ("cs", "liderpapel", "lider", "comercial del sur"):
        return "Liderpapel"
    return (p or "").strip().title()


def _estado_desde_ficha(norm: dict) -> str:
    """disponible / agotado / desaparecido a partir de un dict normalizado."""
    if norm.get("encontrado") and norm.get("disponible"):
        return "disponible"
    if norm.get("encontrado"):
        return "agotado"
    return "desaparecido"


# --------------------------------------------------------------------------- #
# Lectura
# --------------------------------------------------------------------------- #
def listar(proveedor: str | None = None, estado: str | None = None,
           solo_activos: bool = False, limite: int = 2000) -> list[dict]:
    if not activo():
        return []
    params = {"select": "*", "order": "nombre.asc.nullslast", "limit": str(limite)}
    if proveedor:
        params["proveedor"] = f"eq.{normalizar_proveedor(proveedor)}"
    if estado:
        params["estado"] = f"eq.{estado}"
    if solo_activos:
        params["activo"] = "eq.true"
    try:
        r = requests.get(_url(TABLA), params=params, headers=_headers(), timeout=_TIMEOUT)
        if r.status_code < 300:
            return r.json()
        print(f"[monitor] Supabase listar {r.status_code}: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo listar: {e}")
    return []


def resumen() -> dict:
    """Conteos por proveedor y de incidencias (agotado/desaparecido)."""
    filas = listar(solo_activos=True)
    res = {"total": 0, "por_proveedor": {}, "incidencias": 0}
    for f in filas:
        res["total"] += 1
        prov = f.get("proveedor") or "?"
        res["por_proveedor"][prov] = res["por_proveedor"].get(prov, 0) + 1
        if f.get("estado") in ("agotado", "desaparecido"):
            res["incidencias"] += 1
    return res


def historico_de(ean: str, proveedor: str, limite: int = 100) -> list[dict]:
    if not activo():
        return []
    params = {
        "select": "*",
        "ean": f"eq.{ean}",
        "proveedor": f"eq.{normalizar_proveedor(proveedor)}",
        "order": "ts.desc",
        "limit": str(limite),
    }
    try:
        r = requests.get(_url(TABLA_HIST), params=params, headers=_headers(), timeout=_TIMEOUT)
        if r.status_code < 300:
            return r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo leer histórico: {e}")
    return []


# --------------------------------------------------------------------------- #
# Escritura
# --------------------------------------------------------------------------- #
def añadir(ean: str, proveedor: str, nombre: str | None = None,
           origen: str = "manual", id_prestashop=None,
           estado: str | None = None, stock=None,
           precio_neto=None, coste_real=None) -> bool:
    """Alta (o actualización) de un vigilado. Upsert por (ean, proveedor)."""
    if not activo() or not ean:
        return False
    fila = {"ean": str(ean), "proveedor": normalizar_proveedor(proveedor),
            "origen": origen, "activo": True}
    if nombre is not None:
        fila["nombre"] = nombre
    if id_prestashop is not None:
        fila["id_prestashop"] = id_prestashop
    if estado is not None:
        fila["estado"] = estado
    if stock is not None:
        fila["stock"] = stock
    if precio_neto is not None:
        fila["precio_neto"] = precio_neto
    if coste_real is not None:
        fila["coste_real"] = coste_real
    try:
        r = requests.post(
            _url(TABLA),
            params={"on_conflict": "ean,proveedor"},
            json=fila,
            headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            timeout=_TIMEOUT,
        )
        if r.status_code < 300:
            return True
        print(f"[monitor] Supabase añadir {r.status_code}: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo añadir: {e}")
    return False


def quitar(ean: str, proveedor: str) -> bool:
    if not activo():
        return False
    params = {"ean": f"eq.{ean}", "proveedor": f"eq.{normalizar_proveedor(proveedor)}"}
    try:
        r = requests.delete(_url(TABLA), params=params,
                            headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        return r.status_code < 300
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo quitar: {e}")
    return False


def _patch(ean: str, proveedor: str, cambios: dict) -> bool:
    params = {"ean": f"eq.{ean}", "proveedor": f"eq.{normalizar_proveedor(proveedor)}"}
    try:
        r = requests.patch(_url(TABLA), params=params, json=cambios,
                          headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        return r.status_code < 300
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo actualizar: {e}")
    return False


def _insertar_historico(fila: dict) -> None:
    try:
        requests.post(_url(TABLA_HIST), json=fila,
                     headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo registrar histórico: {e}")


def actualizar_estado(ean: str, proveedor: str, estado: str, nombre=None,
                      stock=None, precio_neto=None, coste_real=None) -> None:
    from datetime import datetime, timezone
    prov = normalizar_proveedor(proveedor)
    cambios = {"estado": estado, "ultima_revision": datetime.now(timezone.utc).isoformat()}
    if nombre is not None:
        cambios["nombre"] = nombre
    if stock is not None:
        cambios["stock"] = stock
    if precio_neto is not None:
        cambios["precio_neto"] = precio_neto
    if coste_real is not None:
        cambios["coste_real"] = coste_real
    _patch(ean, prov, cambios)
    _insertar_historico({"ean": str(ean), "proveedor": prov, "estado": estado,
                         "stock": stock, "precio_neto": precio_neto,
                         "coste_real": coste_real, "nombre": nombre})


# --------------------------------------------------------------------------- #
# Comprobación de estado (reutiliza busqueda_comun)
# --------------------------------------------------------------------------- #
_pool = ThreadPoolExecutor(max_workers=6)


def _comprobar_uno(v: dict) -> dict:
    ean = v.get("ean")
    prov = normalizar_proveedor(v.get("proveedor"))
    estado_previo = v.get("estado")
    try:
        if prov == "AZETA":
            norm = bc.norm_azeta(bc.buscar_azeta(ean))
        else:
            norm = bc.norm_cs(bc.buscar_cs(ean))
    except Exception as e:  # noqa: BLE001
        return {"ean": ean, "proveedor": prov, "error": str(e)}

    if norm.get("error") and not norm.get("encontrado"):
        return {"ean": ean, "proveedor": prov, "error": norm.get("error")}

    estado = _estado_desde_ficha(norm)
    actualizar_estado(ean, prov, estado,
                      nombre=norm.get("nombre") or v.get("nombre"),
                      stock=norm.get("stock"),
                      precio_neto=norm.get("precio_neto"),
                      coste_real=norm.get("coste_real"))
    incidencia = None
    if estado != "disponible" and estado_previo != estado:
        incidencia = {"ean": ean, "proveedor": prov, "estado": estado,
                      "nombre": norm.get("nombre") or v.get("nombre")}
    return {"ean": ean, "proveedor": prov, "estado": estado, "incidencia": incidencia}


def comprobar_todos(proveedor: str | None = None) -> dict:
    """Recomprueba todos los vigilados activos (o solo los de un proveedor)."""
    vigilados = listar(proveedor=proveedor, solo_activos=True)
    if not vigilados:
        return {"comprobados": 0, "disponibles": 0, "incidencias": [], "errores": []}

    resultados = list(_pool.map(_comprobar_uno, vigilados))
    disponibles = sum(1 for r in resultados if r.get("estado") == "disponible")
    incidencias = [r["incidencia"] for r in resultados if r.get("incidencia")]
    errores = [{"ean": r["ean"], "proveedor": r["proveedor"], "error": r["error"]}
               for r in resultados if r.get("error")]
    return {"comprobados": len(vigilados), "disponibles": disponibles,
            "incidencias": incidencias, "errores": errores}


# --------------------------------------------------------------------------- #
# Sincronización desde PrestaShop (añade como vigilados de AZETA)
# --------------------------------------------------------------------------- #
def sincronizar_desde_prestashop() -> int:
    """Trae los productos de PrestaShop y los añade al monitor como AZETA."""
    from prestashop_client import PrestashopClient
    productos = PrestashopClient().listar_productos()
    n = 0
    for p in productos:
        ean = p.get("ean13") or p.get("ean")
        if not ean:
            continue
        if añadir(ean, "AZETA", nombre=p.get("nombre"), origen="prestashop",
                  id_prestashop=p.get("id") or p.get("id_product")):
            n += 1
    return n
