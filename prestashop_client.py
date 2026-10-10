"""
prestashop_client.py
---------------------
Cliente HTTP que habla con el módulo PrestaShop 9 'azetaconnector'.

Lee la configuración EN CALIENTE desde config.leer_config() (no requiere reiniciar).

Endpoints del módulo (autenticados por token):
  - controller=crear    -> crea un producto desactivado a partir del JSON del scraper
  - controller=listado  -> devuelve los productos (id, ean13, nombre, activo)
"""

from __future__ import annotations

import requests

import config

# Nombre del módulo en PrestaShop (parte de la URL de los endpoints).
MODULO = "aplproveedoresconector"


class PrestashopError(Exception):
    pass


import re as _re
import time as _time

_CACHE_OPC = {"t": 0.0, "datos": None}
_RE_21 = _re.compile(r"(?<![\d.,])21(?:[.,]0+)?\s*%")


def _limpiar_nombre(txt, maximo: int) -> str | None:
    """PrestaShop rechaza <>;=#{} en nombres de producto y fabricante."""
    if txt is None:
        return None
    t = _re.sub(r"\s+", " ", _re.sub(r"[<>;=#{}]", " ", str(txt))).strip()
    return t[:maximo] if t else t


class PrestashopClient:
    def __init__(self):
        c = config.leer_config()
        self.url = (c.get("PRESTASHOP_URL", "") or "").rstrip("/")
        self.token = c.get("PRESTASHOP_TOKEN", "") or ""
        try:
            self.categoria_defecto = int(c.get("PRESTASHOP_CATEGORIA_DEFECTO", "2") or 2)
        except ValueError:
            self.categoria_defecto = 2
        self.id_proveedor = c.get("PRESTASHOP_PROVEEDOR_AZETA", "") or ""
        self.id_impuestos = c.get("PRESTASHOP_IMPUESTOS", "") or ""
        if not self.url or not self.token:
            raise PrestashopError("Falta configurar PRESTASHOP_URL y PRESTASHOP_TOKEN.")

    def _endpoint(self, controller: str) -> str:
        return f"{self.url}/index.php?fc=module&module={MODULO}&controller={controller}"

    def actualizar_producto(self, id_product=None, ean=None, cambios: dict | None = None,
                            timeout=(5, 30)) -> dict:
        """Actualiza EAN13 y/o nombre de un producto existente (por id o por ean)."""
        payload = {"token": self.token, "producto": cambios or {}}
        if id_product:
            payload["id_product"] = int(id_product)
        if ean:
            payload["ean"] = str(ean)
        try:
            r = requests.post(self._endpoint("actualizar"), json=payload, timeout=timeout)
        except requests.RequestException as e:
            raise PrestashopError(f"No se pudo conectar con PrestaShop: {e}")
        return self._parse(r)

    def crear_producto(self, datos: dict, id_proveedor=None, id_impuestos=None,
                       timeout=(5, 100)) -> dict:
        """Envía la ficha del scraper al módulo para crear el producto.

        id_proveedor: si se indica, sobrescribe el proveedor por defecto de la
        configuración (para asignar un proveedor distinto según el origen:
        AZETA vs Liderpapel / Comercial del sur).
        id_impuestos: si se indica, sobrescribe la regla de IVA por defecto.

        El producto se crea DESACTIVADO salvo que 'datos' traiga activo=True, y el
        precio de venta se puede fijar a mano con 'precio_venta_sin_iva'."""
        prov = id_proveedor if id_proveedor is not None else self.id_proveedor
        imp = id_impuestos if id_impuestos not in (None, "") else self.id_impuestos
        if not str(imp).isdigit() or int(imp) <= 0:
            # Sin regla (p. ej. tras un reinicio en Render, o desde lote/escáner):
            # la del 21 %. Antes iba a 0 y el PVP se guardaba con el IVA dentro.
            tasa = 21
            if datos.get("iva_pct") is not None and not datos.get("iva_estimado"):
                try:
                    tasa = float(datos["iva_pct"])
                except (TypeError, ValueError):
                    tasa = 21
            imp = self.id_impuestos_por_tasa(tasa) or self.id_impuestos_por_tasa(21) or imp
        payload = {
            "token": self.token,
            "id_categoria": self.categoria_defecto,
            "id_proveedor": int(prov) if str(prov).isdigit() else 0,
            "id_impuestos": int(imp) if str(imp).isdigit() else 0,
            "producto": {
                "nombre": _limpiar_nombre(datos.get("nombre"), 128),
                "ean13": datos.get("ean"),
                "precio_sin_iva": datos.get("precio_unidad_sin_iva") or datos.get("precio_sin_iva"),
                "precio_venta_con_iva": datos.get("precio_venta_con_iva"),  # PVP a mano (con IVA)
                "coste_real": datos.get("coste_real_unidad"),   # coste con IVA + recargo
                "pvp": datos.get("pvp_recomendado"),
                "iva_pct": datos.get("iva_pct"),
                "unidades_venta": datos.get("unidades_venta"),
                "fabricante": _limpiar_nombre(datos.get("fabricante"), 64),
                "descripcion": datos.get("descripcion"),
                "situacion": datos.get("situacion"),
                "dropshipping": datos.get("dropshipping", True),
                "activo": datos.get("activo", False),
                "imagenes": datos.get("imagenes") or [],
                # Opcionales: si van a None el módulo no los toca.
                "stock": datos.get("stock"),
                "modo_venta": datos.get("modo_venta"),
            },
        }
        try:
            r = requests.post(self._endpoint("crear"), json=payload, timeout=timeout)
        except requests.RequestException as e:
            raise PrestashopError(f"No se pudo conectar con PrestaShop: {e}")
        return self._parse(r)

    def obtener_ficha(self, ean, timeout=(5, 12)) -> dict:
        """Ficha resumida de un producto por EAN (nombre, precio, imagen, activo)."""
        try:
            r = requests.post(self._endpoint("ficha"),
                              json={"token": self.token, "ean": str(ean)}, timeout=timeout)
        except requests.RequestException as e:
            raise PrestashopError(f"No se pudo conectar con PrestaShop: {e}")
        return self._parse(r)

    def listar_productos(self, timeout: int = 90, precio: bool = True,
                         solo_con_ean: bool = True) -> list[dict]:
        """Productos de la tienda. precio=False evita calcular el PVP de cada
        producto (mucho más rápido con miles de productos)."""
        try:
            r = requests.post(self._endpoint("listado"),
                              json={"token": self.token, "precio": precio,
                                    "solo_con_ean": solo_con_ean}, timeout=timeout)
        except requests.RequestException as e:
            raise PrestashopError(f"No se pudo conectar con PrestaShop: {e}")
        return self._parse(r).get("productos", [])

    def comprobar_eans(self, eans: list, timeout=(5, 15)) -> dict:
        """Devuelve {ean: {id, activo, nombre, precio}} de los EANs que YA existen
        en la tienda. 'precio' es el PVP con IVA incluido (módulo >= 1.3.0)."""
        eans = [str(e) for e in eans if e]
        if not eans:
            return {}
        try:
            r = requests.post(self._endpoint("comprobar"),
                              json={"token": self.token, "eans": eans}, timeout=timeout)
        except requests.RequestException as e:
            raise PrestashopError(f"No se pudo conectar con PrestaShop: {e}")
        return self._parse(r).get("existentes", {})

    def id_impuestos_por_tasa(self, tasa: float = 21) -> int | None:
        """id de la regla de impuestos del tipo indicado (por defecto 21 %).
        Usa el 'tasa' que da el módulo (>= 1.6.0) o, si no, el nombre
        ("... (21%)"). Cachea las opciones 1 hora."""
        ahora = _time.time()
        if _CACHE_OPC["datos"] is None or ahora - _CACHE_OPC["t"] > 3600:
            try:
                _CACHE_OPC["datos"] = self.obtener_opciones()
                _CACHE_OPC["t"] = ahora
            except Exception:  # noqa: BLE001
                return None
        imps = (_CACHE_OPC["datos"] or {}).get("impuestos") or []
        for t in imps:
            if t.get("tasa") is not None and abs(float(t["tasa"]) - float(tasa)) < 0.01:
                return int(t["id"])
        if int(tasa) == 21:
            for t in imps:
                if _RE_21.search(str(t.get("nombre") or "")):
                    return int(t["id"])
        return None

    def obtener_opciones(self, timeout=(5, 15)) -> dict:
        """Devuelve {'proveedores': [...], 'impuestos': [...]} desde el módulo."""
        try:
            r = requests.post(self._endpoint("opciones"), json={"token": self.token}, timeout=timeout)
        except requests.RequestException as e:
            raise PrestashopError(f"No se pudo conectar con PrestaShop: {e}")
        data = self._parse(r)
        return {
            "proveedores": data.get("proveedores", []),
            "impuestos": data.get("impuestos", []),
        }

    @staticmethod
    def _parse(r: requests.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            raise PrestashopError(
                f"Respuesta no-JSON de PrestaShop (HTTP {r.status_code}): {r.text[:200]}"
            )
        if not data.get("success", False):
            raise PrestashopError(data.get("error", "Error desconocido en PrestaShop"))
        return data


def prestashop_configurado() -> bool:
    return config.prestashop_configurado()
