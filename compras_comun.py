"""
compras_comun.py — Persistencia en Supabase del histórico de compras
====================================================================
Registramos todo lo que compramos a los proveedores (AZETA / Liderpapel) para:
  1) comparar el precio pagado con el precio ACTUAL del proveedor (scraping local),
  2) ver la evolución de nuestras propias compras (más caro/barato que antes),
  3) detectar productos "pendientes" (sin enlazar a EAN o sin publicar en la tienda).

Tabla (ver supabase_compras_schema.sql): public.compras

Identificación del producto:
  - AZETA trae EAN en la factura            -> `ean`.
  - Liderpapel NO trae EAN (solo código y referencia) -> `codigo` + `referencia`;
    el EAN se resuelve por scraping y se guarda en `ean_resuelto`.
  - `clave` agrupa el histórico de un mismo producto:
      EAN (propio o resuelto) si lo hay; si no, "proveedor:codigo:referencia".

Igual criterio que competencia_comun / monitor_comun: la escritura (altas) puede
hacerse desde cualquier sitio; la comprobación de precio actual va por scraping y
solo tiene sentido en local, pero el snapshot resultante se guarda aquí y se lee
desde cualquier parte (incluido Render).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
TABLA = os.environ.get("SUPABASE_COMPRAS_TABLE", "compras").strip() or "compras"
_TIMEOUT = 12


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


# --------------------------------------------------------------------------- #
# Helpers de identidad
# --------------------------------------------------------------------------- #
def calcular_clave(proveedor: str, ean: str | None, codigo: str | None,
                   referencia: str | None, ean_resuelto: str | None = None) -> str:
    """Clave estable para agrupar el histórico de un mismo producto."""
    e = (ean or ean_resuelto or "").strip()
    if e:
        return e
    prov = (proveedor or "").strip().lower()
    return f"{prov}:{(codigo or '').strip()}:{(referencia or '').strip()}"


def _num(v):
    """Convierte a float admitiendo coma decimal y símbolos; None si no se puede."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("€", "").replace(" ", "")
    # Si trae coma decimal (formato ES), quita separador de miles '.' y usa '.'.
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Altas
# --------------------------------------------------------------------------- #
def _fila_norm(d: dict) -> dict:
    """Normaliza un dict de entrada (form o CSV) al esquema de la tabla."""
    proveedor = (d.get("proveedor") or "").strip()
    ean = (str(d.get("ean") or "").strip()) or None
    codigo = (str(d.get("codigo") or "").strip()) or None
    referencia = (str(d.get("referencia") or "").strip()) or None
    ean_resuelto = (str(d.get("ean_resuelto") or "").strip()) or None

    cantidad = _num(d.get("cantidad"))
    precio = _num(d.get("precio_pagado"))
    importe = _num(d.get("importe_total"))
    if importe is None and cantidad is not None and precio is not None:
        importe = round(cantidad * precio, 4)

    fecha = (d.get("fecha_compra") or "").strip() or None

    fila = {
        "fecha_compra": fecha,
        "proveedor": proveedor,
        "ean": ean,
        "codigo": codigo,
        "referencia": referencia,
        "nombre": (d.get("nombre") or "").strip() or None,
        "cantidad": cantidad,
        "precio_pagado": precio,
        "importe_total": importe,
        "factura": (d.get("factura") or "").strip() or None,
        "notas": (d.get("notas") or "").strip() or None,
        "ean_resuelto": ean_resuelto,
        "enlazado": bool(ean or ean_resuelto),
        "clave": calcular_clave(proveedor, ean, codigo, referencia, ean_resuelto),
    }
    return fila


def anadir_compra(d: dict) -> bool:
    return anadir_compras([d])


