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


def buscar_cs(ean: str, forzar: bool = False) -> dict:
    with _cs["lock"]:
        for intento in (1, 2):
            try:
                if _cs["ses"] is None:
                    _cs["ses"] = CSSession()
                datos = cs_producto.buscar_producto(ean, sesion=_cs["ses"], forzar=forzar)
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
        "ean_no_coincide": bool(d.get("ean_no_coincide")),
        "ean_encontrado": d.get("ean_encontrado"),
        "nombre_encontrado": d.get("nombre_encontrado"),
        "nombre": d.get("nombre"),
        "ean": d.get("ean"),
        "marca": d.get("marca"),
        "referencia": d.get("referencia"),
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
def buscar(ean: str, con_azeta: bool = True, con_cs: bool = True,
           forzar_cs: bool = False) -> dict:
    fa = _pool.submit(buscar_azeta, ean) if con_azeta else None
    fc = _pool.submit(buscar_cs, ean, forzar_cs) if con_cs else None
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


def _normalizar_stock(valor):
    """Devuelve un entero >= 0 o None si no se indicó stock."""
    if valor is None or valor == "":
        return None
    try:
        return max(0, int(float(valor)))
    except (TypeError, ValueError):
        return None


def _normalizar_modo_venta(valor):
    """Modo de venta del módulo aplsalemode: 1/2/3. None si no se indicó.
    (0 = 'sin definir' equivale a no tocar nada -> None.)"""
    if valor is None or valor == "":
        return None
    try:
        m = int(valor)
    except (TypeError, ValueError):
        return None
    return m if m in (1, 2, 3) else None


def opciones_prestashop() -> dict:
    """Listas auxiliares de la tienda (proveedores, impuestos) o {} si falla."""
    if not prestashop_configurado():
        return {}
    try:
        from prestashop_client import PrestashopClient
        return PrestashopClient().obtener_opciones()
    except Exception:  # noqa: BLE001
        return {}


def actualizar_prestashop(ean: str | None = None, id_product=None,
                          precio=None, stock=None, ean13=None, id_impuestos=None,
                          activo=None) -> dict:
    """Actualiza EAN13, tipo de impuesto, estado (activo), precio (CON IVA) y/o stock
    de un producto YA existente en PrestaShop, localizado por EAN actual (o id_product)."""
    if not prestashop_configurado():
        return {"ok": False, "error": "PrestaShop no está configurado."}
    if not ean and not id_product:
        return {"ok": False, "error": "Falta 'ean' o 'id_product'."}

    cambios: dict = {}
    if activo is not None and str(activo).strip() != "":
        cambios["activo"] = 1 if str(activo).strip() in ("1", "true", "True", "on", "si", "sí") else 0
    if ean13 is not None and str(ean13).strip() != "":
        nuevo = "".join(ch for ch in str(ean13) if ch.isdigit())
        if not nuevo:
            return {"ok": False, "error": "EAN no válido."}
        if nuevo != (ean or ""):
            cambios["ean13"] = nuevo
    if id_impuestos is not None and str(id_impuestos).strip() != "":
        try:
            cambios["id_impuestos"] = int(id_impuestos)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Impuesto no válido."}
    if precio is not None and str(precio).strip() != "":
        try:
            cambios["precio"] = float(str(precio).replace(",", "."))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Precio no válido."}
    if stock is not None and str(stock).strip() != "":
        s = _normalizar_stock(stock)
        if s is None:
            return {"ok": False, "error": "Stock no válido."}
        cambios["stock"] = s

    if not cambios:
        return {"ok": False, "error": "Nada que actualizar."}

    try:
        from prestashop_client import PrestashopClient
        res = PrestashopClient().actualizar_producto(
            id_product=id_product, ean=ean, cambios=cambios)
        return {"ok": True, **res}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def publicar(proveedor: str, ean: str, stock=None, modo_venta=None,
             precio_con_iva=None, id_impuestos=None, activo=None,
             ean_publicar=None) -> dict:
    """Re-busca el producto por EAN en el proveedor indicado y lo crea en
    PrestaShop. Devuelve {ok, id_product|error}.

    Todos estos son OPCIONALES (si van a None, se mantiene el comportamiento
    anterior):
      - stock, modo_venta.
      - precio_con_iva: fija el PVP (precio de venta CON IVA) a mano.
      - id_impuestos: regla de IVA a aplicar.
      - activo: si es verdadero, el producto se crea ya activado.
      - ean_publicar: EAN13 con el que crear el producto. Útil cuando la ficha
        del proveedor lista un EAN distinto al buscado (el producto se localiza
        por `ean`, pero se publica con `ean_publicar`)."""
    if not prestashop_configurado():
        return {"ok": False, "error": "PrestaShop no está configurado."}

    stock = _normalizar_stock(stock)
    modo_venta = _normalizar_modo_venta(modo_venta)

    # PVP (precio de venta CON IVA) opcional, introducido a mano.
    precio_venta = None
    if precio_con_iva is not None and str(precio_con_iva).strip() != "":
        try:
            precio_venta = float(str(precio_con_iva).replace(",", "."))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Precio con IVA no válido."}

    # Activo (opcional).
    activar = None
    if activo is not None and str(activo).strip() != "":
        activar = str(activo).strip().lower() in ("1", "true", "on", "si", "sí", "yes")

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
        # forzar=True: si el usuario llega aquí es porque ya vio la ficha (quizá
        # tras pulsar "ver de todos modos"), así que no repetimos el guard de EAN.
        raw = buscar_cs(ean, forzar=True)
        if not raw or not raw.get("encontrado"):
            return {"ok": False, "error": "No se encontró el producto en Liderpapel."}
        payload = _mapear_cs_a_prestashop(raw)
        # Las imágenes de Liderpapel están tras Akamai: PrestaShop no puede
        # descargarlas por URL. Las bajamos aquí con la sesión "impersonate"
        # y se envían como data URI (base64) dentro del propio JSON.
        with _cs["lock"]:
            ses_cs = _cs["ses"]
            payload["imagenes"] = cs_producto.descargar_imagenes_b64(
                raw.get("imagenes") or [], sesion=ses_cs)
        # Proveedor distinto para Liderpapel ("Comercial del sur").
        id_proveedor = cfg.get("PRESTASHOP_PROVEEDOR_CS", "")
    else:
        return {"ok": False, "error": f"Proveedor no válido: {proveedor}"}

    # EAN a publicar: si se indica uno (p. ej. el buscado, cuando la ficha del
    # proveedor lista otro distinto), se usa ese en lugar del de la ficha.
    if ean_publicar is not None and str(ean_publicar).strip() != "":
        nuevo_ean = "".join(ch for ch in str(ean_publicar) if ch.isdigit())
        if not nuevo_ean:
            return {"ok": False, "error": "EAN a publicar no válido."}
        payload["ean"] = nuevo_ean

    # Campos opcionales comunes a ambos proveedores.
    if stock is not None:
        payload["stock"] = stock
    if modo_venta is not None:
        payload["modo_venta"] = modo_venta
    if precio_venta is not None:
        payload["precio_venta_con_iva"] = precio_venta
    if activar is not None:
        payload["activo"] = activar

    try:
        from prestashop_client import PrestashopClient
        res = PrestashopClient().crear_producto(
            payload, id_proveedor=id_proveedor, id_impuestos=id_impuestos)
        return {"ok": True, "id_product": res.get("id_product"),
                "nombre": payload.get("nombre"),
                "stock": res.get("stock"), "modo_venta": res.get("modo_venta"),
                "activo": res.get("activo")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
