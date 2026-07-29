"""
lote.py — Procesamiento por lotes de EANs (versión 2)
-----------------------------------------------------
Permite procesar muchos EANs de una vez sin bloquear la petición HTTP:

  - extraer_eans / extraer_eans_de_archivo : sacan los EANs de un texto, CSV o Excel.
  - crear_job(eans, opciones)              : lanza un HILO en segundo plano que va
                                             procesando uno a uno (login único).
  - obtener_job(id)                        : estado actual del job (para sondear desde JS).

Así la petición que inicia el lote vuelve al instante (devuelve un job_id) y el
frontend va preguntando el progreso cada pocos segundos. Nada se queda esperando
ni da timeout.
"""

from __future__ import annotations

import copy
import io
import re
import threading
import time
import uuid
from datetime import datetime

import db
from azeta_login import AzetaSession, AzetaError
from azeta_producto import buscar_producto

# Almacén de jobs en memoria (se pierde al reiniciar la app, es intencional)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()

# EAN/ISBN: secuencia de 8 a 14 dígitos no pegada a otros dígitos
_EAN_RE = re.compile(r"(?<!\d)\d{8,14}(?!\d)")


# --------------------------------------------------------------------------- #
# Extracción de EANs
# --------------------------------------------------------------------------- #
def extraer_eans(texto: str) -> list[str]:
    """Saca los EANs de un texto libre (separados por coma, espacio, salto de línea…)."""
    vistos = []
    for m in _EAN_RE.findall(texto or ""):
        if m not in vistos:
            vistos.append(m)
    return vistos


def columna_a_indice(valor) -> int | None:
    """'A'->0, 'B'->1, 'AA'->26, '1'->0, '2'->1. Vacío/None -> None (todas)."""
    valor = (str(valor) if valor is not None else "").strip()
    if not valor:
        return None
    if valor.isdigit():
        n = int(valor)
        return n - 1 if n >= 1 else 0
    idx = 0
    for ch in valor.upper():
        if "A" <= ch <= "Z":
            idx = idx * 26 + (ord(ch) - 64)
        else:
            return None
    return idx - 1 if idx >= 1 else None


def extraer_eans_de_archivo(nombre: str, contenido: bytes,
                            columna="" , fila_inicial: int = 1) -> list[str]:
    """Saca los EANs de un CSV/TXT/Excel.

    - columna: letra (A, B…) o número (1, 2…) donde están los EANs. Vacío = buscar
               en todas las columnas.
    - fila_inicial: nº de fila (1-based) por la que empezar (para saltar cabeceras).
    """
    nombre = (nombre or "").lower()
    col = columna_a_indice(columna)
    try:
        fila_inicial = max(1, int(fila_inicial))
    except (TypeError, ValueError):
        fila_inicial = 1

    if nombre.endswith(".xlsx"):
        try:
            import openpyxl
        except ImportError:
            return []
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        partes = []
        for ws in wb.worksheets:
            for r, fila in enumerate(ws.iter_rows(values_only=True), start=1):
                if r < fila_inicial:
                    continue
                if col is None:
                    for celda in fila:
                        if celda is not None:
                            partes.append(str(celda))
                elif col < len(fila) and fila[col] is not None:
                    partes.append(str(fila[col]))
        return extraer_eans(" ".join(partes))

    # CSV / TXT (probamos varias codificaciones)
    texto = ""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            texto = contenido.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not texto:
        texto = contenido.decode("latin-1", "ignore")

    import csv
    muestra = texto[:2000]
    delim = ";" if muestra.count(";") >= muestra.count(",") else ","
    partes = []
    for r, fila in enumerate(csv.reader(io.StringIO(texto), delimiter=delim), start=1):
        if r < fila_inicial:
            continue
        if col is None:
            partes.append(" ".join(fila))
        elif col < len(fila):
            partes.append(fila[col])
    return extraer_eans(" ".join(partes))


# --------------------------------------------------------------------------- #
# Jobs en segundo plano
# --------------------------------------------------------------------------- #
def crear_job(eans: list[str], opciones: dict) -> str:
    """Crea un job y lanza el hilo que lo procesa. Devuelve el job_id."""
    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid,
        "estado": "en_curso",
        "creado": datetime.now().isoformat(timespec="seconds"),
        "total": len(eans),
        "procesados": 0,
        "opciones": opciones,
        "aviso": None,
        "resumen": {"disponibles": 0, "subidos": 0, "ya_existen": 0,
                    "no_disponibles": 0, "errores": 0},
        "items": [{"ean": e, "estado": "pendiente"} for e in eans],
    }
    with _lock:
        _jobs[jid] = job
    hilo = threading.Thread(target=_procesar, args=(jid, eans, opciones), daemon=True)
    hilo.start()
    return jid


