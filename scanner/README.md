# Escáner AZETA + Liderpapel (móvil)

App web para **escanear un código de barras con la cámara del móvil** y ver la
ficha del producto en **AZETA** y en **Liderpapel / CS Papelería** a la vez, con
el **precio/coste comparado**. Si el EAN no aparece en ninguno, muestra un aviso
con el EAN buscado.

Reutiliza los scrapers que ya tienes:
`../azeta_producto.py` y `../cspapeleria/cs_producto.py` (con sus `.env` y
credenciales). No duplica lógica: solo los orquesta y añade la interfaz móvil.

---

## Cómo funciona

- El **móvil** hace el escaneo en el navegador (librería ZXing, sin instalar nada).
- Al leer un EAN, llama al **backend Flask** que corre en tu PC.
- El backend consulta **AZETA y Liderpapel en paralelo** (login reutilizado) y
  devuelve ambas fichas + qué proveedor sale más barato por *coste real/unidad*.

La cámara del navegador **solo funciona sobre HTTPS** (o en `localhost`). Por eso
el servidor arranca con un **certificado autofirmado** que se genera solo la
primera vez. El móvil pedirá aceptar un aviso de "sitio no seguro": es normal.

---

## Arranque rápido (Windows)

1. Doble clic en **`run_scanner.bat`** (la 1ª vez instala dependencias y crea el
   certificado; anota la IPv4 que muestra).
2. En el móvil, **misma WiFi**, abre: `https://LA-IP-DE-TU-PC:5002`
   (por ejemplo `https://192.168.18.7:5002`).
3. Acepta el aviso del certificado → **Avanzado → Continuar**.
4. Permite el acceso a la **cámara** y apunta al código de barras.

### Arranque manual

```bash
cd scanner
pip install -r requirements.txt
python gen_cert.py        # crea cert.pem y key.pem (solo la 1ª vez)
python scanner_app.py     # sirve en https://0.0.0.0:5002
```

Puerto configurable con la variable `SCANNER_PORT`.

---

## Uso

- **Escanear**: apunta la cámara al código. Vibra al leerlo y busca automáticamente.
- **A mano**: escribe el EAN en la caja y pulsa *Buscar* (útil si el código está
  dañado o es un móvil sin buena cámara).
- **Resultado**: dos tarjetas (AZETA / Liderpapel) con nombre, imágenes, precio
  neto €/ud, **coste real** (IVA + recargo), disponibilidad y datos extra. Arriba,
  un banner indica **cuál es más barato** y el ahorro por unidad.
- **No encontrado**: banner rojo con el **EAN buscado** para que lo verifiques.

> Nota de coste: el coste real = precio neto × (1 + IVA% + recargo de equivalencia).
> Liderpapel no publica el IVA en su B2B, así que se estima al 21% (configurable en
> `../cspapeleria/.env`, variable `CS_IVA_DEFAULT`).

---

## Requisitos previos

- Que funcionen los dos scrapers de la carpeta padre (credenciales en
  `../.env` para AZETA y `../cspapeleria/.env` para Liderpapel).
- Móvil y PC en la **misma red WiFi**.
- Firewall de Windows: permitir a Python aceptar conexiones en la red privada
  (Windows lo pregunta la primera vez; marca "Redes privadas").

---

## Archivos

| Archivo | Función |
|---|---|
| `scanner_app.py` | Backend Flask: `/` (página) y `/api/buscar?ean=` (consulta ambos) |
| `templates/scanner.html` | Interfaz móvil + escáner de cámara (ZXing) |
| `gen_cert.py` | Genera el certificado HTTPS autofirmado (solo local) |
| `run_scanner.bat` | Arranque en Windows (instala, genera cert, muestra IP) |
| `Procfile` | Arranque con gunicorn (para Render) |
| `requirements.txt` | Dependencias |

---

## Desplegar en Render (gratis) para acceder desde cualquier sitio (4G)

Con esto la app queda en internet con HTTPS de verdad (sin avisos de certificado)
y accesible desde el móvil aunque no estés en casa. Protegida con **contraseña**.

**Antes de empezar, ten en cuenta:**
- El plan gratis **se duerme a los 15 min** de inactividad; la primera búsqueda
  tras un rato tarda ~60 s en despertar. Luego va normal.
- Tus **credenciales de proveedor viajan a Render** (se guardan como variables de
  entorno, no en el código). La app pide usuario/contraseña para entrar.
- Riesgo conocido: el WAF de Liderpapel podría bloquear la IP de Render y dar 403.
  AZETA no se ve afectado. Si pasara, se puede cambiar `CS_IMPERSONATE` o mover
  solo esa parte a otro sitio.

**Pasos:**

1. **Sube el proyecto a GitHub.** Debe subirse **toda la carpeta**
   `Automatización AZETA` (no solo `scanner/`), porque la app usa los scrapers de
   la carpeta padre. El `.gitignore` de la raíz ya evita subir `.env`, `*.db` y
   los certificados (tus credenciales **no** se suben).

   ```bash
   cd "Automatización AZETA"
   git init && git add . && git commit -m "scanner"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
   git push -u origin main
   ```

2. **En Render** (https://render.com, cuenta gratis, sin tarjeta):
   *New > Blueprint* y conecta el repo. Render leerá `render.yaml` automáticamente.
   (Alternativa manual: *New > Web Service*, y pon **Root Directory** = `scanner`,
   Build = `pip install -r requirements.txt`, Start = el comando del `Procfile`.)

3. **Rellena las variables de entorno** (en el panel de Render, marcadas como
   secretas en el blueprint):

   | Variable | Valor |
   |---|---|
   | `AZETA_USER` / `AZETA_PASSWORD` | tus credenciales de AZETA |
   | `CS_USER` / `CS_PASS` | tus credenciales de Liderpapel / CS Papelería |
   | `CS_IMPERSONATE` | `chrome` |
   | `APP_USER` / `APP_PASSWORD` | usuario y **contraseña** para entrar a la app |

4. **Deploy.** Al terminar, Render te da una URL `https://...onrender.com`. Ábrela
   en el móvil, introduce usuario/contraseña, permite la cámara y a escanear.

> La protección por contraseña **solo se activa si defines `APP_PASSWORD`**. En
> local (sin esa variable) la app sigue abierta, que es lo cómodo para pruebas.
