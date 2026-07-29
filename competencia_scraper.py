"""
A PASITOS LENTOS — Buscador de precios de la competencia por EAN
================================================================
Dado un listado de códigos EAN, consulta el precio de cada producto en
varias papelerías/librerías españolas y vuelca los resultados a CSV para
poder comparar después con tus propios precios.

Estrategia de extracción (robusta):
  • mypaper.es  -> usa su API de búsqueda (devuelve JSON limpio). Muy fiable.
  • Resto       -> descarga la página de resultados y extrae el precio por
                   PROXIMIDAD al EAN (regex), en lugar de depender de
                   selectores CSS concretos que se rompen al cambiar la web.

Instalación (solo la primera vez):
    pip install requests beautifulsoup4 lxml

Uso:
    python buscar_precios.py

Salidas:
    precios_competencia.csv   -> formato ancho  (1 fila por EAN, columnas por tienda)
    precios_largo.csv         -> formato tidy   (1 fila por EAN+tienda) -> ideal para comparar
"""

import csv
import os
import re
import time
import json
from datetime import datetime
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────
# 1) CONFIGURACIÓN — edita aquí
# ─────────────────────────────────────────────────────────────────────

# Pega aquí los EAN que quieras consultar (uno por línea, entre comillas)
EANS = [
    "3086123764101",
    "3086123690417",
    "3086123185098",
    "8411574109693",
    # "8411574109716",
    # "8411574109709",
    # "8423473077898",
    # "8423473077829",
    # "8423473078390",
    # "8423473077812",
    # "8423473030800",
    # "8423473030848",   # ejemplo real (BOLIGRAFO MILAN P1 TOUCH) — sustitúyelo
]

ARCHIVO_ANCHO = "precios_competencia.csv"
ARCHIVO_LARGO = "precios_largo.csv"

# ── Envío a PrestaShop (módulo apcompetencia) ────────────────────────
# Pon ENVIAR_A_PRESTASHOP = True y rellena la URL y el token que muestra
# la pantalla "Configurar" del módulo en el back office.
ENVIAR_A_PRESTASHOP = True
PRESTASHOP_ENDPOINT = "http://192.168.18.7/module/apcompetencia/api"
PRESTASHOP_TOKEN    = "3geN3WTbLw3DuQWb4VM6z4OzMIHoAqsn2MCLh45Y"
ENVIO_LOTE          = 200   # nº de registros por POST (evita payloads enormes)

TIMEOUT       = 20    # segundos máx. por petición
PAUSA_ENTRE   = 2.0   # segundos entre peticiones (sé amable con los servidores)
MAX_REINTENTOS = 2

