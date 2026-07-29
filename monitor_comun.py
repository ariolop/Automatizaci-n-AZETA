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


def _contar(filtros: dict) -> int:
    """Cuenta filas que cumplen 'filtros' sin traerlas (Content-Range)."""
    if not activo():
        return 0
    params = {"select": "id", **filtros, "limit": "1"}
    try:
        r = requests.get(_url(TABLA), params=params,
                        headers=_headers({"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"}),
                        timeout=_TIMEOUT)
        cr = r.headers.get("Content-Range", "")  # p. ej. "0-0/8234"
        if "/" in cr:
            total = cr.split("/")[-1]
            return int(total) if total.isdigit() else 0
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo contar: {e}")
    return 0


def resumen() -> dict:
    """Conteos por proveedor e incidencias, eficiente (sin traer todas las filas)."""
    base = {"activo": "eq.true"}
    return {
        "total": _contar(base),
        "por_proveedor": {
            "AZETA": _contar({**base, "proveedor": "eq.AZETA"}),
            "Liderpapel": _contar({**base, "proveedor": "eq.Liderpapel"}),
        },
        "incidencias": _contar({**base, "estado": "in.(agotado,desaparecido)"}),
    }


def listar_pagina(proveedor: str | None = None, estado: str | None = None,
                  buscar: str | None = None, pagina: int = 1, por_pagina: int = 50) -> dict:
    """Página de vigilados con filtros + búsqueda de texto (ean/nombre).
    Devuelve {filas, total, pagina, por_pagina, paginas}."""
    if not activo():
        return {"filas": [], "total": 0, "pagina": 1, "por_pagina": por_pagina, "paginas": 0}
    pagina = max(1, int(pagina))
    desde = (pagina - 1) * por_pagina
    hasta = desde + por_pagina - 1
    params = {"select": "*", "order": "nombre.asc.nullslast"}
    if proveedor:
        params["proveedor"] = f"eq.{normalizar_proveedor(proveedor)}"
    if estado:
        params["estado"] = f"eq.{estado}"
    if buscar:
        t = buscar.replace(",", " ").strip()
        params["or"] = f"(ean.ilike.*{t}*,nombre.ilike.*{t}*)"
    try:
        r = requests.get(_url(TABLA), params=params,
                        headers=_headers({"Prefer": "count=exact", "Range-Unit": "items",
                                          "Range": f"{desde}-{hasta}"}),
                        timeout=_TIMEOUT)
        filas = r.json() if r.status_code < 300 else []
        cr = r.headers.get("Content-Range", "")
        total = int(cr.split("/")[-1]) if "/" in cr and cr.split("/")[-1].isdigit() else len(filas)
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo paginar: {e}")
        filas, total = [], 0
    paginas = (total + por_pagina - 1) // por_pagina if por_pagina else 1
    return {"filas": filas, "total": total, "pagina": pagina,
            "por_pagina": por_pagina, "paginas": paginas}


def obtener(ean: str, proveedor: str) -> dict | None:
    if not activo():
        return None
    params = {"select": "*", "ean": f"eq.{ean}",
              "proveedor": f"eq.{normalizar_proveedor(proveedor)}", "limit": "1"}
    try:
        r = requests.get(_url(TABLA), params=params, headers=_headers(), timeout=_TIMEOUT)
        if r.status_code < 300:
            filas = r.json()
            return filas[0] if filas else None
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo obtener: {e}")
    return None


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


def comprobar_uno(ean: str, proveedor: str) -> dict:
    """Recomprueba una sola fila (ean, proveedor) y actualiza su estado."""
    v = obtener(ean, proveedor) or {"ean": ean, "proveedor": normalizar_proveedor(proveedor)}
    return _comprobar_uno(v)


