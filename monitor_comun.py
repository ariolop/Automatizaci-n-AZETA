"""
monitor_comun.py — Monitor unificado (AZETA + Liderpapel) sobre Supabase
========================================================================
Un único monitor para los dos proveedores. Cada fila vigilada se identifica por
la pareja (ean, proveedor), de modo que el MISMO EAN puede estar vigilado en
AZETA y en Liderpapel a la vez, cada uno con su estado/stock/precio.

EAN de expositor: algunos productos están en PrestaShop con un EAN y en el
proveedor con otro (el del expositor). En esos casos la fila guarda los dos:
'ean' es SIEMPRE el de PrestaShop (y parte de la clave), y 'ean_expositor' es
el del proveedor, que es el que se usa para consultar AZETA / Liderpapel.

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
from supabase_paginado import get_todos

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


def ean_consulta(v: dict) -> str:
    """EAN con el que hay que preguntar al proveedor.

    Si la fila tiene 'ean_expositor' (el EAN con el que el proveedor vende el
    producto), se usa ese; si no, el EAN de PrestaShop."""
    exp = (v.get("ean_expositor") or "").strip()
    return exp or str(v.get("ean") or "").strip()


ESTADOS_INCIDENCIA = ("agotado", "desaparecido", "otro_proveedor")


def _filtro_estado(params: dict, estado: str) -> None:
    """Filtro de estado para PostgREST. Además de los estados normales admite
    'incidencias' (agotado + desaparecido) y 'sin_comprobar' (nunca revisado)."""
    if estado == "incidencias":
        params["estado"] = "in.(%s)" % ",".join(ESTADOS_INCIDENCIA)
    elif estado == "sin_comprobar":
        params["ultima_revision"] = "is.null"
    else:
        params["estado"] = f"eq.{estado}"


# --------------------------------------------------------------------------- #
# Lectura
# --------------------------------------------------------------------------- #
def listar(proveedor: str | None = None, estado: str | None = None,
           solo_activos: bool = False, limite: int | None = None,
           orden: str = "nombre.asc.nullslast,id.asc") -> list[dict]:
    """TODAS las filas que cumplen los filtros (paginando de 1000 en 1000)."""
    if not activo():
        return []
    params = {"select": "*", "order": orden}
    if proveedor:
        params["proveedor"] = f"eq.{normalizar_proveedor(proveedor)}"
    if estado:
        _filtro_estado(params, estado)
    if solo_activos:
        params["activo"] = "eq.true"
    return get_todos(_url(TABLA), params, _headers(), maximo=limite) or []


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
    filtros = {
        "total": base,
        "AZETA": {**base, "proveedor": "eq.AZETA"},
        "Liderpapel": {**base, "proveedor": "eq.Liderpapel"},
        "incidencias": {**base, "estado": "in.(%s)" % ",".join(ESTADOS_INCIDENCIA)},
        "sin_comprobar": {**base, "ultima_revision": "is.null"},
        "otro_proveedor": {**base, "estado": "eq.otro_proveedor"},
    }
    # En paralelo: antes eran 4 peticiones seguidas en cada carga de la página.
    claves = list(filtros)
    valores = list(_pool_lectura.map(lambda k: _contar(filtros[k]), claves))
    n = dict(zip(claves, valores))
    return {
        "total": n["total"],
        "por_proveedor": {"AZETA": n["AZETA"], "Liderpapel": n["Liderpapel"]},
        "incidencias": n["incidencias"],
        "sin_comprobar": n["sin_comprobar"],
        "otro_proveedor": n["otro_proveedor"],
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
        _filtro_estado(params, estado)
    if buscar:
        # Entre comillas dobles para que paréntesis, comas o puntos del texto
        # no rompan la sintaxis de PostgREST (antes "Pilot (azul)" daba 400).
        t = buscar.strip().replace("\\", "\\\\").replace('"', '\\"')
        params["or"] = (f'(ean.ilike."*{t}*",ean_expositor.ilike."*{t}*",'
                        f'nombre.ilike."*{t}*")')
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


def filas_por_eans(eans: list) -> list[dict]:
    """Filas del monitor cuyo EAN o EAN de expositor es alguno de 'eans'
    (para que /buscar indique si el producto ya está vigilado)."""
    eans = [e for e in dict.fromkeys("".join(ch for ch in str(x or "") if ch.isdigit()) for x in eans) if e]
    if not activo() or not eans:
        return []
    lista = ",".join(eans)
    params = {"select": "ean,ean_expositor,proveedor,estado,activo,ultima_revision,coste_real",
              "or": f"(ean.in.({lista}),ean_expositor.in.({lista}))", "limit": "50"}
    try:
        r = requests.get(_url(TABLA), params=params, headers=_headers(), timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else []
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo consultar vigilados: {e}")
        return []


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
           precio_neto=None, coste_real=None,
           ean_expositor: str | None = None,
           ultima_revision: str | None = None) -> bool:
    """Alta (o actualización) de un vigilado. Upsert por (ean, proveedor).

    'ean' es el de PrestaShop; 'ean_expositor' (opcional) el del proveedor."""
    if not activo() or not ean:
        return False
    fila = {"ean": str(ean), "proveedor": normalizar_proveedor(proveedor),
            "origen": origen, "activo": True}
    if ean_expositor is not None:
        fila["ean_expositor"] = str(ean_expositor).strip() or None
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
    if ultima_revision is not None:
        fila["ultima_revision"] = ultima_revision
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


def vigilar_con_ficha(ean: str, proveedor: str, ficha: dict, origen: str = "manual",
                      id_prestashop=None, ean_expositor: str | None = None) -> bool:
    """Alta (o actualización) de un vigilado YA rellena con lo que dice el
    proveedor (nombre, estado, stock, precio neto, coste real), sin esperar a la
    próxima comprobación. 'ficha' es un dict normalizado (bc.norm_azeta /
    bc.norm_cs). Deja también una entrada en el histórico."""
    from datetime import datetime, timezone
    if not activo() or not ean or not ficha:
        return False
    prov = normalizar_proveedor(proveedor)
    estado = _estado_desde_ficha(ficha)
    ok = añadir(ean, prov, nombre=ficha.get("nombre"), origen=origen,
                id_prestashop=id_prestashop, estado=estado,
                stock=ficha.get("stock"), precio_neto=ficha.get("precio_neto"),
                coste_real=ficha.get("coste_real"), ean_expositor=ean_expositor,
                ultima_revision=datetime.now(timezone.utc).isoformat())
    if ok:
        _insertar_historico({"ean": str(ean), "proveedor": prov, "estado": estado,
                             "stock": ficha.get("stock"),
                             "precio_neto": ficha.get("precio_neto"),
                             "coste_real": ficha.get("coste_real"),
                             "nombre": ficha.get("nombre")})
    return ok


def vincular_expositor(id_prestashop, ean13: str | None, ean_expositor: str | None) -> bool:
    """Replica en el monitor el EAN de expositor de un producto de PrestaShop.

    1) Filas del producto (por id_prestashop o por su EAN de tienda): se les
       pone el EAN de expositor (o se les quita, si llega vacío).
    2) Filas que se dieron de alta con el EAN del proveedor (antes de vincular):
       pasan a la clave del EAN de tienda; si ya había fila con esa clave, la
       duplicada se borra."""
    if not activo():
        return False
    exp = (ean_expositor or "").strip() or None
    ean13 = (str(ean13 or "")).strip() or None
    conds = []
    if id_prestashop:
        conds.append(f"id_prestashop.eq.{int(id_prestashop)}")
    if ean13:
        conds.append(f"ean.eq.{ean13}")
    if not conds:
        return False
    cambios = {"ean_expositor": exp}
    if id_prestashop:
        cambios["id_prestashop"] = int(id_prestashop)
    ok = True
    try:
        r = requests.patch(_url(TABLA), params={"or": f"({','.join(conds)})"}, json=cambios,
                           headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        ok = r.status_code < 300
    except Exception as e:  # noqa: BLE001
        print(f"[monitor] no se pudo fijar el EAN de expositor: {e}")
        ok = False

    if exp and ean13 and exp != ean13:
        try:
            r = requests.get(_url(TABLA), params={"select": "ean,proveedor", "ean": f"eq.{exp}"},
                             headers=_headers(), timeout=_TIMEOUT)
            viejas = r.json() if r.status_code < 300 else []
        except Exception:  # noqa: BLE001
            viejas = []
        for f in viejas:
            prov = f.get("proveedor")
            if obtener(ean13, prov):
                quitar(exp, prov)
            else:
                _patch(exp, prov, {"ean": ean13, **cambios})
    return ok


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


def _distinto(a, b) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    try:
        return abs(float(a) - float(b)) > 0.0001
    except (TypeError, ValueError):
        return str(a) != str(b)


def actualizar_estado(ean: str, proveedor: str, estado: str, nombre=None,
                      stock=None, precio_neto=None, coste_real=None,
                      previo: dict | None = None) -> None:
    """Guarda el estado de una comprobación. En el histórico solo se apunta si
    algo ha cambiado respecto a 'previo' (antes: una fila por comprobación,
    ~8000 por cada "Comprobar todos")."""
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
    cambia = previo is None or previo.get("estado") != estado or any(
        _distinto(previo.get(k), v) for k, v in
        (("stock", stock), ("precio_neto", precio_neto), ("coste_real", coste_real)))
    if cambia:
        _insertar_historico({"ean": str(ean), "proveedor": prov, "estado": estado,
                             "stock": stock, "precio_neto": precio_neto,
                             "coste_real": coste_real, "nombre": nombre})


# --------------------------------------------------------------------------- #
# Comprobación de estado (reutiliza busqueda_comun)
# --------------------------------------------------------------------------- #
_pool = ThreadPoolExecutor(max_workers=6)
_pool_lectura = ThreadPoolExecutor(max_workers=5)


# 'otro_proveedor': no está en su proveedor pero SÍ en el otro (proveedor mal
# asignado). Se guarda como estado para que no se pierda y se pueda filtrar.
ESTADOS_AUSENTE = ("desaparecido", "otro_proveedor")


def otro_proveedor(prov: str) -> str:
    return "Liderpapel" if normalizar_proveedor(prov) == "AZETA" else "AZETA"


def _consultar(prov: str, ean_prov: str) -> dict:
    """Ficha normalizada del proveedor (formato busqueda_comun). Nunca lanza."""
    try:
        if prov == "AZETA":
            return bc.norm_azeta(bc.buscar_azeta(ean_prov))
        return bc.norm_cs(bc.buscar_cs(ean_prov))
    except Exception as e:  # noqa: BLE001
        return {"encontrado": False, "error": f"Error consultando {prov}: {e}"}


def fallo_proveedor(norm: dict) -> bool:
    """True si el proveedor NO ha respondido bien (caído, bloqueado, ocupado...):
    no sabemos si el producto está, así que NO se toca su estado.
    'EAN no coincide' (Liderpapel) es un "no encontrado" normal, no un fallo."""
    return bool(norm.get("error")) and not norm.get("encontrado") \
        and not norm.get("ean_no_coincide")


def buscar_en_otro(v: dict, prov: str) -> dict | None:
    """Un producto que no está en su proveedor, ¿está en el otro? (p. ej. un
    Liderpapel vigilado como AZETA). Devuelve {proveedor, ean, nombre,
    disponible, ya_vigilado} o None (tampoco está, o el otro no responde)."""
    otro = otro_proveedor(prov)
    eans = [x for x in (ean_consulta(v), str(v.get("ean") or "").strip()) if x]
    for e in dict.fromkeys(eans):
        n = _consultar(otro, e)
        if n.get("encontrado"):
            return {"proveedor": otro, "ean": e, "nombre": n.get("nombre"),
                    "disponible": bool(n.get("disponible")),
                    "ya_vigilado": obtener(v.get("ean"), otro) is not None}
    return None


def _guardar(v: dict, prov: str, norm: dict, en_otro: dict | None = None) -> dict:
    """Guarda el resultado de una consulta VÁLIDA y devuelve el resumen."""
    ean = v.get("ean")
    estado_previo = v.get("estado")
    estado = _estado_desde_ficha(norm)
    if estado == "desaparecido" and en_otro:
        estado = "otro_proveedor"
    nombre = norm.get("nombre") or v.get("nombre")
    actualizar_estado(ean, prov, estado, nombre=nombre,
                      stock=norm.get("stock"),
                      precio_neto=norm.get("precio_neto"),
                      coste_real=norm.get("coste_real"),
                      previo=v if v.get("ultima_revision") else None)
    incidencia = None
    if estado != "disponible" and estado_previo != estado:
        incidencia = {"ean": ean, "proveedor": prov, "estado": estado, "nombre": nombre}
        if en_otro:
            incidencia["en_otro"] = en_otro["proveedor"]
    return {"ean": ean, "ean_expositor": v.get("ean_expositor"),
            "ean_consultado": ean_consulta(v),
            "proveedor": prov, "estado": estado, "nombre": nombre,
            "stock": norm.get("stock"), "precio_neto": norm.get("precio_neto"),
            "coste_real": norm.get("coste_real"), "incidencia": incidencia,
            "en_otro": en_otro}


def _comprobar_uno(v: dict, mirar_otro: bool = True) -> dict:
    """Comprobación de UNA fila (botón ↻). Si el proveedor falla no se toca nada."""
    prov = normalizar_proveedor(v.get("proveedor"))
    norm = _consultar(prov, ean_consulta(v))
    if fallo_proveedor(norm):
        return {"ean": v.get("ean"), "proveedor": prov, "error": norm.get("error")}
    en_otro = buscar_en_otro(v, prov) if (mirar_otro and not norm.get("encontrado")) else None
    return _guardar(v, prov, norm, en_otro)


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
                id_prestashop=row.get("id_prestashop"),
                ean_expositor=row.get("ean_expositor"))
    if not ok:
        return False
    quitar(ean, viejo)
    try:
        comprobar_uno(ean, nuevo)   # actualizar estado con el proveedor correcto
    except Exception:  # noqa: BLE001
        pass
    return True


def pasar_al_otro(lote: int = 8, proveedor: str | None = None) -> dict:
    """Pasa al otro proveedor las filas en estado 'otro_proveedor' (de 'lote' en
    'lote', para que cada petición web sea corta; el navegador repite hasta 0).
    Antes de moverla se vuelve a consultar en el otro proveedor: así la fila
    nueva queda ya con estado/stock/coste y con el EAN correcto (si se encontró
    por el EAN de tienda, se quita el expositor). Si ya no está, no se mueve."""
    filtros = {"activo": "eq.true", "estado": "eq.otro_proveedor"}
    if proveedor:
        filtros["proveedor"] = f"eq.{normalizar_proveedor(proveedor)}"
    filas = listar(proveedor=proveedor, estado="otro_proveedor", solo_activos=True,
                   limite=lote, orden="id.asc")
    movidos, saltados, error = 0, [], None
    for v in filas:
        viejo = normalizar_proveedor(v.get("proveedor"))
        nuevo = otro_proveedor(viejo)
        ean = str(v.get("ean") or "")
        norm, ean_ok = None, None
        for e in dict.fromkeys(x for x in (ean_consulta(v), ean) if x):
            n = _consultar(nuevo, e)
            if fallo_proveedor(n):
                error = n.get("error")
                break
            if n.get("encontrado"):
                norm, ean_ok = n, e
                break
        if error:
            break                              # el otro proveedor no responde: parar
        if not norm:
            # Ya no aparece en el otro: se deja como desaparecido en el suyo.
            _patch(ean, viejo, {"estado": "desaparecido"})
            saltados.append(ean)
            continue
        ok = añadir(ean, nuevo, nombre=norm.get("nombre") or v.get("nombre"),
                    origen=v.get("origen") or "manual", id_prestashop=v.get("id_prestashop"),
                    ean_expositor=(ean_ok if ean_ok != ean else ""),
                    estado=_estado_desde_ficha(norm), stock=norm.get("stock"),
                    precio_neto=norm.get("precio_neto"), coste_real=norm.get("coste_real"),
                    ultima_revision=_dt.now(_tz.utc).isoformat())
        if ok and quitar(ean, viejo):
            movidos += 1
            _insertar_historico({"ean": ean, "proveedor": nuevo, "estado": _estado_desde_ficha(norm),
                                 "stock": norm.get("stock"), "precio_neto": norm.get("precio_neto"),
                                 "coste_real": norm.get("coste_real"),
                                 "nombre": norm.get("nombre") or v.get("nombre")})
    return {"ok": error is None, "movidos": movidos, "saltados": saltados,
            "error": error, "restantes": _contar(filtros)}


# --------------------------------------------------------------------------- #
# Comprobación masiva EN SEGUNDO PLANO (una sola a la vez, con progreso)
# --------------------------------------------------------------------------- #
# Antes "Comprobar todos" se hacía dentro de la petición: con miles de filas
# tardaba horas, el navegador daba error y nunca se veía el resultado.
import threading as _threading
import time as _time
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

_JOB_LOCK = _threading.Lock()
_JOB: dict = {"estado": "parado"}
_MAX_INCIDENCIAS = 300
_MAX_OTRO = 500

# Protección ante bloqueos de los proveedores (2026-10-24). El 16-10 una
# comprobación marcó como desaparecidos ~2000 productos que existían: AZETA y
# Liderpapel respondían con páginas de bloqueo que se leían como "no encontrado".
# Ahora (además de que los scrapers ya distinguen esas páginas y dan error):
#  - Si un proveedor falla, se PAUSA (PAUSAS_FALLO, cada vez más larga) y sigue;
#    si tras todas las pausas sigue fallando se PARA solo ese proveedor, y lo que
#    queda sin comprobar no se toca (ni estado ni fecha de revisión).
#  - Un producto que NO estaba desaparecido y ahora no aparece no se marca en el
#    momento: se vuelve a consultar al final, pasados ESPERA_CONFIRMAR segundos.
#  - Tras RACHA_SOSPECHOSA "no encontrados" seguidos se consulta un producto
#    TESTIGO (el último que sí se encontró): si ya no aparece, es un bloqueo
#    silencioso y se trata como fallo del proveedor.
#  - Lo que no está en su proveedor se busca en el otro (estado 'otro_proveedor').
PAUSAS_FALLO = (60, 120, 300)     # segundos
ESPERA_OCUPADO = 5                # la sesión la está usando otra consulta
MAX_OCUPADO = 6                   # ocupado tantas veces seguidas = fallo
ESPERA_CONFIRMAR = 90             # segundos antes de confirmar un desaparecido nuevo
RACHA_SOSPECHOSA = 5


def estado_comprobacion() -> dict:
    """Copia del estado del trabajo (para la barra de progreso)."""
    import copy as _copy
    with _JOB_LOCK:
        return _copy.deepcopy(_JOB)


def iniciar_comprobacion(proveedor: str | None = None, estado: str | None = None,
                         maximo: int | None = None, origen: str = "manual") -> dict:
    """Lanza la comprobación en segundo plano. Primero las filas revisadas hace
    más tiempo (o nunca), así una ejecución cortada se puede retomar.
    maximo: solo las N más antiguas (útil para un cron que va por tandas)."""
    with _JOB_LOCK:
        if _JOB.get("estado") in ("en_curso", "preparando"):
            return {"ok": False, "error": "Ya hay una comprobación en marcha.", "ya_en_curso": True}
        _JOB.clear()
        _JOB.update({"estado": "preparando", "origen": origen, "proveedor": proveedor,
                     "filtro_estado": estado, "total": 0, "hechos": 0, "disponibles": 0,
                     "n_incidencias": 0, "n_errores": 0, "incidencias": [], "errores": [],
                     "n_otro": 0, "otro": [], "falsas_alarmas": 0, "sin_comprobar": 0,
                     "proveedores": {},
                     "inicio": _dt.now(_tz.utc).isoformat(), "fin": None, "cancelar": False})
    try:
        filas = listar(proveedor=proveedor, estado=estado, solo_activos=True, limite=maximo,
                       orden="ultima_revision.asc.nullsfirst,id.asc")
    except Exception as e:  # noqa: BLE001
        filas = []
        print(f"[monitor] no se pudo preparar la comprobación: {e}")
    with _JOB_LOCK:
        _JOB.update({"estado": "en_curso", "total": len(filas)})
    _threading.Thread(target=_trabajo_comprobacion, args=(filas,), daemon=True,
                      name="monitor-comprobar").start()
    return {"ok": True, "total": len(filas)}


def cancelar_comprobacion() -> bool:
    with _JOB_LOCK:
        if _JOB.get("estado") not in ("en_curso", "preparando"):
            return False
        _JOB["cancelar"] = True
    return True


def _cancelado() -> bool:
    with _JOB_LOCK:
        return bool(_JOB.get("cancelar"))


def _dormir(segundos: float) -> bool:
    """Espera 'segundos' salvo que se cancele el trabajo (False si se canceló)."""
    fin = _time.monotonic() + segundos
    while True:
        if _cancelado():
            return False
        resto = fin - _time.monotonic()
        if resto <= 0:
            return True
        _time.sleep(min(1.0, resto))


def _prov_set(prov: str, **cambios) -> None:
    with _JOB_LOCK:
        _JOB.setdefault("proveedores", {}).setdefault(prov, {}).update(cambios)


def _anotar(prov: str, v: dict, r: dict | None = None, error: str | None = None) -> None:
    """Cuenta un producto terminado en el progreso global y en el del proveedor."""
    with _JOB_LOCK:
        _JOB["hechos"] += 1
        p = _JOB["proveedores"].setdefault(prov, {})
        p["hechos"] = p.get("hechos", 0) + 1
        if error:
            _JOB["n_errores"] += 1
            if len(_JOB["errores"]) < 50:
                _JOB["errores"].append({"ean": v.get("ean"), "proveedor": prov,
                                        "error": str(error)[:200]})
            return
        if not r:
            return
        if r.get("estado") == "disponible":
            _JOB["disponibles"] += 1
        if r.get("falsa_alarma"):
            _JOB["falsas_alarmas"] += 1
        if r.get("incidencia"):
            _JOB["n_incidencias"] += 1
            if len(_JOB["incidencias"]) < _MAX_INCIDENCIAS:
                _JOB["incidencias"].append(r["incidencia"])
        if r.get("en_otro"):
            _JOB["n_otro"] += 1
            if len(_JOB["otro"]) < _MAX_OTRO:
                o = r["en_otro"]
                _JOB["otro"].append({"ean": r.get("ean"), "proveedor": prov,
                                     "nombre": r.get("nombre") or o.get("nombre"),
                                     "otro": o.get("proveedor"),
                                     "disponible": o.get("disponible"),
                                     "ya_vigilado": o.get("ya_vigilado")})


class _Freno:
    """Pausas crecientes cuando un proveedor falla; avisa cuando hay que pararlo."""

    def __init__(self, prov: str):
        self.prov = prov
        self.fallos = 0
        self.ocupado = 0
        self.ultimo_error = None

    def ok(self) -> None:
        if self.fallos:
            _prov_set(self.prov, estado="en_curso", nota=None, hasta=None)
        self.fallos = 0
        self.ocupado = 0

    def fallo(self, error) -> bool:
        """Registra un fallo y espera. False = hay que parar este proveedor
        (o se ha cancelado el trabajo)."""
        error = str(error or "error desconocido")
        if "ocupado" in error.lower() and self.ocupado < MAX_OCUPADO:
            self.ocupado += 1                 # otra consulta tiene la sesión
            return _dormir(ESPERA_OCUPADO)
        self.ocupado = 0
        self.fallos += 1
        self.ultimo_error = error
        if self.fallos > len(PAUSAS_FALLO):
            return False
        pausa = PAUSAS_FALLO[self.fallos - 1]
        print(f"[monitor] {self.prov} falla ({error[:150]}); pausa de {pausa} s", flush=True)
        _prov_set(self.prov, estado="pausado", error=error[:200],
                  nota=f"{self.prov} no responde bien; pausa {self.fallos}/{len(PAUSAS_FALLO)} "
                       f"de {pausa} s y se reintenta.",
                  hasta=(_dt.now(_tz.utc) + _td(seconds=pausa)).isoformat())
        return _dormir(pausa)


def _trabajo_proveedor(prov: str, filas: list[dict]) -> None:
    """Comprueba las filas de UN proveedor, de una en una, con freno y confirmación."""
    freno = _Freno(prov)
    testigo = None          # EAN encontrado hace poco: sirve para detectar bloqueos silenciosos
    racha = 0               # "no encontrados" seguidos desde el último testigo
    por_confirmar: list[dict] = []
    aplazadas: list[dict] = []
    ultimo_visto = 0.0

    def parar(pendientes: list[dict]) -> None:
        with _JOB_LOCK:
            _JOB["sin_comprobar"] = _JOB.get("sin_comprobar", 0) + len(pendientes)
            p = _JOB["proveedores"].setdefault(prov, {})
            n = p["sin_comprobar"] = (p.get("sin_comprobar") or 0) + len(pendientes)
        _prov_set(prov, estado="parado", hasta=None,
                  error=(freno.ultimo_error or "")[:200],
                  nota=(f"{prov} sigue fallando tras {len(PAUSAS_FALLO)} pausas: se ha parado "
                        f"su comprobación. {n} productos quedan sin comprobar (sin tocar)."))
        print(f"[monitor] {prov} PARADO: {n} sin comprobar. Último error: {freno.ultimo_error}",
              flush=True)

    def testigo_ok() -> bool | None:
        """¿Sigue apareciendo el testigo? True/False, o None si no hay testigo
        o el proveedor ha fallado al consultarlo (lo gestiona el freno)."""
        if not testigo:
            return None
        nt = _consultar(prov, testigo)
        if fallo_proveedor(nt):
            return None if freno.fallo(nt.get("error")) else False
        return bool(nt.get("encontrado"))

    # --- 1.ª pasada (y 2.ª para las que fallaron por el proveedor) ----------
    cola, i, segunda = list(filas), 0, False
    while True:
        if i >= len(cola):
            if aplazadas and not segunda:
                cola, aplazadas, i, segunda = aplazadas, [], 0, True
                continue
            break
        if _cancelado():
            return
        v = cola[i]
        norm = _consultar(prov, ean_consulta(v))

        if fallo_proveedor(norm):
            if not segunda:
                aplazadas.append(v)          # se reintenta al final
            else:
                # Ya falló una vez: error de ESTE producto. Se apunta y se le
                # pone fecha de revisión para que el cron no se atasque en él.
                _anotar(prov, v, error=norm.get("error"))
                _patch(v.get("ean"), prov, {"ultima_revision": _dt.now(_tz.utc).isoformat()})
            i += 1
            if not freno.fallo(norm.get("error")):
                if not _cancelado():
                    parar(cola[i:] + aplazadas + por_confirmar)
                return
            continue

        freno.ok()
        i += 1
        if norm.get("encontrado"):
            testigo, racha = ean_consulta(v), 0
            _anotar(prov, v, _guardar(v, prov, norm))
            continue

        # No encontrado. ¿Racha sospechosa? -> comprobar el testigo.
        racha += 1
        if racha >= RACHA_SOSPECHOSA:
            racha = 0
            if testigo_ok() is False:
                print(f"[monitor] {prov}: el testigo {testigo} ya no aparece -> posible bloqueo",
                      flush=True)
                # Esta fila (y las recientes por confirmar) se revisan luego.
                aplazadas.append(v)
                if not freno.fallo(f"{prov} deja de encontrar productos que sí existen "
                                   f"(testigo {testigo}): posible bloqueo"):
                    if not _cancelado():
                        parar(cola[i:] + aplazadas + por_confirmar)
                    return
                continue

        if v.get("estado") not in ESTADOS_AUSENTE:
            por_confirmar.append(v)          # nuevo "desaparecido": confirmar luego
            ultimo_visto = _time.monotonic()
            continue
        # Ya estaba ausente y sigue sin estar: ¿está en el otro proveedor?
        _anotar(prov, v, _guardar(v, prov, norm, buscar_en_otro(v, prov)))

    if segunda and aplazadas:
        # Filas que fallaron en la 2.ª pasada por un bloqueo puntual: quedan sin
        # tocar para la próxima comprobación.
        with _JOB_LOCK:
            _JOB["sin_comprobar"] = _JOB.get("sin_comprobar", 0) + len(aplazadas)
            p = _JOB["proveedores"].setdefault(prov, {})
            p["sin_comprobar"] = (p.get("sin_comprobar") or 0) + len(aplazadas)

    # --- Confirmación de los "desaparecidos nuevos" -------------------------
    if por_confirmar:
        _prov_set(prov, estado="confirmando", hasta=None,
                  nota=f"Confirmando {len(por_confirmar)} posibles desaparecidos…")
        espera = ESPERA_CONFIRMAR - (_time.monotonic() - ultimo_visto)
        if espera > 0 and not _dormir(espera):
            return
        j, racha = 0, 0
        while j < len(por_confirmar):
            if _cancelado():
                return
            if racha >= RACHA_SOSPECHOSA or (j == 0 and testigo):
                racha = 0
                t = testigo_ok()
                if t is False:
                    if not freno.fallo(f"{prov} deja de encontrar productos que sí existen "
                                       f"(testigo {testigo}): posible bloqueo"):
                        if not _cancelado():
                            parar(por_confirmar[j:])
                        return
                    continue
            v = por_confirmar[j]
            norm = _consultar(prov, ean_consulta(v))
            if fallo_proveedor(norm):
                if not freno.fallo(norm.get("error")):
                    if not _cancelado():
                        parar(por_confirmar[j:])
                    return
                continue                      # el mismo producto, tras la pausa
            freno.ok()
            j += 1
            if norm.get("encontrado"):
                r = _guardar(v, prov, norm)
                r["falsa_alarma"] = True      # la 1.ª vez no salió: se ha evitado un falso desaparecido
                testigo, racha = ean_consulta(v), 0
            else:
                racha += 1
                r = _guardar(v, prov, norm, buscar_en_otro(v, prov))
            _anotar(prov, v, r)

    _prov_set(prov, estado="terminado", nota=None, hasta=None)


def _trabajo_comprobacion(filas: list[dict]) -> None:
    try:
        # Un hilo por proveedor: AZETA y Liderpapel van en paralelo entre sí, y
        # dentro de cada uno de una en una (una sola sesión; AZETA además va
        # limitada a 1 petición cada 2 s por Cloudflare).
        grupos: dict[str, list] = {}
        for v in filas:
            grupos.setdefault(normalizar_proveedor(v.get("proveedor")), []).append(v)
        with _JOB_LOCK:
            _JOB["proveedores"] = {p: {"total": len(g), "hechos": 0, "estado": "en_curso",
                                       "nota": None} for p, g in grupos.items()}
        hilos = [_threading.Thread(target=_trabajo_proveedor, args=(p, g), daemon=True,
                                   name=f"monitor-{p}") for p, g in grupos.items()]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        with _JOB_LOCK:
            _JOB["estado"] = "cancelado" if _JOB.get("cancelar") else "terminado"
    except Exception as e:  # noqa: BLE001
        with _JOB_LOCK:
            _JOB["estado"] = "error"
            _JOB["error"] = str(e)
    finally:
        with _JOB_LOCK:
            _JOB["fin"] = _dt.now(_tz.utc).isoformat()
            if _JOB.get("estado") in ("en_curso", "preparando"):
                _JOB["estado"] = "terminado"


def comprobar_todos(proveedor: str | None = None) -> dict:
    """(Síncrono, para scripts) Recomprueba todos los vigilados activos."""
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
           nuevo_ean: str | None = None, nuevo_ean_expositor: str | None = None,
           empujar_ps: bool = True) -> dict:
    """Edita nombre, EAN y/o EAN de expositor de un vigilado.

    'nuevo_ean' es el EAN de PrestaShop (clave de la fila). 'nuevo_ean_expositor'
    es el del proveedor: None = no tocar, cadena vacía = quitarlo.
    Si la fila tiene id_prestashop y empujar_ps, se actualiza también en
    PrestaShop (para mantener ambos iguales)."""
    prov = normalizar_proveedor(proveedor)
    row = obtener(ean, prov)
    if not row:
        return {"ok": False, "error": "No se encontró el producto vigilado."}

    nuevo_ean = (nuevo_ean or "").strip() or None
    nuevo_nombre = (nuevo_nombre if nuevo_nombre is not None else None)
    exp_actual = (row.get("ean_expositor") or "").strip()
    exp_nuevo = None if nuevo_ean_expositor is None else nuevo_ean_expositor.strip()
    cambia_exp = exp_nuevo is not None and exp_nuevo != exp_actual

    # 1) Empujar a PrestaShop primero (si procede); si falla, no divergemos.
    if empujar_ps and row.get("id_prestashop"):
        cambios_ps = {}
        if nuevo_ean and nuevo_ean != str(row.get("ean")):
            cambios_ps["ean13"] = nuevo_ean
        if nuevo_nombre and nuevo_nombre != (row.get("nombre") or ""):
            cambios_ps["nombre"] = nuevo_nombre
        if cambia_exp:
            cambios_ps["ean_expositor"] = exp_nuevo
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
    if cambia_exp:
        cambios["ean_expositor"] = exp_nuevo or None
    if cambios:
        _patch(ean, prov, cambios)
    return {"ok": True}


def _filas_con_ps() -> list[dict]:
    """Filas del monitor que están enlazadas a un producto de PrestaShop."""
    if not activo():
        return []
    params = {"select": "ean,ean_expositor,proveedor,nombre,id_prestashop",
              "id_prestashop": "not.is.null", "order": "id.asc"}
    return get_todos(_url(TABLA), params, _headers()) or []


def _insertar_lote(filas: list[dict]) -> int:
    """Alta masiva (bloques de 500). Ignora las que ya existan (ean, proveedor)."""
    n = 0
    for i in range(0, len(filas), 500):
        trozo = filas[i:i + 500]
        try:
            r = requests.post(_url(TABLA), params={"on_conflict": "ean,proveedor"}, json=trozo,
                              headers=_headers({"Prefer": "resolution=ignore-duplicates,return=minimal"}),
                              timeout=30)
            if r.status_code < 300:
                n += len(trozo)
            else:
                print(f"[monitor] alta masiva {r.status_code}: {r.text[:200]}")
        except Exception as e:  # noqa: BLE001
            print(f"[monitor] alta masiva fallida: {e}")
    return n


def _int_o_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def sincronizar_desde_prestashop() -> dict:
    """PrestaShop -> monitor, sin duplicar.

    - Productos ya vigilados (enlazados por id_prestashop, o por EAN / EAN de
      expositor si aún no estaban enlazados): se actualizan EAN, EAN de
      expositor, nombre y el enlace, en TODAS sus filas (AZETA y Liderpapel).
    - Productos nuevos: se dan de alta UNA vez, con el proveedor que tienen en
      PrestaShop (si el módulo lo devuelve; si no, AZETA).
    - Filas enlazadas a productos que ya no existen en PrestaShop: se
      desactivan (dejan de comprobarse), no se borran.
    Devuelve {añadidos, actualizados, desactivados}."""
    import config
    from prestashop_client import PrestashopClient
    productos = PrestashopClient().listar_productos(precio=False, solo_con_ean=False)
    filas = get_todos(_url(TABLA), {"select": "*", "order": "id.asc"}, _headers())
    if filas is None:
        # Sin la lista completa trataríamos todo como nuevo y se duplicaría.
        raise RuntimeError("No se pudo leer el monitor completo de Supabase; inténtalo de nuevo.")
    cfg = config.leer_config()
    prov_cs = str(cfg.get("PRESTASHOP_PROVEEDOR_CS") or "").strip()

    por_id: dict[int, list[dict]] = {}
    por_ean: dict[str, list[dict]] = {}
    for f in filas:
        try:
            if f.get("id_prestashop") is not None:
                por_id.setdefault(int(f["id_prestashop"]), []).append(f)
        except (TypeError, ValueError):
            pass
        for e in (f.get("ean"), f.get("ean_expositor")):
            if e:
                por_ean.setdefault(str(e).strip(), []).append(f)

    añadidos = actualizados = desactivados = 0
    ids_ps: set[int] = set()
    for p in productos:
        try:
            ids_ps.add(int(p.get("id") or p.get("id_product")))
        except (TypeError, ValueError):
            pass
    nuevas: list[dict] = []
    vistos_nuevos: set[str] = set()
    reenlazadas: set = set()
    for p in productos:
        try:
            pid = int(p.get("id") or p.get("id_product"))
        except (TypeError, ValueError):
            continue
        ean = str(p.get("ean13") or p.get("ean") or "").strip()
        # Solo si el módulo ya devuelve el campo: así una versión antigua del
        # módulo no borra los EAN de expositor puestos a mano.
        trae_exp = "ean_expositor" in p
        exp = (p.get("ean_expositor") or "").strip()
        nombre = p.get("nombre")

        rows = por_id.get(pid) or []
        if not rows:   # vigilado pero sin enlazar: localizar por EAN / expositor
            cand = (por_ean.get(ean, []) if ean else []) + (por_ean.get(exp, []) if exp else [])
            # Sin enlazar, o enlazadas a un id que ya no existe (producto borrado
            # y vuelto a crear con el mismo EAN): se re-enlazan a este.
            rows = [r for r in {id(r): r for r in cand}.values()
                    if r.get("id_prestashop") in (None, "", pid)
                    or _int_o_none(r.get("id_prestashop")) not in ids_ps]
        if rows:
            for row in rows:
                cambios = {}
                if ean and str(row.get("ean")) != ean:
                    cambios["ean"] = ean
                if nombre and (row.get("nombre") or "") != nombre:
                    cambios["nombre"] = nombre
                # El EAN de expositor manda desde PrestaShop (incluido quitarlo).
                if trae_exp and exp != (row.get("ean_expositor") or "").strip():
                    cambios["ean_expositor"] = exp or None
                if _int_o_none(row.get("id_prestashop")) != pid:
                    cambios["id_prestashop"] = pid
                    reenlazadas.add((row.get("ean"), row.get("proveedor")))
                if row.get("activo") is False:
                    cambios["activo"] = True       # vuelve a existir en la tienda
                if cambios and _patch(row["ean"], row["proveedor"], cambios):
                    actualizados += 1
        elif ean and ean not in vistos_nuevos:
            vistos_nuevos.add(ean)
            prov = "AZETA"
            if prov_cs and str(p.get("id_supplier") or "") == prov_cs:
                prov = "Liderpapel"
            # Todas las filas con las MISMAS claves (PostgREST lo exige en altas masivas).
            nuevas.append({"ean": ean, "proveedor": prov, "nombre": nombre,
                           "origen": "prestashop", "id_prestashop": pid, "activo": True,
                           "ean_expositor": (exp or None) if trae_exp else None})

    if nuevas:
        añadidos = _insertar_lote(nuevas)

    # Productos borrados en PrestaShop -> desactivar sus filas (si el listado
    # vino completo; con un listado vacío no se toca nada por seguridad).
    if ids_ps:
        for pid, rows in por_id.items():
            if pid in ids_ps:
                continue
            for row in rows:
                if (row.get("ean"), row.get("proveedor")) in reenlazadas:
                    continue          # ya enlazada a un producto que sí existe
                if row.get("activo") is not False and _patch(row["ean"], row["proveedor"], {"activo": False}):
                    desactivados += 1
    return {"añadidos": añadidos, "actualizados": actualizados, "desactivados": desactivados}