# Tiendas a consultar.
#   tipo "mypaper_json"  -> parser dedicado vía API JSON (mypaper)
#   tipo "generico"      -> descarga HTML y extrae precio cercano al EAN
#   {EAN} se sustituye por el código en la URL de búsqueda.
TIENDAS = [
    {
        "nombre": "mypaper.es",
        "tipo": "mypaper_json",
        "url_busqueda": "https://www.mypaper.es/module/iqitsearch/searchiqit?ajax=1&s={EAN}",
    },
    {
        # PrestaShop 1.6 -> el parámetro de búsqueda es 'search_query' (no 's')
        "nombre": "materialescolar.es",
        "tipo": "generico",
        "url_busqueda": "https://www.materialescolar.es/buscar?controller=search&search_query={EAN}",
    },
    {
        # Plataforma propia Carlin ("PStores"). s=3045 = tienda Granada Centro.
        "nombre": "carlin (granada)",
        "tipo": "generico",
        "url_busqueda": "https://granadacentro.carlin.es/PStores?searchString={EAN}&o=searchEngine_b2c&p=1&s=3045",
    },
    {
        # Plataforma propia Grupo Selfpaper. Resultados server-side; se toma el
        # precio CON IVA. (Parser dedicado.)
        "nombre": "hipermaterial.es",
        "tipo": "hipermaterial",
        "url_busqueda": "https://www.hipermaterial.es/buscador?q={EAN}",
    },
    # --- Plataforma de librería compartida (listaLibros.php). Misma URL en todas ---
    {
        "nombre": "papelerialacasa.com",
        "tipo": "generico",
        "url_busqueda": "https://www.papelerialacasa.com/es/busqueda/listaLibros.php?tipoBus=full&palabrasBusqueda={EAN}&boton=Buscar",
    },
    {
        "nombre": "papeleriasambra.com",
        "tipo": "generico",
        "url_busqueda": "https://www.papeleriasambra.com/es/busqueda/listaLibros.php?tipoBus=full&palabrasBusqueda={EAN}&boton=Buscar",
    },
    {
        "nombre": "papeleriablack.es",
        "tipo": "generico",
        "url_busqueda": "https://www.papeleriablack.es/es/busqueda/listaLibros.php?tipoBus=full&palabrasBusqueda={EAN}&boton=Buscar",
    },
    {
        "nombre": "librerianevada.es",
        "tipo": "generico",
        "url_busqueda": "https://www.librerianevada.es/es/busqueda/listaLibros.php?tipoBus=full&palabrasBusqueda={EAN}&boton=Buscar",
    },
    {
        "nombre": "libreriaoxford.com",
        "tipo": "generico",
        "url_busqueda": "https://www.libreriaoxford.com/es/busqueda/listaLibros.php?tipoBus=full&palabrasBusqueda={EAN}&boton=Buscar",
    },
    {
        "nombre": "papelerialapaz.com",
        "tipo": "generico",
        "url_busqueda": "https://www.papelerialapaz.com/es/busqueda/listaLibros.php?tipoBus=full&palabrasBusqueda={EAN}&boton=Buscar",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Connection": "keep-alive",
}

# Rango de precios plausibles (€). Filtra basura tipo "0,00" o importes enormes.
PRECIO_MIN, PRECIO_MAX = 0.10, 1000.0

# Carpeta donde se guarda el HTML de las tiendas que fallan, para diagnóstico.
CARPETA_DEBUG = "debug_html"
_HOSTS_CALENTADOS = set()   # hosts ya "visitados" para obtener cookies de sesión

# ─────────────────────────────────────────────────────────────────────
# 2) UTILIDADES
# ─────────────────────────────────────────────────────────────────────

RE_PRECIO = re.compile(r"(\d{1,4}[.,]\d{2})\s*(?:€|eur)", re.IGNORECASE)


def a_float(texto):
    """'1.234,56 €' -> 1234.56 ; '12,90' -> 12.90 ; '12.90' -> 12.90 (o None)."""
    if not texto:
        return None
    # 1) formato español con decimales por coma (admite separador de miles)
    m = re.search(r"\d{1,3}(?:[.\s]\d{3})*,\d{2}", str(texto))
    if m:
        s = m.group(0).replace(".", "").replace(" ", "").replace(",", ".")
    else:
        # 2) formato con punto decimal (p. ej. price_amount serializado)
        m = re.search(r"\d+\.\d{2}", str(texto))
        if not m:
            return None
        s = m.group(0)
    try:
        val = float(s)
    except ValueError:
        return None
    return val if PRECIO_MIN <= val <= PRECIO_MAX else None


def _calentar(session, url):
    """Visita la home del dominio una vez para obtener cookies de sesión.
    Muchos WAF devuelven 403 si vas directo al buscador sin cookie previa."""
    host = urlsplit(url)
    origin = f"{host.scheme}://{host.netloc}/"
    if host.netloc in _HOSTS_CALENTADOS:
        return
    _HOSTS_CALENTADOS.add(host.netloc)
    try:
        session.get(origin, headers=HEADERS, timeout=TIMEOUT)
        time.sleep(1.0)
    except requests.RequestException:
        pass


def get(session, url):
    """GET con reintentos (+ calentamiento de sesión). Devuelve response o None."""
    _calentar(session, url)
    host = urlsplit(url)
    headers = dict(HEADERS)
    headers["Referer"] = f"{host.scheme}://{host.netloc}/"
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            r = session.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            print(f"       HTTP {r.status_code} (intento {intento})")
        except requests.RequestException as e:
            print(f"       error de red: {type(e).__name__} (intento {intento})")
        time.sleep(1.5)
    return None


def resultado_vacio(url, estado="sin resultado"):
    return {"nombre": "—", "precio": None, "precio_txt": estado, "url": url}


# ─────────────────────────────────────────────────────────────────────
# 3) PARSERS POR TIENDA
# ─────────────────────────────────────────────────────────────────────

def parser_mypaper_json(session, tienda, ean):
    """mypaper expone su buscador como JSON -> extracción exacta y fiable."""
    url = tienda["url_busqueda"].replace("{EAN}", ean)
    r = get(session, url)
    if not r:
        return resultado_vacio(url, "ERROR")
    try:
        data = r.json()
    except (json.JSONDecodeError, ValueError):
        return resultado_vacio(url, "ERROR")

    return _extraer_mypaper(data, url)


def _extraer_mypaper(data, url):
    """Extrae nombre/precio del JSON de mypaper (función pura, testeable)."""
    productos = data.get("products") or []
    if not productos:
        return resultado_vacio(url)

    p = productos[0]  # primer/mejor resultado
    if isinstance(p.get("price_amount"), (int, float)):
        precio = float(p["price_amount"])
    else:
        precio = a_float(p.get("price"))
    nombre = (p.get("name") or "—").strip()
    link = p.get("link") or url
    return {
        "nombre": nombre[:90],
        "precio": precio,
        "precio_txt": f"{precio:.2f}".replace(".", ",") if precio else "REVISAR",
        "url": link,
    }


def parser_generico(session, tienda, ean):
    """
    Descarga la página de resultados y busca el precio MÁS CERCANO al EAN
    dentro del HTML. Resistente a cambios de plantilla porque no depende
    de clases CSS concretas.
    """
    url = tienda["url_busqueda"].replace("{EAN}", ean)
    r = get(session, url)
    if not r:
        return resultado_vacio(url, "ERROR")

    html = r.text
    soup = BeautifulSoup(html, "lxml")

    # Quitamos el "chrome" de la página que puede REPETIR el EAN sin ser un
    # producto real (la caja de búsqueda <input>, el <title>) o aportar precios
    # no relacionados (scripts, banners de cookies). Así, si el EAN solo sale en
    # el buscador (búsqueda sin resultados), NO lo damos por encontrado.
    for tag in soup(["head", "script", "style", "noscript", "input", "button", "select", "textarea"]):
        tag.decompose()
    cuerpo = str(soup)
    texto_visible = soup.get_text(" ", strip=True)
    ean_en_cuerpo = ean in cuerpo

    # 1) Precio encontrado Y el EAN aparece en el CUERPO (producto real).
    precio = _buscar_precio(soup, cuerpo, ean)
    if precio is not None and ean_en_cuerpo:
        nombre = _nombre_cercano_al_ean(cuerpo, ean) or _nombre_producto(soup)
        return {
            "nombre": nombre[:90],
            "precio": precio,
            "precio_txt": f"{precio:.2f}".replace(".", ","),
            "url": url,
        }

    # 2) Sin precio fiable: ¿hay realmente un producto del EAN en la página?
    hay_fichas = bool(RE_FICHA.search(cuerpo))
    if not ean_en_cuerpo or not hay_fichas or _sin_resultados(texto_visible):
        return resultado_vacio(url)   # no hay producto (o se carga por JavaScript)

    # Hay ficha(s) de producto con el EAN pero no se leyó el precio -> revisar.
    nombre = _nombre_cercano_al_ean(cuerpo, ean) or _nombre_producto(soup)
    _dump_html(tienda["nombre"], ean, html)
    return {"nombre": nombre[:90], "precio": None, "precio_txt": "REVISAR", "url": url}


# Frases que indican que la búsqueda no devolvió productos.
RE_SIN_RESULTADOS = re.compile(
    r"no hay resultados|no se han encontrado|no se encontr|"
    r"0\s*resultados|ning[uú]n resultado|sin resultados",
    re.IGNORECASE,
)


def _sin_resultados(html):
    return bool(RE_SIN_RESULTADOS.search(html))


# Enlaces a ficha de producto en la plataforma de librería (…/libro/… o …/objeto/…).
RE_FICHA = re.compile(
    r'<a[^>]+href="[^"]*/(?:libro|objeto)/[^"]*"[^>]*>\s*([^<>]{2,90}?)\s*</a>',
    re.IGNORECASE,
)


def _nombre_cercano_al_ean(html, ean):
    """Nombre del producto = texto del último enlace de ficha situado ANTES del EAN."""
    pos = html.find(ean)
    if pos == -1:
        return None
    nombre = None
    for m in RE_FICHA.finditer(html):
        if m.start() > pos:
            break
        texto = m.group(1).strip()
        if texto:
            nombre = texto
    return nombre


def _buscar_precio(soup, html, ean):
    """Intenta varias estrategias, de la más fiable a la más laxa."""
    # 1) Microdatos / meta estándar: itemprop="price" content="3.45"
    for el in soup.select('[itemprop="price"], meta[property="product:price:amount"]'):
        val = a_float(el.get("content") or el.get_text(" ", strip=True))
        if val is not None:
            return val
    # 2) Atributos data-price / data-precio
    m = re.search(r'data-(?:price|precio)\s*=\s*["\']?([\d.,]+)', html, re.I)
    if m:
        val = a_float(m.group(1)) or a_float(m.group(1).replace(".", ","))
        if val is not None:
            return val
    # 3) Texto plano: precio con € más cercano al EAN
    val = _precio_cercano_al_ean(soup.get_text(" ", strip=True), ean)
    if val is not None:
        return val
    # 4) HTML crudo cerca del EAN (por si el € está en otra etiqueta/entidad)
    return _precio_cercano_al_ean(html.replace("&euro;", "€").replace("&#8364;", "€"), ean)


def _dump_html(nombre_tienda, ean, html):
    """Guarda el HTML para diagnóstico y muestra el contexto alrededor del EAN."""
    try:
        os.makedirs(CARPETA_DEBUG, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", nombre_tienda.lower())
        ruta = os.path.join(CARPETA_DEBUG, f"{slug}_{ean}.html")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(html)
        pos = html.find(ean)
        ctx = html[max(0, pos - 400): pos + 400] if pos != -1 else ""
        ctx = re.sub(r"\s+", " ", ctx)
        print(f"       ↳ HTML guardado: {ruta}")
        if ctx:
            print(f"       ↳ contexto EAN: …{ctx[:300]}…")
    except OSError:
        pass


def _precio_cercano_al_ean(texto, ean):
    """
    Precio (float) que paga el cliente para el producto del EAN.
    El precio va DESPUÉS del EAN. Si el producto tiene descuento, aparecen dos
    precios juntos (original + final); el FINAL es el último del grupo pegado,
    así que se devuelve el último de la ráfaga contigua de precios tras el EAN.
    """
    pos_ean = texto.find(ean)
    precios = [(m.start(), m.end(), a_float(m.group(0))) for m in RE_PRECIO.finditer(texto)]
    precios = [(ini, fin, val) for ini, fin, val in precios if val is not None]
    if not precios:
        return None
    if pos_ean == -1:
        return precios[0][2]
    posteriores = [p for p in precios if p[0] >= pos_ean]
    if not posteriores:
        # ningún precio tras el EAN -> el más cercano anterior
        return min(precios, key=lambda p: abs(p[0] - pos_ean))[2]
    # ráfaga contigua: precios separados por <25 caracteres (original + rebajado)
    final = posteriores[0]
    for prev, sig in zip(posteriores, posteriores[1:]):
        if sig[0] - prev[1] <= 25:
            final = sig
        else:
            break
    return final[2]


def _nombre_producto(soup):
    selectores = [
        "[itemprop='name']", ".product-title", ".product-name",
        ".titulo", ".nombre", ".titulo-libro", "h2 a", "h3 a", "h2", "h3",
    ]
    for sel in selectores:
        el = soup.select_one(sel)
        if el:
            t = el.get("content") or el.get_text(strip=True)
            if t and len(t.strip()) > 2:
                return t.strip()
    return "—"


def parser_hipermaterial(session, tienda, ean):
    """
    Buscador server-side de hipermaterial.es (Grupo Selfpaper).
    Muestra 'Encontrados N productos para : EAN' y, por producto, el precio
    CON IVA en formato '… € 0,54 con Iva'. Se devuelve ese precio (el que paga
    el cliente final), que es el comparable con tus precios de venta.
    """
    url = tienda["url_busqueda"].replace("{EAN}", ean)
    r = get(session, url)
    if not r:
        return resultado_vacio(url, "ERROR")

    html = r.text
    soup = BeautifulSoup(html, "lxml")
    texto = soup.get_text(" ", strip=True)

    m = re.search(r"Encontrados\s+(\d+)\s+producto", texto, re.IGNORECASE)
    if not m or int(m.group(1)) == 0:
        return resultado_vacio(url)

    # Precio CON IVA del primer resultado (primer "… con Iva" tras el conteo).
    pm = re.search(r"([\d.,]+)\s*con\s*iva", texto[m.end():], re.IGNORECASE)
    precio = a_float(pm.group(1)) if pm else None

    # Nombre: título del primer enlace de ficha (.html) tras el conteo.
    nombre = "—"
    nm = re.search(r'href="https://www\.hipermaterial\.es/[^"]+\.html"[^>]*title="([^"]+)"', html, re.I)
    if nm:
        nombre = nm.group(1).strip()

    if precio is not None:
        return {
            "nombre": nombre[:90],
            "precio": precio,
            "precio_txt": f"{precio:.2f}".replace(".", ","),
            "url": url,
        }
    _dump_html(tienda["nombre"], ean, html)
    return {"nombre": nombre[:90], "precio": None, "precio_txt": "REVISAR", "url": url}


PARSERS = {
    "mypaper_json": parser_mypaper_json,
    "generico": parser_generico,
    "hipermaterial": parser_hipermaterial,
}

# ─────────────────────────────────────────────────────────────────────
# 3b) ENVÍO AL MÓDULO DE PRESTASHOP
# ─────────────────────────────────────────────────────────────────────

def enviar_a_prestashop(filas_largo):
    """
    Envía las filas (formato tidy) al endpoint del módulo apcompetencia.
    Mapea las columnas del CSV a las claves que espera la API e incluye
    SIEMPRE la URL de origen de cada precio (para revisión manual).
    """
    if not ENVIAR_A_PRESTASHOP:
        return
    if not PRESTASHOP_ENDPOINT or "TU-TIENDA" in PRESTASHOP_ENDPOINT or not PRESTASHOP_TOKEN \
            or "PEGA_AQUI" in PRESTASHOP_TOKEN:
        print("\n⚠️  Envío a PrestaShop omitido: configura PRESTASHOP_ENDPOINT y PRESTASHOP_TOKEN.")
        return

    # Solo se suben registros CON precio (estado OK). Los "sin resultado",
    # "REVISAR" o "ERROR" no se envían: no queremos filas sin datos en PrestaShop.
    filas_ok = [f for f in filas_largo if f.get("Estado") == "OK" and f.get("Precio")]
    omitidas = len(filas_largo) - len(filas_ok)
    if omitidas:
        print(f"  ({omitidas} registros sin precio omitidos; no se suben a PrestaShop)")
    if not filas_ok:
        print("  No hay registros con precio que enviar.")
        return

    # Mapea cada fila tidy -> registro de la API.
    registros = [{
        "ean":    f["EAN"],
        "tienda": f["Tienda"],
        "nombre": f["Nombre"],
        "precio": f["Precio"],     # "6,56" -> el módulo lo interpreta
        "estado": f["Estado"],
        "url":    f["URL"],        # origen del precio, para revisar a mano
        "fecha":  f["Fecha"],
    } for f in filas_ok]

    headers = {
        "X-Api-Token": PRESTASHOP_TOKEN,
        "Content-Type": "application/json",
    }

    print("\n" + "=" * 62)
    print(f"  Enviando {len(registros)} registros a PrestaShop…")
    enviados = errores = 0
    for i in range(0, len(registros), ENVIO_LOTE):
        lote = registros[i:i + ENVIO_LOTE]
        try:
            r = requests.post(
                PRESTASHOP_ENDPOINT,
                headers=headers,
                data=json.dumps({"records": lote}),
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                try:
                    resp = r.json()
                    ins = resp.get("insertados", 0)
                    enviados += ins
                    print(f"    lote {i // ENVIO_LOTE + 1}: {ins}/{len(lote)} insertados")
                except ValueError:
                    print(f"    lote {i // ENVIO_LOTE + 1}: respuesta no-JSON ({r.status_code})")
            else:
                errores += len(lote)
                print(f"    lote {i // ENVIO_LOTE + 1}: ❌ HTTP {r.status_code} — {r.text[:200]}")
        except requests.RequestException as e:
            errores += len(lote)
            print(f"    lote {i // ENVIO_LOTE + 1}: ❌ {type(e).__name__}: {e}")

    print(f"  ✅ Enviados a PrestaShop: {enviados}  |  fallos: {errores}")
    print("=" * 62)


# ─────────────────────────────────────────────────────────────────────
# 3c) FUNCIÓN REUTILIZABLE — consulta de un solo EAN (la usa la app web)
# ─────────────────────────────────────────────────────────────────────

def buscar_ean(ean, session=None, tiendas=None, pausa=None):
    """
    Consulta UN EAN en todas las tiendas (o en las indicadas) y devuelve una
    lista de dicts, uno por tienda, con las claves:
        EAN, Fecha, Tienda, Nombre, Precio (texto), PrecioNum (float|None),
        Estado (OK|REVISAR|ERROR|sin resultado), URL
    Reutilizable desde la aplicación web y desde el CLI.
    """
    ean = str(ean).strip()
    if session is None:
        session = requests.Session()
    if tiendas is None:
        tiendas = TIENDAS
    if pausa is None:
        pausa = PAUSA_ENTRE
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")

    filas = []
    for i, tienda in enumerate(tiendas):
        parser = PARSERS.get(tienda["tipo"], parser_generico)
        try:
            res = parser(session, tienda, ean)
        except Exception:
            res = resultado_vacio(tienda["url_busqueda"].replace("{EAN}", ean), "ERROR")
        filas.append({
            "EAN": ean,
            "Fecha": fecha,
            "Tienda": tienda["nombre"],
            "Nombre": res["nombre"],
            "Precio": (f"{res['precio']:.2f}".replace(".", ",") if res["precio"] is not None else ""),
            "PrecioNum": res["precio"],
            "Estado": "OK" if res["precio"] is not None else res["precio_txt"],
            "URL": res["url"],
        })
        if pausa and i < len(tiendas) - 1:
            time.sleep(pausa)
    return filas


# ─────────────────────────────────────────────────────────────────────
# 4) PROGRAMA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 62)
    print("  A PASITOS LENTOS — Precios de la competencia por EAN")
    print(f"  EANs: {len(EANS)}  |  Tiendas: {len(TIENDAS)}")
    print("=" * 62)

    session = requests.Session()
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")

    filas_largo = []   # tidy: una fila por EAN+tienda
    filas_ancho = []   # una fila por EAN

    for ean in EANS:
        print(f"\nEAN {ean}")
        fila = {"EAN": ean, "Fecha": fecha}

        for tienda in TIENDAS:
            parser = PARSERS.get(tienda["tipo"], parser_generico)
            try:
                res = parser(session, tienda, ean)
            except Exception as e:
                print(f"    {tienda['nombre']}: ❌ {type(e).__name__}: {e}")
                res = resultado_vacio(tienda["url_busqueda"].replace("{EAN}", ean), "ERROR")

            estado = res["precio_txt"]
            if res["precio"] is not None:
                pstr = f"{res['precio']:.2f}".replace(".", ",") + " €"
                print(f"    {tienda['nombre']:<22} {pstr:>9}  {res['nombre'][:40]}")
            else:
                print(f"    {tienda['nombre']:<22} {estado:>9}")

            n = tienda["nombre"]
            fila[f"Precio ({n})"] = res["precio_txt"]
            fila[f"Nombre ({n})"] = res["nombre"]
            fila[f"URL ({n})"] = res["url"]

            filas_largo.append({
                "EAN": ean,
                "Fecha": fecha,
                "Tienda": n,
                "Nombre": res["nombre"],
                "Precio": (f"{res['precio']:.2f}".replace(".", ",") if res["precio"] is not None else ""),
                "Estado": "OK" if res["precio"] is not None else estado,
                "URL": res["url"],
            })
            time.sleep(PAUSA_ENTRE)

        filas_ancho.append(fila)

    # ── CSV ANCHO ─────────────────────────────────────────────
    columnas = ["EAN", "Fecha"]
    for t in TIENDAS:
        n = t["nombre"]
        columnas += [f"Precio ({n})", f"Nombre ({n})", f"URL ({n})"]
    with open(ARCHIVO_ANCHO, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        w.writerows(filas_ancho)

    # ── CSV LARGO (tidy) ──────────────────────────────────────
    with open(ARCHIVO_LARGO, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["EAN", "Fecha", "Tienda", "Nombre", "Precio", "Estado", "URL"])
        w.writeheader()
        w.writerows(filas_largo)

    # ── RESUMEN ───────────────────────────────────────────────
    ok = sum(1 for r in filas_largo if r["Estado"] == "OK")
    revisar = sum(1 for r in filas_largo if r["Estado"] == "REVISAR")
    total = len(filas_largo)
    print("\n" + "=" * 62)
    print(f"  ✅ Precios encontrados : {ok}/{total}")
    print(f"  ⚠️  A revisar a mano   : {revisar}")
    print(f"  📄 {ARCHIVO_ANCHO}  y  {ARCHIVO_LARGO}")
    print("=" * 62)

    # ── Envío al módulo de PrestaShop (si está activado) ──────
    enviar_a_prestashop(filas_largo)


if __name__ == "__main__":
    main()

# ─────────────────────────────────────────────────────────────────────
# NOTA — si alguna librería devuelve siempre "sin resultado":
# Puede que cargue los resultados por JavaScript. En ese caso instala
# Selenium (pip install undetected-chromedriver selenium) y dime cuál,
# y le añadimos un parser con navegador real para esa tienda concreta.
#
# Envío a PrestaShop: activa ENVIAR_A_PRESTASHOP y rellena PRESTASHOP_ENDPOINT
# y PRESTASHOP_TOKEN (los muestra la pantalla "Configurar" del módulo).
# ─────────────────────────────────────────────────────────────────────
