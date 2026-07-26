"""
config.py
---------
Lectura/escritura EN CALIENTE del archivo .env (sin reiniciar la app).

leer_config()  -> dict con los valores actuales del .env (se lee el fichero cada vez).
guardar_config(cambios) -> actualiza esas claves en el .env conservando el resto.
"""

from __future__ import annotations

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
    "PRESTASHOP_PROVEEDOR": "",        # id del proveedor a asignar a los productos
    "PRESTASHOP_IMPUESTOS": "",        # id_tax_rules_group a aplicar a los productos
    "APLICAR_RECARGO": "1",            # aplicar recargo de equivalencia al coste real
}


def leer_config() -> dict:
    """Lee el .env directamente del disco (siempre fresco)."""
    valores = dict(CLAVES)  # arranca con los defaults
    if ENV_PATH.exists():
        for linea in ENV_PATH.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
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