def obtener_job(jid: str) -> dict | None:
    """Devuelve una copia del estado del job (segura para serializar a JSON)."""
    with _lock:
        job = _jobs.get(jid)
        return copy.deepcopy(job) if job else None


def _set(item: dict, **kw):
    item.update(kw)


def _procesar(jid: str, eans: list[str], opciones: dict):
    job = _jobs[jid]

    # 1) Login único para todo el lote
    try:
        sesion = AzetaSession()
        sesion.login()
    except AzetaError as e:
        with _lock:
            job["estado"] = "terminado"
            job["aviso"] = f"No se pudo iniciar sesión en AZETA: {e}"
            for it in job["items"]:
                if it["estado"] == "pendiente":
                    _set(it, estado="error", detalle=str(e))
            job["resumen"]["errores"] = len(eans)
        return

    subir = bool(opciones.get("subir"))
    vigilar = bool(opciones.get("vigilar"))

    # Comprobar qué EANs ya están en PrestaShop (aunque NO se vayan a subir).
    # Cargamos el catálogo una sola vez y comprobamos en memoria (eficiente).
    cliente = None
    existentes = {}
    from prestashop_client import prestashop_configurado
    if prestashop_configurado():
        try:
            from prestashop_client import PrestashopClient
            cliente = PrestashopClient()
            for p in cliente.listar_productos():
                e = (p.get("ean13") or "").strip()
                if e:
                    existentes[e] = p
        except Exception as e:
            job["aviso"] = f"No se pudo comprobar PrestaShop: {e}"
            cliente = None
    if subir and cliente is None:
        subir = False
        if not job.get("aviso"):
            job["aviso"] = "No se subirá a PrestaShop (no disponible)."

    # 2) Procesar uno a uno
    for idx, ean in enumerate(eans):
        item = job["items"][idx]
        try:
            d = buscar_producto(ean, sesion=sesion)
        except AzetaError as e:
            _set(item, estado="error", detalle=str(e))
            job["resumen"]["errores"] += 1
            job["procesados"] = idx + 1
            continue

        imagenes = d.get("imagenes") or []
        _set(
            item,
            nombre=d.get("nombre"),
            ean_real=d.get("ean"),
            disponible=d.get("disponible"),
            dropshipping=d.get("dropshipping"),
            precio=d.get("precio_unidad_sin_iva"),
            coste=d.get("coste_real_unidad"),
            imagen=imagenes[0] if imagenes else None,
        )

        if not d.get("encontrado"):
            _set(item, estado="no_encontrado")
            job["resumen"]["no_disponibles"] += 1
        elif not d.get("disponible"):
            _set(item, estado="no_disponible")
            job["resumen"]["no_disponibles"] += 1
        else:
            job["resumen"]["disponibles"] += 1
            if vigilar and d.get("ean"):
                import monitor_comun
                monitor_comun.añadir(d["ean"], "AZETA", nombre=d.get("nombre"), origen="manual")

            ya = existentes.get((d.get("ean") or "").strip())
            if ya:
                # Ya está en PrestaShop: no se vuelve a subir (warning)
                _set(item, estado="ya_existe", id_product=ya.get("id"),
                     activo_ps=ya.get("activo"))
                job["resumen"]["ya_existen"] += 1
            elif subir and cliente:
                try:
                    res = cliente.crear_producto(d)
                    _set(item, estado="subido", id_product=res.get("id_product"),
                         disponible_pedido=res.get("disponible_para_pedido"))
                    job["resumen"]["subidos"] += 1
                    if d.get("ean"):
                        import monitor_comun
                        monitor_comun.añadir(d["ean"], "AZETA", nombre=d.get("nombre"),
                                             origen="prestashop", id_prestashop=res.get("id_product"))
                except Exception as e:
                    _set(item, estado="error_subida", detalle=str(e))
                    job["resumen"]["errores"] += 1
            else:
                _set(item, estado="disponible")

        job["procesados"] = idx + 1
        time.sleep(0.3)  # pequeño respiro para no saturar AZETA

    job["estado"] = "terminado"
