"""
azeta_producto.py
-----------------
Busca un producto en AZETA Distribuciones por EAN/ISBN/texto y extrae su ficha.

Requiere: azeta_login.py (misma carpeta) + requests + beautifulsoup4 + lxml.

Uso por línea de comandos:
    python azeta_producto.py 8712645316959
    python azeta_producto.py 8712645316959 --json producto.json

Uso como módulo:
    from azeta_producto import buscar_producto
    datos = buscar_producto("8712645316959")            # login automático
    # o reutilizando una sesión ya autenticada:
    from azeta_login import AzetaSession
    s = AzetaSession(); s.login()
    datos = buscar_producto("8712645316959", sesion=s)
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata

from bs4 import BeautifulSoup

from azeta_login import AzetaSession, AzetaError

# Estados de "Situación" que indican que el producto NO está disponible
_SITUACIONES_NO_DISPONIBLE = [
    "agotado", "descatalogado", "sin stock", "sin existencias",
    "no disponible", "proxima edicion", "pendiente", "baja", "anulado",
]

BUSCAR_URL = (
    "https://www.azetadistribuciones.es/lib/buscar/listaLibros.php"
    "?tipoBusqueda=R&tipoArticulo=&patron={patron}&boton=Buscar"
)


# --------------------------------------------------------------------------- #
# Utilidades de parseo
# --------------------------------------------------------------------------- #
def _texto(soup, **attrs):
    el = soup.find(attrs=attrs)
    return el.get_text(" ", strip=True) if el else None


def _norm(s: str) -> str:
    """minúsculas sin acentos, para usar como clave."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.strip().lower()


def _fix_mojibake(t: str | None) -> str | None:
    """Corrige texto UTF-8 mal decodificado como Latin-1 (metÃ¡lica -> metálica).

    AZETA mezcla codificaciones: la mayoría de fichas son ISO-8859-1 correctas,
    pero algunas descripciones llegan como UTF-8 (con secuencias 'Ã', 'Â').
    Solo se aplica cuando se detectan esas secuencias, para no romper el texto correcto.
    """
    if not t or ("Ã" not in t and "Â" not in t):
        return t
    try:
        return t.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return t


def _campos_detalle(soup) -> dict:
    """Lee las filas 'Etiqueta: valor' del bloque de detalle (div.filaDetalle)."""
    campos = {}
    for f in soup.find_all(attrs={"class": "filaDetalle"}):
        txt = f.get_text(" ", strip=True)
        if ":" in txt:
            label, _, val = txt.partition(":")
            label = _norm(label)
            val = val.strip()
            if label and val and label not in campos:
                campos[label] = val
    return campos


def _es_disponible(situacion: str | None):
    """True/False según la 'Situación', o None si no se puede determinar."""
    if not situacion:
        return None
    s = _norm(situacion)
    if any(m in s for m in _SITUACIONES_NO_DISPONIBLE):
        return False
    if "disponible" in s:
        return True
    return None


def _valor_material(soup):
    """Busca el valor de MATERIAL en la tabla de 'Datos técnicos'."""
    el = soup.find(string=re.compile(r"^\s*MATERIAL\s*$", re.I))
    if not el:
        return None
    celda = el.find_parent(["td", "th"])
    if celda:
        sig = celda.find_next_sibling(["td", "th"])
        if sig:
            return sig.get_text(" ", strip=True)
    return None


def _precio_num(texto: str | None):
    """'4.95 €' -> 4.95 (float) o None."""
    if not texto:
        return None
    m = re.search(r"(\d+[.,]\d{2})", str(texto))
    return float(m.group(1).replace(",", ".")) if m else None


# Recargo de equivalencia español según el tipo de IVA
_RECARGO_POR_IVA = {21: 5.2, 10: 1.4, 5: 0.62, 4: 0.5, 0: 0.0}


def _recargo_de(iva_pct):
    if iva_pct is None:
        return 0.0
    return _RECARGO_POR_IVA.get(int(round(iva_pct)), 0.0)


