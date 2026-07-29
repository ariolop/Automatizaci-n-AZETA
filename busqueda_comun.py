"""
busqueda_comun.py — Lógica compartida de búsqueda y publicación
================================================================
Un único sitio para:
  - Buscar un EAN en AZETA y/o Liderpapel (CS Papelería), en paralelo,
    reutilizando las sesiones (login una vez).
  - Normalizar ambas fichas a un formato común para el front.
  - Comparar cuál sale más barato por coste real (IVA + recargo).
  - Publicar en PrestaShop desde CUALQUIERA de los dos proveedores
    (AZETA ya lo tenía; Liderpapel se mapea aquí a ese mismo formato).

Lo usan tanto la página unificada (/buscar) como el escáner (/escaner),
para no duplicar la lógica.
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import azeta_producto
from azeta_login import AzetaSession, AzetaError
from cspapeleria import cs_producto
from cspapeleria.cs_login import CSSession, CSError

_pool = ThreadPoolExecutor(max_workers=4)

# Sesiones persistentes por proveedor (login perezoso; re-login si caduca).
_azeta = {"ses": None, "lock": threading.Lock()}
_cs = {"ses": None, "lock": threading.Lock()}


# --------------------------------------------------------------------------- #
# Búsqueda (nunca lanzan: devuelven dict con 'error' si algo falla)
# --------------------------------------------------------------------------- #
def buscar_azeta(ean: str) -> dict:
    with _azeta["lock"]:
        for intento in (1, 2):
            try:
                if _azeta["ses"] is None:
                    s = AzetaSession()
                    s.login()
                    _azeta["ses"] = s
                datos = azeta_producto.buscar_producto(ean, sesion=_azeta["ses"])
                datos["proveedor"] = "AZETA"
                return datos
            except AzetaError as e:
                _azeta["ses"] = None
                if intento == 2:
                    return {"proveedor": "AZETA", "encontrado": False,
                            "error": f"AZETA no disponible: {e}"}
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return {"proveedor": "AZETA", "encontrado": False,
                        "error": f"Error consultando AZETA: {e}"}


def buscar_cs(ean: str) -> dict:
    with _cs["lock"]:
        for intento in (1, 2):
            try:
                if _cs["ses"] is None:
                    _cs["ses"] = CSSession()
                datos = cs_producto.buscar_producto(ean, sesion=_cs["ses"])
                datos["proveedor"] = "Liderpapel"
                return datos
            except CSError as e:
                _cs["ses"] = None
                if intento == 2:
                    return {"proveedor": "Liderpapel", "encontrado": False,
                            "error": f"Liderpapel no disponible: {e}"}
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return {"proveedor": "Liderpapel", "encontrado": False,
                        "error": f"Error consultando Liderpapel: {e}"}


# --------------------------------------------------------------------------- #
# Normalización a formato común para el front
# --------------------------------------------------------------------------- #
def norm_azeta(d: dict) -> dict:
    return {
        "proveedor": "AZETA",
        "encontrado": bool(d.get("encontrado")),
        "error": d.get("error"),
        "nombre": d.get("nombre"),
        "ean": d.get("ean"),
        "marca": d.get("fabricante"),
        "disponible": d.get("disponible"),
        "situacion": d.get("situacion"),
        "stock": None,
        "precio_neto": d.get("precio_unidad_sin_iva") or d.get("precio_sin_iva"),
        "coste_real": d.get("coste_real_unidad"),
        "pvp": d.get("pvp_recomendado"),
        "descripcion": d.get("descripcion"),
        "imagenes": d.get("imagenes") or [],
        "url": d.get("url_ficha"),
        "extra": {
            "IVA %": d.get("iva_pct"),
            "Uds/envase": d.get("unidades_venta"),
            "Dropshipping": "Sí" if d.get("dropshipping") else "No",
            "Material": d.get("material"),
        },
    }


def norm_cs(d: dict) -> dict:
    return {
        "proveedor": "Liderpapel",
        "encontrado": bool(d.get("encontrado")),
        "error": d.get("error") or d.get("motivo"),
        "nombre": d.get("nombre"),
        "ean": d.get("ean"),
        "marca": d.get("marca"),
        "disponible": d.get("disponible"),
        "situacion": None,
        "stock": d.get("stock"),
        "precio_neto": d.get("precio_neto"),
        "coste_real": d.get("coste_real"),
        "pvp": None,
        "descripcion": d.get("descripcion"),
        "imagenes": d.get("imagenes") or [],
        "url": None,
        "extra": {
            "IVA % (est.)": d.get("iva"),
            "Stock": d.get("stock"),
        },
    }


def comparar(azeta: dict, cs: dict) -> dict:
    """Decide el proveedor más barato por coste_real (IVA+recargo por unidad)."""
    ca = azeta.get("coste_real") if azeta.get("encontrado") else None
    cc = cs.get("coste_real") if cs.get("encontrado") else None
    res = {"mas_barato": None, "diferencia": None, "coste_azeta": ca, "coste_cs": cc}
    if ca is not None and cc is not None:
        if ca < cc:
            res["mas_barato"] = "AZETA"
        elif cc < ca:
            res["mas_barato"] = "Liderpapel"
        else:
            res["mas_barato"] = "empate"
        res["diferencia"] = round(abs(ca - cc), 2)
    elif ca is not None:
        res["mas_barato"] = "AZETA"
    elif cc is not None:
        res["mas_barato"] = "Liderpapel"
    return res


# --------------------------------------------------------------------------- #
# Búsqueda combinada (uno o los dos proveedores, en paralelo)
# --------------------------------------------------------------------------- #
def buscar(ean: str, con_azeta: bool = True, con_cs: bool = True) -> dict:
    fa = _pool.submit(buscar_azeta, ean) if con_azeta else None
    fc = _pool.submit(buscar_cs, ean) if con_cs else None
    azeta = norm_azeta(fa.result()) if fa else None
    cs = norm_cs(fc.result()) if fc else None
    encontrado = bool((azeta and azeta["encontrado"]) or (cs and cs["encontrado"]))
    comp = comparar(azeta or {}, cs or {})
    return {"ean": ean, "encontrado": encontrado,
            "azeta": azeta, "cs": cs, "comparacion": comp}


# --------------------------------------------------------------------------- #
# Publicación en PrestaShop (desde AZETA o desde Liderpapel)
# --------------------------------------------------------------------------- #
def _mapear_cs_a_prestashop(cs_raw: dict) -> dict:
    """Convierte la ficha cruda de Liderpapel al formato que espera el módulo
    PrestaShop (mismas claves que usa AZETA en prestashop_client.crear_producto)."""
    return {
        "nombre": cs_raw.get("nombre"),
        "ean": cs_raw.get("ean"),
        "precio_sin_iva": cs_raw.get("precio_neto"),
        "coste_real_unidad": cs_raw.get("coste_real"),
        "pvp_recomendado": None,
        "iva_pct": cs_raw.get("iva"),
        "unidades_venta": None,
        "fabricante": cs_raw.get("marca"),
        "descripcion": cs_raw.get("descripcion"),
        "situacion": None,
        "dropshipping": False,   # Liderpapel no es dropshipping como AZETA
        "imagenes": cs_raw.get("imagenes") or [],
    }


def prestashop_configurado() -> bool:
    import config
    return config.prestashop_configurado()


def ficha_prestashop(ean: str) -> dict | None:
    """Cómo está el producto en PrestaShop (nombre, precio, imagen, activo) o None."""
    if not ean or not prestashop_configurado():
        return None
    try:
        from prestashop_client import PrestashopClient
        d = PrestashopClient().obtener_ficha(ean)
        return d if d and d.get("encontrado") else None
    except Exception:  # noqa: BLE001
        return None


def ya_en_prestashop(ean: str) -> dict | None:
    """Devuelve {id, activo, nombre} si el EAN ya existe en la tienda; None si no
    existe o si PrestaShop no responde/está sin configurar."""
    if not ean or not prestashop_configurado():
        return None
    try:
        from prestashop_client import PrestashopClient
        existentes = PrestashopClient().comprobar_eans([ean])
        return existentes.get(ean)
    except Exception:  # noqa: BLE001
        return None


def publicar(proveedor: str, ean: str) -> dict:
    """Re-busca el producto por EAN en el proveedor indicado y lo crea en
    PrestaShop (desactivado). Devuelve {ok, id_product|error}."""
    if not prestashop_configurado():
        return {"ok": False, "error": "PrestaShop no está configurado."}

    import config
    cfg = config.leer_config()

    prov = (proveedor or "").lower()
    if prov == "azeta":
        raw = buscar_azeta(ean)
        if not raw or not raw.get("encontrado"):
            return {"ok": False, "error": "No se encontró el producto en AZETA."}
        payload = raw  # ya viene con las claves que espera el módulo
        id_proveedor = cfg.get("PRESTASHOP_PROVEEDOR_AZETA", "")
    elif prov in ("cs", "liderpapel"):
        raw = buscar_cs(ean)
        if not raw or not raw.get("encontrado"):
            return {"ok": False, "error": "No se encontró el producto en Liderpapel."}
        payload = _mapear_cs_a_prestashop(raw)
        # Proveedor distinto para Liderpapel ("Comercial del sur").
        id_proveedor = cfg.get("PRESTASHOP_PROVEEDOR_CS", "")
    else:
        return {"ok": False, "error": f"Proveedor no válido: {proveedor}"}

    try:
        from prestashop_client import PrestashopClient
        res = PrestashopClient().crear_producto(payload, id_proveedor=id_proveedor)
        return {"ok": True, "id_product": res.get("id_product"),
                "nombre": payload.get("nombre")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
