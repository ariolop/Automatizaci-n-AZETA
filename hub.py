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

from urllib.parse import quote
from http.cookies import SimpleCookie

from flask import Flask, render_template, request, redirect
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
import auth_comun as auth                         # noqa: E402  Autenticación por sesión
from buscador import app as buscador_mod         # noqa: E402  Búsqueda + publicar unificada
from lote_app import app as lote_mod             # noqa: E402  Lote unificado
from monitor_app import app as monitor_mod       # noqa: E402  Monitor unificado
from configuracion import app as config_mod      # noqa: E402  Configuración central
import app as azeta_mod                          # noqa: E402  AZETA (raíz)
from cspapeleria import app as cs_mod            # noqa: E402  CS Papelería / Liderpapel
from scanner import scanner_app as scanner_mod   # noqa: E402  Escáner

buscador_app = buscador_mod.app
lote_app = lote_mod.app
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
    {"slug": "lote",    "url": "/lote",    "nombre": "Lote unificado",
     "desc": "Procesa muchos EANs a la vez (texto o CSV/Excel) contra AZETA y Liderpapel; vigilar/publicar/CSV.",
     "icon": "📋"},
    {"slug": "monitor", "url": "/monitor", "nombre": "Monitor unificado",
     "desc": "Vigila disponibilidad y stock de AZETA y Liderpapel en una sola lista, con su etiqueta de proveedor.",
     "icon": "📊"},
    {"slug": "config",  "url": "/config",  "nombre": "Configuración",
     "desc": "PrestaShop y credenciales de AZETA y Liderpapel, todo en un solo sitio.",
     "icon": "⚙️"},
    # AZETA Manager (/azeta) y CS Papelería (/cs) siguen montadas (para las
    # redirecciones y la búsqueda individual) pero se ocultan del menú.
]


@hub.route("/")
def menu():
    return render_template("menu.html", apps=APPS)


@hub.route("/salud")
def salud():
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Login / logout (sesión por cookie firmada; usuarios en Supabase).
# --------------------------------------------------------------------------- #
@hub.route("/login", methods=["GET", "POST"])
def login():
    error = None
    nxt = request.args.get("next") or request.form.get("next") or "/"
    if not nxt.startswith("/"):
        nxt = "/"
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        if auth.verificar_credenciales(u, p):
            resp = redirect(nxt)
            resp.set_cookie(auth.COOKIE, auth.crear_cookie(u), max_age=auth.MAX_AGE,
                            httponly=True, samesite="Lax", secure=request.is_secure)
            return resp
        error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error, next=nxt)


@hub.route("/logout")
def logout():
    resp = redirect("/login")
    resp.delete_cookie(auth.COOKIE)
    return resp


# --------------------------------------------------------------------------- #
# Montaje por prefijo.
# --------------------------------------------------------------------------- #
_mounted = DispatcherMiddleware(hub, {
    "/buscar": buscador_app,
    "/lote": lote_app,
    "/monitor": monitor_app,
    "/config": config_app,
    "/azeta": azeta_app,
    "/cs": cs_app,
    "/escaner": scanner_app,
})


# --------------------------------------------------------------------------- #
# Protección global por SESIÓN (login de usuarios en Supabase).
# Se exige sesión válida en todo salvo /login, /logout y /salud. Si Supabase
# de usuarios no está configurado (p. ej. en local sin credenciales), el
# conjunto queda abierto para no bloquear el desarrollo.
# --------------------------------------------------------------------------- #
_LIBRES = ("/login", "/logout", "/salud")


def _cookie_de(environ) -> str | None:
    raw = environ.get("HTTP_COOKIE", "")
    if not raw:
        return None
    ck = SimpleCookie()
    try:
        ck.load(raw)
    except Exception:  # noqa: BLE001
        return None
    return ck[auth.COOKIE].value if auth.COOKIE in ck else None


def application(environ, start_response):
    """Middleware WSGI: exige sesión válida salvo rutas libres."""
    path = environ.get("PATH_INFO", "")
    if auth.activo() and path not in _LIBRES:
        usuario = auth.validar_cookie(_cookie_de(environ))
        if not usuario:
            # API -> 401 JSON; navegación -> redirección a /login
            if "/api/" in path or path.endswith("/api"):
                start_response("401 Unauthorized",
                               [("Content-Type", "application/json; charset=utf-8")])
                return [b'{"ok":false,"error":"No autorizado"}']
            destino = "/login?next=" + quote(path)
            start_response("302 Found",
                           [("Location", destino), ("Content-Type", "text/html; charset=utf-8")])
            return [b""]
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
