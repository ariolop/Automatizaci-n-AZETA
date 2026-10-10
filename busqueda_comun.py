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

import copy
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import azeta_producto
from azeta_login import AzetaSession, AzetaError
from cspapeleria import cs_producto
from cspapeleria.cs_login import CSSession, CSError

_pool = ThreadPoolExecutor(max_workers=8)

# Sesiones persistentes por proveedor (login perezoso; re-login si caduca).
_azeta = {"ses": None, "lock": threading.Lock()}
_cs = {"ses": None, "lock": threading.Lock()}

# Máximo que una petición espera a que quede libre la sesión de un proveedor
# (antes esperaba sin límite y podía dejar colgados los 4 hilos del servidor).
ESPERA_SESION = 45

# Caché corta de fichas del proveedor: publicar / vigilar / vincular reutilizan
# la búsqueda que el usuario acaba de ver en vez de volver a consultar.
CACHE_TTL = 600
_cache: dict = {}
_cache_lock = threading.Lock()


def _cache_get(clave):
    with _cache_lock:
        v = _cache.get(clave)
        if v and time.time() - v[0] < CACHE_TTL:
            return copy.deepcopy(v[1])
    return None


def _cache_put(clave, datos: dict) -> None:
    if not datos or not datos.get("encontrado") or datos.get("error"):
        return
    with _cache_lock:
        if len(_cache) > 500:          # poda: fuera lo caducado (o lo más viejo)
            ahora = time.time()
            for k in [k for k, v in _cache.items() if ahora - v[0] >= CACHE_TTL] or \
                     sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
        _cache[clave] = (time.time(), copy.deepcopy(datos))


def _ocupado(prov: str) -> dict:
    return {"proveedor": prov, "encontrado": False,
            "error": f"{prov} está ocupado con otra consulta; inténtalo en unos segundos."}


# --------------------------------------------------------------------------- #
# Búsqueda (nunca lanzan: devuelven dict con 'error' si algo falla)
# --------------------------------------------------------------------------- #
def buscar_azeta(ean: str, usar_cache: bool = False) -> dict:
    """usar_cache=True: vale una ficha consultada hace < CACHE_TTL segundos."""
    if usar_cache:
        c = _cache_get(("AZETA", str(ean)))
        if c:
            return c
    if not _azeta["lock"].acquire(timeout=ESPERA_SESION):
        return _ocupado("AZETA")
    try:
        datos = _buscar_azeta(ean)
    finally:
        _azeta["lock"].release()
    _cache_put(("AZETA", str(ean)), datos)
    return datos


def _buscar_azeta(ean: str) -> dict:
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


def buscar_cs(ean: str, forzar: bool = False, usar_cache: bool = False) -> dict:
    """usar_cache=True: vale una ficha consultada hace < CACHE_TTL segundos
    (con forzar=True sirve también la de una búsqueda normal que sí coincidió)."""
    if usar_cache:
        c = _cache_get(("CS", str(ean), bool(forzar)))
        if c is None and forzar:
            c = _cache_get(("CS", str(ean), False))
        if c:
            return c
    if not _cs["lock"].acquire(timeout=ESPERA_SESION):
        return _ocupado("Liderpapel")
    try:
        datos = _buscar_cs(ean, forzar)
    finally:
        _cs["lock"].release()
    _cache_put(("CS", str(ean), bool(forzar)), datos)
    return datos


def _buscar_cs(ean: str, forzar: bool = False) -> dict:
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
        "iva_pct": d.get("iva_pct"),
        "iva_estimado": bool(d.get("iva_estimado")),
        "descripcion": d.get("descripcion"),
        "imagenes": d.get("imagenes") or [],
        "url": d.get("url_ficha"),
        "extra": {
            "IVA %": (f"{d.get('iva_pct')} (estimado)" if d.get("iva_estimado")
                      else d.get("iva_pct")),
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
        "iva_pct": d.get("iva"),
        "iva_estimado": True,            # Liderpapel no publica el IVA
        "descripcion": d.get("descripcion"),
        "imagenes": d.get("imagenes") or [],
        "url": None,
        "extra": {
            "IVA % (est.)": d.get("iva"),
            "Stock": d.get("stock"),
        },
    }


def _alinear_iva_cs(azeta: dict | None, cs: dict | None) -> None:
    """Liderpapel no publica el IVA (se estima al 21 %). Si AZETA sí lo da para
    el mismo producto y es otro (p. ej. 4 % en libros), se recalcula el coste
    de Liderpapel con ese IVA para que la comparación sea justa."""
    if not (azeta and cs and azeta.get("encontrado") and cs.get("encontrado")):
        return
    iva = azeta.get("iva_pct")
    if iva is None or azeta.get("iva_estimado") or cs.get("precio_neto") is None:
        return
    if cs.get("iva_pct") is not None and float(cs["iva_pct"]) == float(iva):
        return
    coste, iva2, _rec = cs_producto.calcular_coste(cs["precio_neto"], iva)
    cs["coste_real"] = coste
    cs["iva_pct"] = iva2
    cs["iva_estimado"] = False
    cs.setdefault("extra", {})
    cs["extra"].pop("IVA % (est.)", None)
    cs["extra"]["IVA % (de AZETA)"] = iva2


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
    _alinear_iva_cs(azeta, cs)
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
        "iva_estimado": True,             # Liderpapel no publica el IVA
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


