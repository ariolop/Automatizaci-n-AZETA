"""
hub.py — App unificada (menú + montaje de las 3 herramientas)
=============================================================
Reúne en un solo servicio, cada una bajo su prefijo de URL:

    /            -> Menú de inicio (esta app)
    /azeta       -> AZETA Manager        (app.py de la raíz)
    /cs          -> CS Papelería / Liderpapel  (cspapeleria/app.py)
    /escaner     -> Escáner de código de barras (scanner/scanner_app.py)

Cada herramienta sigue siendo su propia app Flask intacta; se montan con
DispatcherMiddleware, que gestiona el prefijo automáticamente (los url_for de
cada app generan rutas con su prefijo correcto).

Para que las tres convivan en un mismo proceso sin colisión de nombres de
módulo (config/db/lote/monitor existían por duplicado), CS Papelería se ha
convertido en paquete (cspapeleria.*) mientras AZETA sigue con módulos planos.

Arranque en producción (Render):
    gunicorn wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4

Arranque en local (con HTTPS para la cámara del móvil):
    python hub.py
    -> https://IP-DE-TU-PC:5000
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, render_template, Response, request
from werkzeug.middleware.dispatcher import DispatcherMiddleware

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- #
# Carga de los .env (igual criterio que el escáner): raíz + cspapeleria + scanner.
# En Render las variables llegan del entorno; setdefault no las pisa.
# --------------------------------------------------------------------------- #
def _cargar_env(ruta: Path) -> None:
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env(ROOT / ".env")
_cargar_env(ROOT / "cspapeleria" / ".env")
_cargar_env(ROOT / "scanner" / ".env")


# --------------------------------------------------------------------------- #
# Importar las tres apps (el orden no colisiona: AZETA plano, CS paquete).
# --------------------------------------------------------------------------- #
from buscador import app as buscador_mod         # noqa: E402  Búsqueda + publicar unificada
from monitor_app import app as monitor_mod       # noqa: E402  Monitor unificado
from configuracion import app as config_mod      # noqa: E402  Configuración central
import app as azeta_mod                          # noqa: E402  AZETA (raíz)
from cspapeleria import app as cs_mod            # noqa: E402  CS Papelería / Liderpapel
from scanner import scanner_app as scanner_mod   # noqa: E402  Escáner

buscador_app = buscador_mod.app
monitor_app = monitor_mod.app
config_app = config_mod.app
azeta_app = azeta_mod.app
cs_app = cs_mod.app
scanner_app = scanner_mod.app


# --------------------------------------------------------------------------- #
# App del menú (landing).
# --------------------------------------------------------------------------- #
hub = Flask(__name__, template_folder="hub_templates")

APPS = [
    {"slug": "buscar",  "url": "/buscar",  "nombre": "Buscar y publicar (unificado)",
     "desc": "Busca un EAN y compara AZETA y Liderpapel a la vez; publica en PrestaShop el que elijas.",
     "icon": "🔎", "destacado": True},
    {"slug": "monitor", "url": "/monitor", "nombre": "Monitor unificado",
     "desc": "Vigila disponibilidad y stock de AZETA y Liderpapel en una sola lista, con su etiqueta de proveedor.",
     "icon": "📊"},
    {"slug": "escaner", "url": "/escaner", "nombre": "Escáner de código de barras",
     "desc": "Escanea un EAN con la cámara del móvil y compara AZETA vs Liderpapel al instante.",
     "icon": "📷"},
    {"slug": "config",  "url": "/config",  "nombre": "Configuración",
     "desc": "PrestaShop y credenciales de AZETA y Liderpapel, todo en un solo sitio.",
     "icon": "⚙️"},
    {"slug": "azeta",   "url": "/azeta",   "nombre": "AZETA Manager",
     "desc": "Herramienta específica de AZETA: búsqueda individual y carga por lotes.",
     "icon": "📦"},
    {"slug": "cs",      "url": "/cs",      "nombre": "CS Papelería / Liderpapel",
     "desc": "Herramienta específica de Liderpapel: búsqueda individual y carga por lotes.",
     "icon": "🗂️"},
]


@hub.route("/")
def menu():
    return render_template("menu.html", apps=APPS)


@hub.route("/salud")
def salud():
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Montaje por prefijo.
# --------------------------------------------------------------------------- #
_mounted = DispatcherMiddleware(hub, {
    "/buscar": buscador_app,
    "/monitor": monitor_app,
    "/config": config_app,
    "/azeta": azeta_app,
    "/cs": cs_app,
    "/escaner": scanner_app,
})


# --------------------------------------------------------------------------- #
# Protección global por contraseña (Basic Auth) para TODO el conjunto.
# Antes solo el escáner pedía login; ahora AZETA (que puede publicar en
# PrestaShop) y CS también quedan protegidas. Si no defines APP_PASSWORD, el
# conjunto queda abierto (cómodo para pruebas en local).
# --------------------------------------------------------------------------- #
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


def _auth_ok(environ) -> bool:
    from base64 import b64decode
    cabecera = environ.get("HTTP_AUTHORIZATION", "")
    if not cabecera.startswith("Basic "):
        return False
    try:
        usuario, _, clave = b64decode(cabecera[6:]).decode("utf-8").partition(":")
    except Exception:  # noqa: BLE001
        return False
    return usuario == APP_USER and clave == APP_PASSWORD


def application(environ, start_response):
    """Middleware WSGI: exige Basic Auth (si hay APP_PASSWORD) salvo /salud."""
    if APP_PASSWORD and environ.get("PATH_INFO", "") != "/salud":
        if not _auth_ok(environ):
            start_response("401 Unauthorized", [
                ("WWW-Authenticate", 'Basic realm="Herramientas AZETA"'),
                ("Content-Type", "text/plain; charset=utf-8"),
            ])
            return [b"Acceso restringido."]
    return _mounted(environ, start_response)


# --------------------------------------------------------------------------- #
# Arranque local con HTTPS (la cámara del móvil solo funciona sobre HTTPS).
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from werkzeug.serving import run_simple

    puerto = int(os.environ.get("PORT", 5000))
    ctx = None
    try:
        from scanner import gen_cert
        cert = ROOT / "scanner" / "cert.pem"
        key = ROOT / "scanner" / "key.pem"
        if not (cert.exists() and key.exists()):
            gen_cert.generar(cert, key)
        ctx = (str(cert), str(key))
    except Exception as e:  # noqa: BLE001
        print(f"[aviso] Sin certificado HTTPS ({e}); arranco en HTTP (la cámara "
              f"del móvil no funcionará, solo en localhost).")

    print("=" * 64)
    print("  Herramientas AZETA — App unificada")
    print(f"  Menú:   {'https' if ctx else 'http'}://IP-DE-TU-PC:{puerto}/")
    print("  /azeta  /cs  /escaner")
    print("=" * 64)
    run_simple("0.0.0.0", puerto, application,
               ssl_context=ctx, threaded=True, use_reloader=False)
