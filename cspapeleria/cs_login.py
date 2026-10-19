"""
cs_login.py — Sesión autenticada contra el portal B2B de Comercial del Sur de
Papelería (mayorista de Liderpapel): https://b2b.cspapeleria.com

IMPORTANTE (WAF Akamai):
El borde de la web es AkamaiGHost y bloquea con 403 cualquier cliente HTTP cuya
huella TLS (JA3) no sea la de un navegador real. Por eso NO se usa `requests`
sino `curl_cffi` con impersonate="chrome", que reproduce el handshake TLS de
Chrome y pasa el filtro. Verificado 2026-07-26 desde IP de datacenter.

Login: GET a la página de login (fija JSESSIONID + cookie WAF "TS...") y POST del
formulario (o=login) con los campos user / password / credentialId (vacío) /
commit. Éxito = redirección a ControlServlet?o=iniciob2b y desaparición del campo
password.
"""
import os
import re
import time

from curl_cffi import requests as cr

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass


class CSError(Exception):
    """Error controlado de la sesión (login fallido, timeout, WAF, etc.)."""


class CSSession:
    BASE = "https://b2b.cspapeleria.com"
    LOGIN_PAGE = BASE + "/PublicControlServlet?o=login&p=ext&loginIdiom=ES&redirect="
    SERVLET = BASE + "/ControlServlet"

    def __init__(self, usuario=None, clave=None, timeout=None,
                 impersonate=None, reintentos=3):
        self.usuario = usuario or os.environ.get("CS_USER")
        self.clave = clave or os.environ.get("CS_PASS")
        if not self.usuario or not self.clave:
            raise CSError("Faltan credenciales: define CS_USER y CS_PASS en .env")
        self.timeout = int(timeout or os.environ.get("CS_TIMEOUT", 25))
        self.impersonate = impersonate or os.environ.get("CS_IMPERSONATE", "chrome")
        self.reintentos = reintentos
        self._logueado = False
        self.s = cr.Session(impersonate=self.impersonate, timeout=self.timeout)

    # ------------------------------------------------------------------ #
    # Petición con reintentos (la web es intermitente / WAF puede cortar)
    # ------------------------------------------------------------------ #
    def _request(self, metodo, url, **kw):
        kw.setdefault("timeout", self.timeout)
        ultimo = None
        for intento in range(1, self.reintentos + 1):
            try:
                r = self.s.request(metodo, url, **kw)
                if r.status_code == 403 and "AkamaiGHost" in r.headers.get("Server", ""):
                    raise CSError(
                        "403 de Akamai: la huella TLS no se acepta. "
                        "Revisa CS_IMPERSONATE (prueba 'chrome', 'chrome120', 'chrome110')."
                    )
                return r
            except CSError:
                raise
            except Exception as e:  # timeout / conexión
                ultimo = e
                if intento < self.reintentos:
                    time.sleep(1.2 * intento)
        raise CSError(f"No se pudo contactar con {url}: {ultimo}")

    # ------------------------------------------------------------------ #
    # Login
    # ------------------------------------------------------------------ #
    def login(self):
        g = self._request("GET", self.LOGIN_PAGE)
        m_action = re.search(r'<form[^>]*action="([^"]*)"', g.text, re.I)
        if not m_action:
            raise CSError("No se encontró el formulario de login (¿cambió la web?)")
        action = m_action.group(1)
        post_url = action if action.startswith("http") else self.BASE + "/" + action.lstrip("/")
        m_cred = re.search(r'name="credentialId"[^>]*value="([^"]*)"', g.text, re.I)
        credential_id = m_cred.group(1) if m_cred else ""

        data = {
            "user": self.usuario,
            "password": self.clave,
            "credentialId": credential_id,
            "commit": "Iniciar Sesión",
        }
        p = self._request("POST", post_url, data=data, allow_redirects=True)

        low = p.text.lower()
        ok = ("iniciob2b" in p.url) or ('type="password"' not in low and "logout" in low)
        if not ok:
            raise CSError("Login rechazado: revisa usuario/contraseña en .env")
        self._logueado = True
        return True

    def asegurar_login(self):
        if not self._logueado:
            self.login()

    # ------------------------------------------------------------------ #
    # Peticiones ya autenticadas al servlet
    # ------------------------------------------------------------------ #
    @staticmethod
    def parece_login(r) -> bool:
        """True si la respuesta es la pantalla de login (sesión caducada)."""
        try:
            if "PublicControlServlet" in (r.url or ""):
                return True
            low = (r.text or "").lower()
        except Exception:  # noqa: BLE001
            return False
        return 'type="password"' in low and 'name="user"' in low

    # Esperas (s) antes de cada reintento cuando la respuesta no es la esperada
    # (429, 5xx o una página que no es de resultados/ficha: interstitial del WAF,
    # aviso de mantenimiento...). Antes eso se leía como "producto no encontrado".
    ESPERAS_REINTENTO = (3, 6)

    @staticmethod
    def describir(r) -> str:
        """Resumen corto de una respuesta para mensajes de error y logs."""
        try:
            m = re.search(r"<title[^>]*>(.*?)</title>", r.text or "", re.S | re.I)
            titulo = " ".join(m.group(1).split())[:80] if m else "sin título"
        except Exception:  # noqa: BLE001
            titulo = "?"
        try:
            tam = len(r.content or b"")
        except Exception:  # noqa: BLE001
            tam = 0
        return f"HTTP {r.status_code}, {tam} bytes, «{titulo}»"

    def get(self, params, url=None, valida=None):
        """GET autenticado.

        - Si la sesión ha caducado (vuelve el login) se inicia sesión de nuevo
          UNA vez y se repite.
        - Un 429/5xx, o una página que no pasa 'valida(texto)' (si se indica),
          se reintenta tras una espera; si persiste lanza CSError, para que no
          se confunda con "producto no encontrado".
        """
        self.asegurar_login()
        destino = url or self.SERVLET
        relogin = False
        problema = None
        for espera in (0, *self.ESPERAS_REINTENTO):
            if espera:
                time.sleep(espera)
            r = self._request("GET", destino, params=params)
            if r.status_code in (401, 403) or self.parece_login(r):
                if relogin:
                    raise CSError("Liderpapel rechaza la sesión (¿credenciales o WAF?).")
                relogin = True
                self._logueado = False
                self.login()
                r = self._request("GET", destino, params=params)
                if r.status_code in (401, 403) or self.parece_login(r):
                    raise CSError("Liderpapel rechaza la sesión (¿credenciales o WAF?).")
            if r.status_code == 429 or r.status_code >= 500:
                problema = f"Liderpapel respondió con un error ({self.describir(r)})."
            elif valida is not None and not valida(r.text or ""):
                problema = f"Liderpapel devolvió una página inesperada ({self.describir(r)})."
            else:
                return r
            print(f"[liderpapel] {problema} params={params}", flush=True)
        raise CSError(problema + " Inténtalo de nuevo en unos segundos.")

    @staticmethod
    def es_pagina_busqueda(texto: str) -> bool:
        """La página de búsqueda real trae siempre la cabecera «Resultados de la
        búsqueda de:» (con o sin resultados; sin ellos dice «No se han obtenido
        resultados»). Si no está, no es una búsqueda: WAF, mantenimiento, etc."""
        return ("Resultados de la b" in texto
                or "No se han obtenido resultados" in texto)

    @staticmethod
    def es_pagina_ficha(texto: str) -> bool:
        """La ficha real trae el nombre en <h3 style="color:#adadad"> y el precio
        en 'precioNeto'."""
        return bool(re.search(r"color:\s*#adadad", texto, re.I)) or "precioNeto" in texto

    def get_producto(self, cod_product):
        return self.get({"o": "productb2b", "p": "1", "codProduct": str(cod_product)},
                        valida=self.es_pagina_ficha)

    def buscar(self, patron, exacto=False):
        return self.get({
            "o": "searchprdb2b", "p": "4",
            "flagExact": "ON" if exacto else "",
            "searchString": str(patron),
        }, valida=self.es_pagina_busqueda)


if __name__ == "__main__":
    ses = CSSession()
    ses.login()
    print("Login OK como usuario", ses.usuario)