def cambiar_proveedor(ean: str, proveedor_viejo: str, proveedor_nuevo: str) -> bool:
    """Reasigna una fila vigilada a otro proveedor (p. ej. AZETA -> Liderpapel).
    Copia los datos, crea la fila nueva, borra la vieja y la recomprueba."""
    viejo = normalizar_proveedor(proveedor_viejo)
    nuevo = normalizar_proveedor(proveedor_nuevo)
    if viejo == nuevo:
        return False
    row = obtener(ean, viejo)
    if not row:
        return False
    ok = añadir(ean, nuevo, nombre=row.get("nombre"), origen=row.get("origen") or "manual",
                id_prestashop=row.get("id_prestashop"))
    if not ok:
        return False
    quitar(ean, viejo)
    try:
        comprobar_uno(ean, nuevo)   # actualizar estado con el proveedor correcto
    except Exception:  # noqa: BLE001
        pass
    return True


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
# Edición (con empuje a PrestaShop) y sincronización bidireccional
# --------------------------------------------------------------------------- #
def editar(ean: str, proveedor: str, nuevo_nombre: str | None = None,
           nuevo_ean: str | None = None, empujar_ps: bool = True) -> dict:
    """Edita nombre y/o EAN de un vigilado. Si tiene id_prestashop y empujar_ps,
    actualiza también el producto en PrestaShop (para mantener ambos iguales)."""
    prov = normalizar_proveedor(proveedor)
    row = obtener(ean, prov)
    if not row:
        return {"ok": False, "error": "No se encontró el producto vigilado."}

    nuevo_ean = (nuevo_ean or "").strip() or None
    nuevo_nombre = (nuevo_nombre if nuevo_nombre is not None else None)

    # 1) Empujar a PrestaShop primero (si procede); si falla, no divergemos.
    if empujar_ps and row.get("id_prestashop"):
        cambios_ps = {}
        if nuevo_ean and nuevo_ean != str(row.get("ean")):
            cambios_ps["ean13"] = nuevo_ean
        if nuevo_nombre and nuevo_nombre != (row.get("nombre") or ""):
            cambios_ps["nombre"] = nuevo_nombre
        if cambios_ps:
            try:
                from prestashop_client import PrestashopClient
                PrestashopClient().actualizar_producto(id_product=row["id_prestashop"], cambios=cambios_ps)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"No se pudo actualizar en PrestaShop: {e}"}

    # 2) Actualizar la fila del monitor.
    cambios = {}
    if nuevo_nombre is not None:
        cambios["nombre"] = nuevo_nombre
    if nuevo_ean and nuevo_ean != str(row.get("ean")):
        cambios["ean"] = nuevo_ean
    if cambios:
        _patch(ean, prov, cambios)
    return {"ok": True}


def _filas_con_ps() -> list[dict]:
    """Filas del monitor que están enlazadas a un producto de PrestaShop."""
    if not activo():
        return []
    params = {"select": "ean,proveedor,nombre,id_prestashop", "id_prestashop": "not.is.null", "limit": "10000"}
    try:
        r = requests.get(_url(TABLA), params=params, headers=_headers(), timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else []
    except Exception:  # noqa: BLE001
        return []


def sincronizar_desde_prestashop() -> dict:
    """Bidireccional (PS -> monitor): trae los productos de PrestaShop; a los ya
    enlazados por id les actualiza EAN/nombre, y añade los que no estén vigilados.
    Devuelve {añadidos, actualizados}."""
    from prestashop_client import PrestashopClient
    productos = PrestashopClient().listar_productos()
    por_id = {}
    for f in _filas_con_ps():
        try:
            por_id[int(f["id_prestashop"])] = f
        except (TypeError, ValueError):
            continue
    añadidos = actualizados = 0
    for p in productos:
        pid = p.get("id") or p.get("id_product")
        ean = p.get("ean13") or p.get("ean")
        nombre = p.get("nombre")
        row = por_id.get(int(pid)) if pid is not None else None
        if row:
            cambios = {}
            if ean and str(row.get("ean")) != str(ean):
                cambios["ean"] = str(ean)
            if nombre and (row.get("nombre") or "") != nombre:
                cambios["nombre"] = nombre
            if cambios:
                _patch(row["ean"], row["proveedor"], cambios)
                actualizados += 1
        elif ean:
            if añadir(ean, "AZETA", nombre=nombre, origen="prestashop", id_prestashop=pid):
                añadidos += 1
    return {"añadidos": añadidos, "actualizados": actualizados}
