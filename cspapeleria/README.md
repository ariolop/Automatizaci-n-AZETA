# Scraper CS Papelería / Liderpapel (B2B)

Automatización para extraer datos de productos del portal mayorista **Comercial
del Sur de Papelería** (`https://b2b.cspapeleria.com`), que es a donde redirige el
"Acceso Web Profesional" de Liderpapel. Proyecto separado del de AZETA.

## Diferencia clave con AZETA: WAF de Akamai

El borde de la web es **AkamaiGHost** y devuelve **403 Forbidden** a cualquier
cliente HTTP cuya huella TLS (JA3) no sea la de un navegador real — un `requests`
normal (como el que usa AZETA) queda bloqueado. La solución es usar
**`curl_cffi` con `impersonate="chrome"`**, que reproduce el handshake TLS de
Chrome y pasa el filtro. Verificado el 26/07/2026 (login + búsqueda + ficha)
incluso desde IP de datacenter, así que no hace falta navegador ni resolver
captchas.

Si algún día Akamai endurece el filtro, prueba otros perfiles en `.env`
(`CS_IMPERSONATE=chrome120`, `chrome110`, etc.).

## Instalación

```
pip install -r requirements.txt
```

Credenciales en `.env`:

```
CS_USER=52381
CS_PASS=********
```

## App web

```
python app.py     ->  http://127.0.0.1:5001
```

Pestañas: **Buscar** (individual), **Lote** (masivo con barra de progreso y CSV,
acepta pegar EANs o subir CSV/TXT/XLSX), **Monitor** (vigilar stock/precio con
histórico) y **Config** (editar el .env en caliente). Sin integración PrestaShop
(pospuesta).

## Uso por línea de comandos

```
python cs_producto.py 3086123594197            # buscar por EAN
python cs_producto.py "boligrafo bic" --no-exacto
python cs_producto.py 3086123594197 --json salida.json
python monitor.py --add 3086123594197          # vigilar
python monitor.py --run                         # comprobar e informar cambios
```

Devuelve un dict con: `codProduct`, `nombre`, `marca`, `ean` (el de la unidad),
`eans_detalle` (lista con el EAN de **Unidad** y el de **Caja**/embalaje y su
cantidad), `precio_neto` (€/ud sin IVA), `coste_real`, `iva`, `recargo`,
`stock`, `disponible`, `imagenes` (URLs), `descripcion`, `encontrado`.

## Endpoints del portal (referencia)

| Acción            | URL                                                             |
|-------------------|-----------------------------------------------------------------|
| Login (form)      | `POST /PublicControlServlet?o=login` — campos `user`,`password`,`credentialId`,`commit` |
| Home B2B          | `GET /ControlServlet?o=iniciob2b&p=1`                           |
| Buscar            | `GET /ControlServlet?o=searchprdb2b&p=4&searchString=<q>&flagExact=ON` |
| Ficha producto    | `GET /ControlServlet?o=productb2b&p=1&codProduct=<id>`          |
| Catálogo categoría| `GET /ControlServlet?o=catonlineb2b&p=2&codCatego=<n>`          |
| Imágenes          | `/resources/img/products/<cod>g.jpg` y `/multi/<cod>_sN_*.jpg`  |

## Estado

- [x] Login validado (curl_cffi / Akamai)
- [x] Búsqueda por EAN y por texto
- [x] Extracción de ficha (nombre, marca, EAN, precio, stock, imágenes, descripción)
- [x] Modelo de coste (IVA + recargo de equivalencia)
- [x] Monitor de stock/precio (SQLite + histórico)
- [x] App web (buscar / lote / monitor / config)
- [ ] Integración PrestaShop (pospuesta por decisión)

> Nota SQLite: si ejecutas sobre una unidad de red y da "disk I/O error",
> define `CS_DB_PATH` a una ruta en disco local. En Windows local no hace falta.
