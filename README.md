# AZETA Manager

Aplicación local para (1) **consultar productos de AZETA Distribuciones** por EAN y
(2) **vigilar a diario su disponibilidad**, con opción de **publicarlos en PrestaShop 9
desactivados** mediante un módulo propio.

AZETA no tiene API: la app obtiene los datos por scraping autenticado (sin navegador
ni captcha; el reCAPTCHA del portal está desactivado).

---

## 1. Requisitos e instalación de la app

```bash
pip install -r requirements.txt
```

Rellena el archivo `.env`:

```
AZETA_USER=180712
AZETA_PASSWORD=********
# PrestaShop (opcional, solo si vas a publicar/sincronizar):
PRESTASHOP_URL=https://tutienda.com
PRESTASHOP_TOKEN=********              # lo da el módulo (ver paso 3)
PRESTASHOP_CATEGORIA_DEFECTO=2         # id de categoría donde crear los productos
```

Arranca la app:

```bash
python app.py
```

Abre **http://127.0.0.1:5000** en el navegador.

---

## 2. Uso

**Buscar / Publicar**
Escribe un EAN, ISBN o texto. Verás la ficha (nombre, precio S/IVA, fabricante, EAN,
situación, descripción e imágenes). Con los checkbox decides si además:
- *Añadir al monitor de disponibilidad*, y/o
- *Subir a PrestaShop* (se crea **desactivado**). Si no marcas nada, solo se consulta.

**Monitor**
Tabla de productos vigilados con su estado (Disponible / Desaparecido) y última revisión.
Puedes añadir EANs a mano, importar los de PrestaShop, y pulsar *Comprobar ahora*.

---

## 3. Módulo PrestaShop 9 (`azetaconnector`)

Carpeta: `prestashop-module/azetaconnector/`

Instalación:
1. Comprime la carpeta `azetaconnector` en `azetaconnector.zip`.
2. En tu PrestaShop: **Módulos → Subir un módulo** y sube el zip (o copia la carpeta a
   `/modules/` de tu tienda).
3. Instálalo. Entra en su **Configuración**: ahí verás el **token** generado y las URLs
   de los endpoints. Copia el valor a `PRESTASHOP_TOKEN` y la URL base a `PRESTASHOP_URL`
   en el `.env`.

El módulo crea los productos con `active = 0` (desactivados), asigna fabricante
(creándolo si no existe), categoría por defecto e imágenes. Evita duplicados por EAN.

Endpoints (autenticados por token):
- `…/index.php?fc=module&module=azetaconnector&controller=crear`
- `…/index.php?fc=module&module=azetaconnector&controller=listado`

---

## 4. Comprobación diaria automática

Ejecuta el monitor sin abrir la app:

```bash
python monitor.py                 # comprueba los EANs vigilados
python monitor.py --sync-prestashop   # además importa antes los productos de PrestaShop
```

Para que corra solo cada día, crea una tarea en el **Programador de tareas de Windows**
que ejecute `python monitor.py` a la hora que quieras. Los resultados quedan en la base
de datos y los ves en la pestaña *Monitor*.

---

## Archivos

| Archivo | Función |
|---|---|
| `app.py` | App web Flask (las dos secciones) |
| `azeta_login.py` | Login/sesión autenticada en AZETA |
| `azeta_producto.py` | Búsqueda y extracción de la ficha |
| `monitor.py` | Comprobación de disponibilidad + histórico |
| `db.py` | Base de datos SQLite (`azeta_manager.db`) |
| `prestashop_client.py` | Cliente que habla con el módulo de PrestaShop |
| `templates/` | Plantillas HTML |
| `prestashop-module/azetaconnector/` | Módulo PrestaShop 9 |
| `.env` | Credenciales y configuración |