# --------------------------------------------------------------------------- #
# Parseo de la ficha
# --------------------------------------------------------------------------- #
def parsear_ficha(html: str, ean_buscado: str | None = None, url: str | None = None,
                  aplicar_recargo: bool = True) -> dict:
    """Extrae los campos de la ficha de producto a partir del HTML del detalle."""
    soup = BeautifulSoup(html, "lxml")

    # ¿Es una ficha de producto (detalle) o una página de resultados/sin resultado?
    es_detalle = "detalle.php" in (url or "") or soup.find(attrs={"class": "bookinfo"}) is not None

    # Nombre: div.bookinfo > h1/h2
    nombre = None
    info = soup.find(attrs={"class": "bookinfo"})
    if info and info.find(["h1", "h2"]):
        nombre = info.find(["h1", "h2"]).get_text(" ", strip=True)

    # Campos del bloque de detalle (Etiqueta: valor)
    campos = _campos_detalle(soup)
    ean = campos.get("ean")
    fabricante = campos.get("fabricante")
    situacion = campos.get("situacion")              # p.ej. "Disponible", "Agotado"
    disponibilidad = campos.get("disponibilidad")    # política, p.ej. "SIN DEVOLUCIÓN"
    material = campos.get("material") or _valor_material(soup)
    idioma = campos.get("idioma")
    ref_fabricante = campos.get("ref. fabricante") or campos.get("ref fabricante")

    # Precios:
    #   id=precio         -> precio POR UNIDAD sin IVA (lo que nos interesa de coste)
    #   id=precioDetalle  -> precio total del envase sin IVA (en expositores = unidad*uds)
    #   id=unidades_venta -> unidades por envase (1 en productos normales)
    precio_unidad = _precio_num(_texto(soup, id="precio"))
    precio_envase_txt = _texto(soup, id="precioDetalle")
    precio_envase = _precio_num(precio_envase_txt)
    pvp_txt = _texto(soup, id="su_pvpDetalle") or _texto(soup, id="su_pvp")
    uds_txt = _texto(soup, id="unidades_venta")
    try:
        unidades_venta = int(re.sub(r"\D", "", uds_txt)) if uds_txt else 1
    except ValueError:
        unidades_venta = 1
    if unidades_venta < 1:
        unidades_venta = 1
    if precio_unidad is None:  # fallback: precio por unidad = envase / uds
        precio_unidad = round(precio_envase / unidades_venta, 4) if precio_envase else None

    # IVA (% del onclick de "Su PVP") y recargo de equivalencia
    miva = re.search(r"susPVP\('[^']*',\s*'[^']*',\s*'(\d+)'", html)
    iva_pct = int(miva.group(1)) if miva else None
    recargo_pct = _recargo_de(iva_pct) if aplicar_recargo else 0.0

    # Coste real por unidad = precio_unidad * (1 + IVA% + recargo%)
    coste_real_unidad = None
    if precio_unidad is not None:
        factor = 1 + ((iva_pct or 0) + recargo_pct) / 100.0
        coste_real_unidad = round(precio_unidad * factor, 2)

    descripcion = None
    sin = soup.find(attrs={"class": re.compile(r"\bsinopsis\b", re.I)})
    if sin:
        descripcion = re.sub(r"^\s*Acerca del producto\s*", "", sin.get_text(" ", strip=True))

    # Imágenes: se filtran por el EAN REAL de la ficha (no por el patrón buscado,
    # porque AZETA puede resolver un código a un producto con EAN distinto).
    base = (ean or ean_buscado or "").strip()[:12]
    imagenes = []
    for m in re.findall(r"https://static\.azetadistribuciones\.es/imagenes[^\s\"'<>]+", html):
        if (not base or base in m) and m not in imagenes:
            imagenes.append(m)

    encontrado = bool(es_detalle and nombre)
    if not encontrado:
        imagenes = []

    # Disponibilidad basada en el campo "Situación" (no en la coincidencia de EAN):
    #   - sin ficha           -> no disponible (desaparecido del catálogo)
    #   - Situación = Agotado/Descatalogado/... -> no disponible
    #   - Situación = Disponible o desconocida   -> disponible
    estado_sit = _es_disponible(situacion)
    disponible = bool(encontrado and estado_sit is not False)

    # ¿Permite DROPSHIPPING? Hay botón <a class="... DS ..." onclick="meterCestaDS(...)">
    # (la función JS meterCestaDS siempre está definida; lo que distingue es el BOTÓN).
    dropshipping = bool(
        re.search(r'<a[^>]*class="[^"]*\bDS\b[^"]*"[^>]*onclick="meterCestaDS\(', html)
    )

    # Corregir posible mojibake (UTF-8 mal decodificado) en los textos
    nombre = _fix_mojibake(nombre)
    fabricante = _fix_mojibake(fabricante)
    material = _fix_mojibake(material)
    descripcion = _fix_mojibake(descripcion)

    return {
        "encontrado": encontrado,
        "disponible": disponible,
        "nombre": nombre,
        "ean": ean,
        "fabricante": fabricante,
        "material": material,
        "idioma": idioma,
        "ref_fabricante": ref_fabricante,
        # Precio por unidad (lo relevante de coste)
        "precio_sin_iva": precio_unidad,
        "precio_unidad_sin_iva": precio_unidad,
        # Envase / expositor
        "precio_envase_sin_iva": precio_envase,
        "unidades_venta": unidades_venta,
        "es_expositor": unidades_venta > 1,
        # Impuestos y coste real
        "iva_pct": iva_pct,
        "recargo_pct": recargo_pct,
        "coste_real_unidad": coste_real_unidad,
        # PVP recomendado
        "pvp_recomendado": _precio_num(pvp_txt),
        "pvp_recomendado_txt": pvp_txt,
        "situacion": situacion,
        "disponibilidad": disponibilidad,
        "dropshipping": dropshipping,
        "descripcion": descripcion,
        "imagenes": imagenes,
    }


