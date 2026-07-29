"""
scanner_app.py
==============
Escáner de códigos de barras (EAN) para el móvil que consulta EN PARALELO
AZETA Distribuciones y el B2B de Liderpapel / CS Papelería, y muestra ambas
fichas en pantalla. Si no aparece en ninguno, muestra error + el EAN buscado.

Reutiliza los scrapers ya existentes:
  - ../azeta_producto.py       -> buscar_producto(ean)      (AZETA)
  - ../cspapeleria/cs_producto.py -> buscar_producto(ean)   (Liderpapel)

La cámara del navegador solo funciona sobre HTTPS (o localhost). Por eso el
servidor arranca con un certificado autofirmado (scanner/cert.pem + key.pem),
que se genera solo la primera vez (ver gen_cert.py / run_scanner.bat).

Uso:
    pip install -r requirements.txt
    python scanner_app.py
    -> abre en el móvil (misma WiFi):  https://IP-DE-TU-PC:5002
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

# --------------------------------------------------------------------------- #
# Rutas y carga de los dos .env (AZETA en la raíz, CS en cspapeleria/)
# --------------------------------------------------------------------------- #
BASE = Path(__file__).resolve().parent          # .../scanner
ROOT = BASE.parent                              # .../Automatización AZETA
CS_DIR = ROOT / "cspapeleria"


def _cargar_env(ruta: Path) -> None:
    """Carga un .env sencillo en os.environ (sin pisar variables ya definidas)."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env(BASE / ".env")       # config del escáner: SUPABASE_*, APP_USER/APP_PASSWORD
_cargar_env(ROOT / ".env")       # AZETA_USER / AZETA_PASSWORD / ...
_cargar_env(CS_DIR / ".env")     # CS_USER / CS_PASS / CS_IMPERSONATE / ...

# La raíz del repo debe estar en sys.path para importar la lógica común.
sys.path.insert(0, str(ROOT))

# Toda la lógica de búsqueda/normalización/comparación vive en busqueda_comun
# (compartida con la página unificada /buscar), para no duplicarla.
import busqueda_comun as bc                                    # noqa: E402
try:
    from . import historial                                    # como paquete (hub)
except ImportError:                                            # ejecutado suelto
    import historial                                           # noqa: E402  registro en Supabase (opcional)

# --------------------------------------------------------------------------- #
# Flask
# --------------------------------------------------------------------------- #
app = Flask(__name__)

# --- Protección con contraseña básica (solo si se define APP_PASSWORD) --------
# En local, sin esas variables, la app queda abierta (cómodo para pruebas).
# En el servidor público, define APP_USER / APP_PASSWORD y pedirá login.
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.before_request
def _proteger():
    # La autenticación ahora es global (sesión del hub). Se deja como no-op
    # para no duplicar el prompt de Basic Auth cuando corre bajo el hub.
    return None


@app.route("/")
def index():
    return render_template("scanner.html")


@app.route("/api/buscar")
def api_buscar():
    ean = (request.args.get("ean") or "").strip()
    if not ean:
        return jsonify({"ok": False, "error": "Falta el parámetro 'ean'."}), 400

    forzar = (request.args.get("forzar") or "").lower() in ("1", "true", "si", "sí", "yes")

    # CACHÉ: si no se fuerza, intenta servir el último escaneo guardado de ese EAN.
    if not forzar:
        cache = historial.buscar_cache(ean)
        if cache:
            p = cache["payload"]
            return jsonify({
                "ok": True,
                "ean": ean,
                "encontrado": p.get("encontrado", True),
                "azeta": p.get("azeta"),
                "cs": p.get("cs"),
                "comparacion": p.get("comparacion"),
                "cache": True,
                "cache_fecha": cache.get("creado_at"),
            })

    # Búsqueda en vivo: los dos proveedores en paralelo (lógica común).
    res = bc.buscar(ean, con_azeta=True, con_cs=True)
    azeta, cs, comparacion = res["azeta"], res["cs"], res["comparacion"]

    # Registro en Supabase (best-effort, en segundo plano; no bloquea la respuesta
    # ni falla la búsqueda si Supabase no está configurado o da error).
    historial.registrar_async(ean, azeta, cs, comparacion)

    return jsonify({
        "ok": True,
        "ean": ean,
        "encontrado": res["encontrado"],
        "azeta": azeta,
        "cs": cs,
        "comparacion": comparacion,
        "cache": False,
    })


@app.route("/historial")
def pagina_historial():
    return render_template("historial.html", activo=historial.activo())


@app.route("/api/historial")
def api_historial():
    try:
        limite = min(int(request.args.get("limite", 100)), 500)
    except ValueError:
        limite = 100
    return jsonify({"ok": True, "activo": historial.activo(),
                    "escaneos": historial.ultimos(limite)})


@app.route("/salud")
def salud():
    return jsonify({"ok": True})


def _ssl_context():
    """Devuelve (cert, key) si existen; si no, genera un autofirmado; si no puede,
    cae a 'adhoc' (requiere pyOpenSSL). Sin HTTPS la cámara del móvil no funciona."""
    cert = BASE / "cert.pem"
    key = BASE / "key.pem"
    if cert.exists() and key.exists():
        return (str(cert), str(key))
    try:
        try:
            from . import gen_cert
        except ImportError:
            import gen_cert
        gen_cert.generar(cert, key)
        return (str(cert), str(key))
    except Exception as e:                       # noqa: BLE001
        print(f"[aviso] No se pudo generar certificado ({e}); uso 'adhoc'.")
        return "adhoc"


if __name__ == "__main__":
    ctx = _ssl_context()
    puerto = int(os.environ.get("SCANNER_PORT", 5002))
    print("=" * 60)
    print("  Escáner AZETA + Liderpapel")
    print(f"  Abre en el móvil (misma WiFi):  https://IP-DE-TU-PC:{puerto}")
    print("  (acepta el aviso de certificado la primera vez)")
    print("=" * 60)
    app.run(host="0.0.0.0", port=puerto, ssl_context=ctx, threaded=True)