def anadir_compras(filas: list[dict]) -> bool:
    """Inserta una o varias compras (en lotes)."""
    if not activo() or not filas:
        return False
    payload = [_fila_norm(f) for f in filas]
    LOTE = 200
    ok = True
    for i in range(0, len(payload), LOTE):
        trozo = payload[i:i + LOTE]
        try:
            r = requests.post(_url(), json=trozo,
                              headers=_headers({"Prefer": "return=minimal"}), timeout=30)
            if r.status_code >= 300:
                print(f"[compras] anadir {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:  # noqa: BLE001
            print(f"[compras] no se pudo añadir: {e}")
            ok = False
    return ok


# --------------------------------------------------------------------------- #
# Lecturas
# --------------------------------------------------------------------------- #
def listar(limite: int = 5000) -> list[dict]:
    if not activo():
        return []
    try:
        r = requests.get(_url(),
                         params={"select": "*", "order": "fecha_compra.desc.nullslast,ts.desc",
                                 "limit": str(limite)},
                         headers=_headers(), timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else []
    except Exception:  # noqa: BLE001
        return []


def historico_de_clave(clave: str, limite: int = 500) -> list[dict]:
    if not activo() or not clave:
        return []
    try:
        r = requests.get(_url(),
                         params={"select": "*", "clave": f"eq.{clave}",
                                 "order": "fecha_compra.asc.nullslast,ts.asc",
                                 "limit": str(limite)},
                         headers=_headers(), timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else []
    except Exception:  # noqa: BLE001
        return []


def actualizar(id_: int, **campos) -> bool:
    if not activo() or not campos:
        return False
    try:
        r = requests.patch(_url(), params={"id": f"eq.{id_}"}, json=campos,
                           headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        return r.status_code < 300
    except Exception as e:  # noqa: BLE001
        print(f"[compras] no se pudo actualizar id={id_}: {e}")
        return False


def borrar(id_: int) -> bool:
    if not activo():
        return False
    try:
        r = requests.delete(_url(), params={"id": f"eq.{id_}"},
                            headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        return r.status_code < 300
    except Exception:  # noqa: BLE001
        return False


def enlazar_ean(clave: str, ean_resuelto: str) -> bool:
    """Marca todas las filas de una clave como enlazadas con el EAN resuelto.
    Nota: la `clave` de esas filas sigue siendo la antigua (Liderpapel sin EAN);
    guardamos el EAN en `ean_resuelto` y `enlazado=true` sin re-mapear la clave,
    para no romper el histórico ya agrupado."""
    if not activo() or not (clave and ean_resuelto):
        return False
    try:
        r = requests.patch(_url(), params={"clave": f"eq.{clave}"},
                           json={"ean_resuelto": ean_resuelto, "enlazado": True},
                           headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        return r.status_code < 300
    except Exception:  # noqa: BLE001
        return False


def guardar_precio_actual(clave: str, precio: float | None) -> bool:
    """Actualiza el snapshot de precio actual para todas las filas de una clave."""
    if not activo() or not clave:
        return False
    campos = {"precio_actual": precio,
              "precio_actual_fecha": datetime.now(timezone.utc).isoformat()}
    try:
        r = requests.patch(_url(), params={"clave": f"eq.{clave}"}, json=campos,
                           headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
        return r.status_code < 300
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Agregado por producto (para el listado principal)
# --------------------------------------------------------------------------- #
def resumen_por_producto(filas: list[dict] | None = None) -> list[dict]:
    """Agrupa las compras por `clave` y calcula, por producto:
       - nombre, proveedor, identificadores,
       - nº de compras y última fecha,
       - último precio pagado y precio pagado anterior (para ver la tendencia),
       - variación % entre las dos últimas compras,
       - precio actual (snapshot) y variación % pagado-vs-actual,
       - flags de pendiente (sin enlazar).
    """
    if filas is None:
        filas = listar()
    grupos: dict[str, list[dict]] = {}
    for f in filas:
        clave = f.get("clave") or calcular_clave(
            f.get("proveedor"), f.get("ean"), f.get("codigo"),
            f.get("referencia"), f.get("ean_resuelto"))
        grupos.setdefault(clave, []).append(f)

    salida = []
    for clave, items in grupos.items():
        # Orden cronológico (por fecha_compra; si falta, por ts).
        items_ord = sorted(
            items, key=lambda x: (x.get("fecha_compra") or "", x.get("ts") or ""))
        ultimo = items_ord[-1]
        anterior = items_ord[-2] if len(items_ord) >= 2 else None

        p_ult = _num(ultimo.get("precio_pagado"))
        p_ant = _num(anterior.get("precio_pagado")) if anterior else None
        var_hist = _pct(p_ant, p_ult)  # >0: ahora pagas más que antes

        precio_actual = None
        for it in reversed(items_ord):
            if it.get("precio_actual") is not None:
                precio_actual = _num(it.get("precio_actual"))
                break
        # Referencia = precio de HOY; dice si tu última compra fue más cara/barata.
        var_actual = _pct(precio_actual, p_ult)  # >0: pagaste más caro que hoy; <0: más barato

        ean = next((it.get("ean") for it in items_ord if it.get("ean")), None)
        ean_res = next((it.get("ean_resuelto") for it in items_ord if it.get("ean_resuelto")), None)
        enlazado = bool(ean or ean_res)

        salida.append({
            "clave": clave,
            "proveedor": ultimo.get("proveedor"),
            "nombre": next((it.get("nombre") for it in reversed(items_ord) if it.get("nombre")), None),
            "ean": ean,
            "ean_resuelto": ean_res,
            "codigo": next((it.get("codigo") for it in items_ord if it.get("codigo")), None),
            "referencia": next((it.get("referencia") for it in items_ord if it.get("referencia")), None),
            "n_compras": len(items_ord),
            "ultima_fecha": ultimo.get("fecha_compra"),
            "precio_ultimo": p_ult,
            "precio_anterior": p_ant,
            "variacion_hist_pct": var_hist,
            "precio_actual": precio_actual,
            "variacion_actual_pct": var_actual,
            "enlazado": enlazado,
        })
    # Primero los que más han subido respecto a la compra anterior.
    salida.sort(key=lambda x: (x["variacion_hist_pct"] is None,
                               -(x["variacion_hist_pct"] or 0)))
    return salida


def _pct(base, nuevo):
    """Variación % de `base` a `nuevo`. >0: ha subido. None si falta algún dato."""
    try:
        if base in (None, 0) or nuevo is None:
            return None
        return round((nuevo - base) / base * 100, 1)
    except (TypeError, ZeroDivisionError):
        return None