def ya_en_prestashop_varios(eans: list[str]) -> dict:
    """{ean: info|None} para varios EAN en UNA sola llamada a PrestaShop."""
    eans = [e for e in dict.fromkeys(str(e) for e in eans if e)]
    if not eans or not prestashop_configurado():
        return {e: None for e in eans}
    try:
        from prestashop_client import PrestashopClient
        ex = PrestashopClient().comprobar_eans(eans) or {}
    except Exception:  # noqa: BLE001
        ex = {}
    return {e: ex.get(e) for e in eans}


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


def _solo_digitos(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _control_gtin(cuerpo: str) -> int:
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(cuerpo)))
    return (10 - total % 10) % 10


def validar_ean(valor) -> tuple[str | None, str | None]:
    """(ean_normalizado, None) si es un EAN-8 / UPC-A (12) / EAN-13 válido, o
    (None, motivo). Un GTIN-14 que empieza por 0 se convierte a EAN-13."""
    e = _solo_digitos(valor)
    if len(e) == 14 and e.startswith("0"):
        e = e[1:]
    if len(e) not in (8, 12, 13):
        if len(e) == 14:
            return None, (f"{e} es un GTIN-14 (código de caja/expositor), no un EAN de "
                          f"unidad: indica el EAN de la tienda.")
        return None, f"El EAN {e or '(vacío)'} no tiene 8, 12 ni 13 dígitos."
    if _control_gtin(e[:-1]) != int(e[-1]):
        return None, f"El EAN {e} tiene el dígito de control mal (¿errata?)."
    return e, None


def _expositor_en_uso(cli, exp: str, ean_loc: str | None = None, id_product=None) -> str | None:
    """Si 'exp' ya pertenece a OTRO producto de la tienda (como EAN13 o como EAN
    de expositor), devuelve el texto del error; si está libre, None."""
    try:
        ex = cli.comprobar_eans([e for e in (exp, ean_loc) if e]) or {}
    except Exception:  # noqa: BLE001  (si PrestaShop no responde, que decida el módulo)
        return None
    otro = ex.get(exp)
    if not otro:
        return None
    objetivo = id_product or (ex.get(ean_loc) or {}).get("id")
    try:
        if objetivo and int(otro.get("id") or 0) == int(objetivo):
            return None
    except (TypeError, ValueError):
        pass
    return (f"El EAN {exp} ya está en PrestaShop en otro producto "
            f"(ID {otro.get('id')} · {otro.get('nombre') or 'sin nombre'}).")


def _expositor_al_monitor(res: dict) -> bool:
    """Lleva al monitor el EAN de expositor que acaba de guardarse en PrestaShop."""
    try:
        import monitor_comun as mon   # perezoso: monitor_comun importa este módulo
        return mon.vincular_expositor(res.get("id_product"), res.get("ean13"),
                                      res.get("ean_expositor") or None)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return False


def asignar_expositor(ean_expositor, ean_tienda=None, id_product=None) -> dict:
    """Guarda (o quita, si llega vacío) el EAN de expositor de un producto de
    PrestaShop, localizado por id o por su EAN de tienda, y lo replica en el
    monitor. Devuelve {ok, id_product, ean13, ean_expositor} o {ok: False, error}."""
    if not prestashop_configurado():
        return {"ok": False, "error": "PrestaShop no está configurado."}
    exp = _solo_digitos(ean_expositor)
    tienda = _solo_digitos(ean_tienda)
    if not tienda and not id_product:
        return {"ok": False, "error": "Indica el EAN del producto en PrestaShop."}
    if exp and len(exp) < 8:
        return {"ok": False, "error": "EAN de expositor no válido."}
    if exp and exp == tienda:
        return {"ok": False, "error": "El EAN de expositor es el mismo que el de la tienda."}
    try:
        from prestashop_client import PrestashopClient
        cli = PrestashopClient()
        if exp:
            err = _expositor_en_uso(cli, exp, ean_loc=tienda or None, id_product=id_product)
            if err:
                return {"ok": False, "error": err}
        res = cli.actualizar_producto(id_product=id_product, ean=None if id_product else tienda,
                                      cambios={"ean_expositor": exp})
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Falta id_product" in msg or "no encontrado" in msg.lower():
            msg = f"No hay ningún producto en PrestaShop con el EAN {tienda}."
        return {"ok": False, "error": msg}
    res["monitor"] = _expositor_al_monitor(res)
    return {"ok": True, **res}