def buscar_producto(patron: str, sesion: AzetaSession | None = None,
                    aplicar_recargo: bool | None = None) -> dict:
    """Busca por EAN/ISBN/texto y devuelve la ficha del producto como dict."""
    if aplicar_recargo is None:
        try:
            from config import aplicar_recargo as _ar
            aplicar_recargo = _ar()
        except Exception:
            aplicar_recargo = True
    if sesion is None:
        sesion = AzetaSession()
        sesion.login()
    url = BUSCAR_URL.format(patron=patron)
    resp = sesion.get(url)
    datos = parsear_ficha(resp.text, ean_buscado=patron, url=resp.url,
                          aplicar_recargo=aplicar_recargo)
    datos["patron"] = patron
    datos["url_ficha"] = resp.url
    return datos


def _imprimir(datos: dict):
    print("=" * 60)
    print(f"  {datos.get('nombre') or '(no encontrado)'}")
    print("=" * 60)
    print(f"  Encontrado.....: {datos.get('encontrado')}")
    print(f"  Disponible.....: {datos.get('disponible')}")
    print(f"  EAN............: {datos.get('ean')}")
    print(f"  Fabricante.....: {datos.get('fabricante')}")
    print(f"  Material.......: {datos.get('material')}")
    print(f"  Precio S/IVA...: {datos.get('precio_sin_iva')}")
    print(f"  PVP recomendado: {datos.get('pvp_recomendado')}")
    print(f"  Situación......: {datos.get('situacion')}")
    print(f"  Descripción....: {(datos.get('descripcion') or '')[:200]}")
    print(f"  Imágenes ({len(datos.get('imagenes') or [])}):")
    for u in datos.get("imagenes") or []:
        print(f"    - {u}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python azeta_producto.py <EAN/ISBN/texto> [--json archivo.json]")
        sys.exit(1)
    patron = sys.argv[1]
    try:
        datos = buscar_producto(patron)
    except AzetaError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)
    _imprimir(datos)
    if "--json" in sys.argv:
        destino = sys.argv[sys.argv.index("--json") + 1]
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        print(f"\n✓ Guardado en {destino}")
