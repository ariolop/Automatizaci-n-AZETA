"""
azeta_login.py
--------------
Módulo de autenticación para el portal de AZETA Distribuciones.

El portal NO tiene API, así que usamos scraping con `requests`.
El formulario de login (reCAPTCHA v3 está comentado en el sitio, no se valida)
hace un POST a /html/identificacion/autentifica.php con:
    - fr_nombre  : usuario
    - fr_clave   : contraseña
    - g-recaptcha-response : (vacío)

Detección de resultado:
    - Login FALLIDO -> redirige a .../index.php?fr_mensaje=1
    - Login OK      -> redirige sin fr_mensaje=1 y la sesión queda autenticada.

Uso:
    from azeta_login import AzetaSession
    sesion = AzetaSession()          # lee credenciales del .env
    sesion.login()                   # lanza excepción si falla
    html = sesion.get(url).text      # peticiones ya autenticadas
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests


# --------------------------------------------------------------------------- #
# Carga del .env (sin dependencias externas: parser mínimo + soporte dotenv)
# --------------------------------------------------------------------------- #
def cargar_env(ruta: str | os.PathLike | None = None) -> dict[str, str]:
    """Carga variables del archivo .env situado junto a este script."""
    if ruta is None:
        ruta = Path(__file__).resolve().parent / ".env"
    ruta = Path(ruta)
    valores: dict[str, str] = {}
    if not ruta.exists():
        return valores
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        valor = valor.strip().strip('"').strip("'")
        valores[clave.strip()] = valor
        # también lo exponemos como variable de entorno
        os.environ.setdefault(clave.strip(), valor)
    return valores


# Constantes del portal
BASE_URL = "https://www.azetadistribuciones.es"
LOGIN_URL = f"{BASE_URL}/html/identificacion/autentifica.php"
INDEX_URL = f"{BASE_URL}/par/index.php"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


class AzetaError(Exception):
    """Error de autenticación o conexión con AZETA."""


class AzetaSession:
    """Sesión autenticada contra el portal de AZETA."""

    def __init__(self, usuario: str | None = None, clave: str | None = None):
        env = cargar_env()
        self.usuario = usuario or env.get("AZETA_USER") or os.environ.get("AZETA_USER")
        self.clave = clave or env.get("AZETA_PASSWORD") or os.environ.get("AZETA_PASSWORD")
        self.base_url = env.get("AZETA_BASE_URL", BASE_URL) or BASE_URL
        # Si el .env trae una URL ausente o mal formada, usamos la constante correcta
        url_env = env.get("AZETA_LOGIN_URL", "")
        self.login_url = url_env if url_env.endswith("autentifica.php") else LOGIN_URL

        if not self.usuario or not self.clave:
            raise AzetaError(
                "Faltan credenciales. Rellena AZETA_USER y AZETA_PASSWORD en el archivo .env"
            )

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.autenticado = False

    # ------------------------------------------------------------------ #
    def login(self, timeout: int = 30, reintentos: int = 4) -> bool:
        """Autentica la sesión. Devuelve True o lanza AzetaError.

        El servidor de AZETA es lento e intermitente: a veces devuelve 404 en el
        POST y a veces agota el tiempo de espera (timeout). Por eso reintentamos
        varias veces con espera creciente antes de rendirnos.
        """
        import time

        datos = {
            "fr_nombre": self.usuario,
            "fr_clave": self.clave,
            "g-recaptcha-response": "",
        }

        ultimo_estado = None
        ultimo_error = None
        for intento in range(1, reintentos + 1):
            try:
                # 1) Visita inicial para obtener/refrescar cookies de sesión
                self.session.get(INDEX_URL, timeout=timeout)

                # 2) POST del formulario de identificación
                resp = self.session.post(
                    self.login_url,
                    data=datos,
                    timeout=timeout,
                    allow_redirects=False,  # leemos el Location para detectar el resultado
                    headers={
                        "Referer": INDEX_URL,
                        "Origin": self.base_url,
                    },  # sin X-Requested-With (el form no es AJAX; con esa cabecera devuelve 404)
                )
            except requests.exceptions.RequestException as e:
                # Timeout / corte de red: reintentar
                ultimo_error = e
                time.sleep(1.5 * intento)
                continue

            location = resp.headers.get("Location", "")
            ultimo_estado = resp.status_code

            # Credenciales incorrectas: no tiene sentido reintentar
            if "fr_mensaje=1" in location:
                raise AzetaError(
                    "Login rechazado: usuario o contraseña incorrectos."
                )

            # Éxito: 302 a la zona privada (o 200)
            if resp.status_code in (200, 302):
                if location:
                    destino = (
                        location if location.startswith("http") else self.base_url + location
                    )
                    try:
                        self.session.get(destino, timeout=timeout)
                    except requests.exceptions.RequestException:
                        pass  # la sesión ya está autenticada por cookies
                self.autenticado = True
                return True

            # 404 u otro código transitorio -> reintentar
            time.sleep(1.5 * intento)

        if ultimo_error is not None:
            raise AzetaError(
                "No se pudo conectar con AZETA (la web tardó demasiado en responder). "
                "Inténtalo de nuevo en unos segundos."
            )
        raise AzetaError(
            f"No se pudo autenticar tras {reintentos} intentos "
            f"(último estado HTTP {ultimo_estado})."
        )

    # ------------------------------------------------------------------ #
    def _peticion(self, metodo: str, url: str, reintentos: int = 3, **kwargs):
        """Realiza una petición autenticada reintentando ante timeouts/red."""
        if not self.autenticado:
            raise AzetaError("La sesión no está autenticada. Llama a login() primero.")
        if not url.startswith("http"):
            url = self.base_url + ("" if url.startswith("/") else "/") + url
        kwargs.setdefault("timeout", 30)
        import time
        ultimo_error = None
        for intento in range(1, reintentos + 1):
            try:
                return self.session.request(metodo, url, **kwargs)
            except requests.exceptions.RequestException as e:
                ultimo_error = e
                time.sleep(1.5 * intento)
        raise AzetaError(
            "AZETA no respondió a tiempo (timeout/red). Inténtalo de nuevo en unos segundos."
        ) from ultimo_error

    def get(self, url: str, **kwargs):
        """GET autenticado con reintentos ante timeouts."""
        return self._peticion("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        """POST autenticado con reintentos ante timeouts."""
        return self._peticion("POST", url, **kwargs)


# --------------------------------------------------------------------------- #
# Prueba rápida desde línea de comandos:  python azeta_login.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    try:
        sesion = AzetaSession()
        print(f"Autenticando como: {sesion.usuario} ...")
        sesion.login()
        print("✓ Login correcto. Sesión autenticada.")
        # Comprobación: pedir la portada privada
        r = sesion.get(INDEX_URL)
        marca_privada = "public-login" not in r.text  # si ya no aparece el botón público
        print(f"  Zona privada accesible: {'sí' if marca_privada else 'comprobar manualmente'}")
    except AzetaError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)