def actualizar_prestashop(ean: str | None = None, id_product=None,
                          precio=None, stock=None, ean13=None, id_impuestos=None,
                          activo=None, ean_expositor=None) -> dict:
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
        nuevo = _solo_digitos(ean13)
        if not nuevo:
            return {"ok": False, "error": "EAN no válido."}
        if nuevo != (ean or ""):
            nuevo, motivo = validar_ean(nuevo)
            if motivo:
                return {"ok": False, "error": motivo}
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
    # EAN de expositor: None = no tocar; "" = quitarlo.
    if ean_expositor is not None:
        exp = _solo_digitos(ean_expositor)
        if exp and len(exp) < 8:
            return {"ok": False, "error": "EAN de expositor no válido."}
        cambios["ean_expositor"] = exp

    if not cambios:
        return {"ok": False, "error": "Nada que actualizar."}

    try:
        from prestashop_client import PrestashopClient
        cli = PrestashopClient()
        if cambios.get("ean_expositor"):
            err = _expositor_en_uso(cli, cambios["ean_expositor"], ean_loc=ean, id_product=id_product)
            if err:
                return {"ok": False, "error": err}
        if cambios.get("ean13"):
            # Que el EAN nuevo no lo tenga ya OTRO producto (luego las búsquedas
            # por EAN devolverían uno cualquiera de los dos).
            err = _expositor_en_uso(cli, cambios["ean13"], ean_loc=ean, id_product=id_product)
            if err:
                return {"ok": False, "error": err}
        res = cli.actualizar_producto(id_product=id_product, ean=ean, cambios=cambios)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if "ean_expositor" in cambios:
        res["monitor"] = _expositor_al_monitor(res)
    return {"ok": True, **res}


def _vigilar_publicado(proveedor: str, ean_tienda: str, ficha: dict,
                       id_product, ean_expositor: str | None) -> bool:
    """Tras publicar: alta en el monitor con los datos del proveedor recién
    consultados, enlazada al producto de PrestaShop (id_prestashop).
    Si el EAN de la tienda no es el del proveedor, se guarda también como EAN
    de expositor en PrestaShop (si no, «Sincronizar» lo borraría del monitor).
    Nunca lanza: la publicación no debe fallar por el monitor."""
    try:
        import monitor_comun as mon   # perezoso: monitor_comun importa este módulo
        if not mon.activo() or not ean_tienda:
            return False
        if ean_expositor and id_product:
            try:
                from prestashop_client import PrestashopClient
                PrestashopClient().actualizar_producto(
                    id_product=id_product, cambios={"ean_expositor": ean_expositor})
            except Exception as e:  # noqa: BLE001
                print(f"[publicar] no se pudo guardar el EAN de expositor: {e}")
        return mon.vigilar_con_ficha(ean_tienda, proveedor, ficha, origen="publicado",
                                     id_prestashop=id_product, ean_expositor=ean_expositor)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return False


