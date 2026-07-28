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
import threading
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

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

# IMPORTANTE el orden: la raíz PRIMERO para que "import config" (que usa AZETA)
# resuelva al config.py correcto y no al de cspapeleria/.
sys.path.insert(0, str(CS_DIR))
sys.path.insert(0, str(ROOT))

# Importamos los scrapers ya existentes (sus cadenas de import no colisionan:
# azeta_producto -> azeta_login + config;  cs_producto -> cs_login).
import azeta_producto                                   # noqa: E402
import cs_producto                                      # noqa: E402
from azeta_login import AzetaSession, AzetaError        # noqa: E402
from cs_login import CSSession, CSError                 # noqa: E402

import historial                                        # noqa: E402  registro en Supabase (opcional)

# --------------------------------------------------------------------------- #
# Sesiones persistentes (login una vez; se re-loguea si caduca/falla)
# --------------------------------------------------------------------------- #
_azeta = {"ses": None, "lock": threading.Lock()}
_cs = {"ses": None, "lock": threading.Lock()}


def _buscar_azeta(ean: str) -> dict:
    """Consulta AZETA. Nunca lanza: devuelve dict (con 'error' si falla)."""
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
                _azeta["ses"] = None            # forzar re-login en el 2º intento
                if intento == 2:
                    return {"proveedor": "AZETA", "encontrado": False,
                            "error": f"AZETA no disponible: {e}"}
            except Exception as e:               # noqa: BLE001
                traceback.print_exc()
                return {"proveedor": "AZETA", "encontrado": False,
                        "error": f"Error consultando AZETA: {e}"}


def _buscar_cs(ean: str) -> dict:
    """Consulta Liderpapel/CS. Nunca lanza: devuelve dict (con 'error' si falla)."""
    with _cs["lock"]:
        for intento in (1, 2):
            try:
                if _cs["ses"] is None:
                    _cs["ses"] = CSSession()     # login perezoso en la 1ª petición
                datos = cs_producto.buscar_producto(ean, sesion=_cs["ses"])
                datos["proveedor"] = "Liderpapel"
                return datos
            except CSError as e:
                _cs["ses"] = None
                if intento == 2:
                    return {"proveedor": "Liderpapel", "encontrado": False,
                            "error": f"Liderpapel no disponible: {e}"}
            except Exception as e:               # noqa: BLE001
                traceback.print_exc()
                return {"proveedor": "Liderpapel", "encontrado": False,
                        "error": f"Error consultando Liderpapel: {e}"}


# --------------------------------------------------------------------------- #
# Normalización a un formato común para el front
# --------------------------------------------------------------------------- #
def _norm_azeta(d: dict) -> dict:
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


def _norm_cs(d: dict) -> dict:
    return {
        "proveedor": "Liderpapel",
        "encontrado": bool(d.get("encontrado")),
        "error": d.get("error") or d.get("motivo"),
        "nombre": d.get("nombre"),
        "ean": d.get("ean"),
        "marca": d.get("marca"),
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


def _comparar(azeta: dict, cs: dict) -> dict:
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
        res["mas_barato"] = "AZETA"        # solo uno tiene precio
    elif cc is not None:
        res["mas_barato"] = "Liderpapel"
    return res


# --------------------------------------------------------------------------- #
# Flask
# --------------------------------------------------------------------------- #
app = Flask(__name__)
_pool = ThreadPoolExecutor(max_workers=4)

# --- Protección con contraseña básica (solo si se define APP_PASSWORD) --------
# En local, sin esas variables, la app queda abierta (cómodo para pruebas).
# En el servidor público, define APP_USER / APP_PASSWORD y pedirá login.
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.before_request
def _proteger():
    if not APP_PASSWORD or request.path == "/salud":
        return None
    auth = request.authorization
    if not auth or auth.username != APP_USER or auth.password != APP_PASSWORD:
        return Response(
            "Acceso restringido.", 401,
            {"WWW-Authenticate": 'Basic realm="Escaner AZETA + Liderpapel"'},
        )
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

    # Búsqueda en vivo: los dos proveedores en paralelo.
    fa = _pool.submit(_buscar_azeta, ean)
    fc = _pool.submit(_buscar_cs, ean)
    azeta = _norm_azeta(fa.result())
    cs = _norm_cs(fc.result())

    encontrado = azeta["encontrado"] or cs["encontrado"]
    comparacion = _comparar(azeta, cs)

    # Registro en Supabase (best-effort, en segundo plano; no bloquea la respuesta
    # ni falla la búsqueda si Supabase no está configurado o da error).
    historial.registrar_async(ean, azeta, cs, comparacion)

    return jsonify({
        "ok": True,
        "ean": ean,
        "encontrado": encontrado,
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
