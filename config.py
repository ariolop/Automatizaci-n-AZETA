"""
config.py
---------
Lectura/escritura EN CALIENTE del archivo .env (sin reiniciar la app).

leer_config()  -> dict con los valores actuales del .env (se lee el fichero cada vez).
guardar_config(cambios) -> actualiza esas claves en el .env conservando el resto.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"

# Claves gestionadas y sus valores por defecto
CLAVES = {
    "AZETA_USER": "",
    "AZETA_PASSWORD": "",
    "AZETA_BASE_URL": "https://www.azetadistribuciones.es",
    "AZETA_LOGIN_URL": "https://www.azetadistribuciones.es/html/identificacion/autentifica.php",
    "PRESTASHOP_URL": "",
    "PRESTASHOP_TOKEN": "",
    "PRESTASHOP_CATEGORIA_DEFECTO": "2",
    "PRESTASHOP_PROVEEDOR": "",        # id del proveedor AZETA a asignar a los productos
    "PRESTASHOP_PROVEEDOR_CS": "",     # id del proveedor "Comercial del sur" (Liderpapel)
    "PRESTASHOP_IMPUESTOS": "",        # id_tax_rules_group a aplicar a los productos
    "APLICAR_RECARGO": "1",            # aplicar recargo de equivalencia al coste real
}


def leer_config() -> dict:
    """Valores de configuración. Precedencia: valor NO vacío del fichero .env >
    variable de entorno > valor por defecto. Un valor vacío en el .env no anula
    la variable de entorno (permite desplegar en la nube sin fichero .env)."""
    valores = dict(CLAVES)  # arranca con los defaults
    # Fallback a variables de entorno (para despliegues sin fichero .env).
    for clave in valores:
        env_val = os.environ.get(clave)
        if env_val not in (None, ""):
            valores[clave] = env_val
    # El fichero .env, si existe, tiene prioridad (uso local + Config en caliente),
    # pero solo con valores NO vacíos (un vacío no debe pisar env/defecto).
    if ENV_PATH.exists():
        for linea in ENV_PATH.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
            if valor == "":
                continue
            if clave in valores:
                valores[clave] = valor
            else:
                valores[clave] = valor
    return valores


def guardar_config(cambios: dict):
    """Actualiza las claves indicadas en el .env, conservando comentarios y orden."""
    lineas = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    pendientes = dict(cambios)

    nuevas = []
    for linea in lineas:
        stripped = linea.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            clave = stripped.partition("=")[0].strip()
            if clave in pendientes:
                nuevas.append(f"{clave}={pendientes.pop(clave)}")
                continue
        nuevas.append(linea)

    # Claves nuevas que no existían en el fichero
    for clave, valor in pendientes.items():
        nuevas.append(f"{clave}={valor}")

    ENV_PATH.write_text("\n".join(nuevas) + "\n", encoding="utf-8")


def prestashop_configurado() -> bool:
    c = leer_config()
    return bool(c.get("PRESTASHOP_URL") and c.get("PRESTASHOP_TOKEN"))


def aplicar_recargo() -> bool:
    return leer_config().get("APLICAR_RECARGO", "1") not in ("0", "", "false", "False", "no")