def vigilar(proveedor: str, ean: str, nombre: str | None = None) -> dict:
    """Botón «Vigilar»: consulta al proveedor (coste, precio, stock, estado) y da
    de alta la fila en el monitor ya rellena. Si el producto existe en
    PrestaShop, la deja enlazada (id_prestashop) y con el EAN de la tienda como
    clave (si coincide por EAN de expositor, el buscado pasa a ser el expositor)."""
    import monitor_comun as mon   # perezoso: monitor_comun importa este módulo
    if not mon.activo():
        return {"ok": False, "error": "El monitor (Supabase) no está configurado."}
    prov = mon.normalizar_proveedor(proveedor)
    if prov == "AZETA":
        ficha = norm_azeta(buscar_azeta(ean, usar_cache=True))
    elif prov == "Liderpapel":
        ficha = norm_cs(buscar_cs(ean, forzar=True, usar_cache=True))
    else:
        return {"ok": False, "error": f"Proveedor no válido: {proveedor}"}

    en_ps = ya_en_prestashop(ean) or {}
    clave, exp = ean, None
    if en_ps.get("por") == "expositor" and en_ps.get("ean13"):
        clave, exp = str(en_ps["ean13"]), ean
    id_ps = en_ps.get("id") or None

    if not ficha.get("encontrado"):
        # Proveedor caído o sin resultado: se da de alta igualmente (como antes)
        # y la próxima comprobación rellenará los datos.
        ok = mon.añadir(clave, prov, nombre=nombre, origen="manual",
                        id_prestashop=id_ps, ean_expositor=exp)
        if not ok:
            return {"ok": False, "error": "No se pudo guardar en el monitor."}
        return {"ok": True, "aviso": ficha.get("error") or f"No se pudo consultar {prov}; "
                "los datos se rellenarán en la próxima comprobación."}

    if nombre and not ficha.get("nombre"):
        ficha["nombre"] = nombre
    ok = mon.vigilar_con_ficha(clave, prov, ficha, origen="manual",
                               id_prestashop=id_ps, ean_expositor=exp)
    if not ok:
        return {"ok": False, "error": "No se pudo guardar en el monitor."}
    return {"ok": True, "coste_real": ficha.get("coste_real"),
            "estado": mon._estado_desde_ficha(ficha)}


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
        raw = buscar_azeta(ean, usar_cache=True)
        if not raw or not raw.get("encontrado"):
            return {"ok": False, "error": raw.get("error") if raw and raw.get("error")
                    else "No se encontró el producto en AZETA."}
        ficha = norm_azeta(raw)          # para el monitor (antes de tocar el payload)
        prov_monitor = "AZETA"
        payload = raw  # ya viene con las claves que espera el módulo
        id_proveedor = cfg.get("PRESTASHOP_PROVEEDOR_AZETA", "")
    elif prov in ("cs", "liderpapel"):
        # forzar=True: si el usuario llega aquí es porque ya vio la ficha (quizá
        # tras pulsar "ver de todos modos"), así que no repetimos el guard de EAN.
        raw = buscar_cs(ean, forzar=True, usar_cache=True)
        if not raw or not raw.get("encontrado"):
            return {"ok": False, "error": raw.get("error") if raw and raw.get("error")
                    else "No se encontró el producto en Liderpapel."}
        ficha = norm_cs(raw)             # para el monitor
        prov_monitor = "Liderpapel"
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
    if payload.get("ean"):
        ean_ok, motivo = validar_ean(payload["ean"])
        if motivo:
            return {"ok": False, "error": motivo}
        payload["ean"] = ean_ok

    # Campos opcionales comunes a ambos proveedores.
    if stock is not None:
        payload["stock"] = stock
    if modo_venta is not None:
        payload["modo_venta"] = modo_venta
    if precio_venta is not None:
        payload["precio_venta_con_iva"] = precio_venta
    if activar is not None:
        payload["activo"] = activar

    aviso = None
    try:
        from prestashop_client import PrestashopClient
        res = PrestashopClient().crear_producto(
            payload, id_proveedor=id_proveedor, id_impuestos=id_impuestos)
    except Exception as e:  # noqa: BLE001
        # Timeout o error 5xx con las imágenes: el producto puede haberse creado
        # igualmente. Si está en la tienda con ese EAN, seguimos (monitor,
        # expositor) en vez de dar un error que lleva a reintentar y a "ya existe".
        msg = str(e)
        recuperado = None
        if "Ya existe" not in msg and payload.get("ean"):
            try:
                time.sleep(2)
                ex = PrestashopClient().comprobar_eans([payload["ean"]]) or {}
                info = ex.get(str(payload["ean"]))
                if info and info.get("por", "ean13") == "ean13":
                    recuperado = info
            except Exception:  # noqa: BLE001
                recuperado = None
        if not recuperado:
            if "Ya existe" in msg:
                msg = "Ya existe un producto con ese EAN en PrestaShop."
            return {"ok": False, "error": msg}
        res = {"id_product": recuperado.get("id"), "activo": recuperado.get("activo")}
        aviso = ("PrestaShop tardó en responder, pero el producto se ha creado. "
                 "Revisa sus imágenes en la tienda.")

    # Todo lo que se publica entra en el monitor, ya con los datos del proveedor
    # (coste, precio neto, stock, estado) de la búsqueda que acabamos de hacer.
    # Si se publica con un EAN distinto al del proveedor, ese otro queda como
    # EAN de expositor (es el que se usará para consultar al proveedor).
    id_product = res.get("id_product")
    ean_tienda = "".join(ch for ch in str(payload.get("ean") or "") if ch.isdigit())
    ean_prov = "".join(ch for ch in str(ean) if ch.isdigit())
    ean_exp = ean_prov if (len(ean_prov) >= 8 and ean_tienda and ean_prov != ean_tienda) else None
    vigilado = _vigilar_publicado(prov_monitor, ean_tienda or ean_prov, ficha,
                                  id_product, ean_exp)
    return {"ok": True, "id_product": id_product,
            "nombre": payload.get("nombre"),
            "stock": res.get("stock"), "modo_venta": res.get("modo_venta"),
            "activo": res.get("activo"),
            "vigilado": vigilado, "ean_expositor": ean_exp,
            "coste_real": ficha.get("coste_real"),
            "imagenes_subidas": res.get("imagenes_subidas"),
            "imagenes_recibidas": res.get("imagenes_recibidas"),
            "aviso": aviso}
