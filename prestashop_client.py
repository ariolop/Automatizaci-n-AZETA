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
                            timeout: int = 40) -> dict:
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

    def crear_producto(self, datos: dict, id_proveedor=None, timeout: int = 40) -> dict:
        """Envía la ficha del scraper al módulo para crear el producto (desactivado).

        id_proveedor: si se indica, sobrescribe el proveedor por defecto de la
        configuración (para asignar un proveedor distinto según el origen:
        AZETA vs Liderpapel / Comercial del sur)."""
        prov = id_proveedor if id_proveedor is not None else self.id_proveedor
        payload = {
            "token": self.token,
            "id_categoria": self.categoria_defecto,
            "id_proveedor": int(prov) if str(prov).isdigit() else 0,
            "id_impuestos": int(self.id_impuestos) if str(self.id_impuestos).isdigit() else 0,
            "producto": {
                "nombre": datos.get("nombre"),
                "ean13": datos.get("ean"),
                "precio_sin_iva": datos.get("precio_unidad_sin_iva") or datos.get("precio_sin_iva"),
                "coste_real": datos.get("coste_real_unidad"),   # coste con IVA + recargo
                "pvp": datos.get("pvp_recomendado"),
                "iva_pct": datos.get("iva_pct"),
                "unidades_venta": datos.get("unidades_venta"),
                "fabricante": datos.get("fabricante"),
                "descripcion": datos.get("descripcion"),
                "situacion": datos.get("situacion"),
                "dropshipping": datos.get("dropshipping", True),
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

    def obtener_ficha(self, ean, timeout: int = 40) -> dict:
        """Ficha resumida de un producto por EAN (nombre, precio, imagen, activo)."""
        try:
            r = requests.post(self._endpoint("ficha"),
                              json={"token": self.token, "ean": str(ean)}, timeout=timeout)
        except requests.RequestException as e:
            raise PrestashopError(f"No se pudo conectar con PrestaShop: {e}")
        return self._parse(r)

    def listar_productos(self, timeout: int = 40) -> list[dict]:
        try:
            r = requests.post(self._endpoint("listado"), json={"token": self.token}, timeout=timeout)
        except requests.RequestException as e:
            raise PrestashopError(f"No se pudo conectar con PrestaShop: {e}")
        return self._parse(r).get("productos", [])

    def comprobar_eans(self, eans: list, timeout: int = 40) -> dict:
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

    def obtener_opciones(self, timeout: int = 40) -> dict:
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
