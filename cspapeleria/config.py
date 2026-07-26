"""
config.py — Lee/escribe el .env en caliente (sin reiniciar la app).
"""
import os

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

CLAVES = ["CS_USER", "CS_PASS", "CS_TIMEOUT", "CS_IMPERSONATE",
          "CS_IVA_DEFAULT", "CS_APLICAR_RECARGO"]


def leer_config():
    cfg = {}
    if os.path.exists(ENV_PATH):
        for linea in open(ENV_PATH, encoding="utf-8"):
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def guardar_config(nuevos):
    cfg = leer_config()
    cfg.update({k: v for k, v in nuevos.items() if v is not None})
    lineas = ["# Configuración CS Papelería / Liderpapel (editable desde la app)"]
    etiquetas = {
        "CS_USER": "Usuario del portal B2B",
        "CS_PASS": "Contraseña",
        "CS_TIMEOUT": "Timeout por petición (s)",
        "CS_IMPERSONATE": "Perfil TLS curl_cffi (chrome/chrome120/...)",
        "CS_IVA_DEFAULT": "IVA por defecto (el portal no lo publica)",
        "CS_APLICAR_RECARGO": "Aplicar recargo de equivalencia (1/0)",
    }
    for k in CLAVES:
        if k in etiquetas:
            lineas.append(f"# {etiquetas[k]}")
        lineas.append(f"{k}={cfg.get(k, '')}")
        lineas.append("")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    # aplicar en caliente al proceso actual
    for k, v in cfg.items():
        os.environ[k] = v
    return cfg
