"""
monitor.py
----------
Lógica de comprobación diaria de disponibilidad en AZETA.

Para cada EAN vigilado:
  - busca el producto en AZETA reutilizando una única sesión autenticada
  - registra el estado ('disponible' / 'desaparecido') y un punto en el histórico
  - devuelve un resumen con las novedades (productos que han desaparecido)

Uso por línea de comandos (apto para tarea programada diaria):
    python monitor.py
"""

from __future__ import annotations

import sys

import db
from azeta_login import AzetaSession, AzetaError
from azeta_producto import buscar_producto


def comprobar_todos(sincronizar_prestashop: bool = False) -> dict:
    """Comprueba todos los EANs vigilados. Devuelve un resumen."""
    db.init_db()

    # Opcional: traer/actualizar la lista desde PrestaShop antes de comprobar
    if sincronizar_prestashop:
        try:
            sincronizar_desde_prestashop()
        except Exception as e:  # no abortamos la comprobación por esto
            print(f"[aviso] No se pudo sincronizar con PrestaShop: {e}", file=sys.stderr)

    vigilados = db.listar_vigilados(solo_activos=True)
    if not vigilados:
        return {"comprobados": 0, "disponibles": 0, "desaparecidos": [], "errores": []}

    sesion = AzetaSession()
    sesion.login()

    disponibles = 0
    incidencias = []   # productos que han dejado de estar disponibles (agotado/desaparecido)
    errores = []

    for v in vigilados:
        ean = v["ean"]
        estado_previo = v["ultimo_estado"]
        try:
            datos = buscar_producto(ean, sesion=sesion)
        except AzetaError as e:
            errores.append({"ean": ean, "error": str(e)})
            continue

        if datos["disponible"]:
            estado = "disponible"
            disponibles += 1
        elif datos["encontrado"]:
            estado = "agotado"          # sigue en catálogo pero Situación no disponible
        else:
            estado = "desaparecido"     # ya no aparece en AZETA

        db.actualizar_estado(
            ean, estado,
            datos.get("nombre") or v.get("nombre"),
            datos.get("precio_sin_iva"),
        )

        # Novedad: pasa de disponible/desconocido a un estado problemático
        if estado != "disponible" and estado_previo != estado:
            incidencias.append({
                "ean": ean,
                "nombre": datos.get("nombre") or v.get("nombre"),
                "estado": estado,
                "situacion": datos.get("situacion"),
            })

    return {
        "comprobados": len(vigilados),
        "disponibles": disponibles,
        "desaparecidos": incidencias,   # se mantiene la clave por compatibilidad
        "incidencias": incidencias,
        "errores": errores,
    }


def sincronizar_desde_prestashop():
    """Trae la lista de productos de PrestaShop y la añade a vigilados."""
    from prestashop_client import PrestashopClient

    cliente = PrestashopClient()
    productos = cliente.listar_productos()
    for p in productos:
        ean = (p.get("ean13") or "").strip()
        if not ean:
            continue
        db.añadir_vigilado(
            ean,
            nombre=p.get("nombre"),
            origen="prestashop",
            id_prestashop=p.get("id"),
        )
    return len(productos)


if __name__ == "__main__":
    sync = "--sync-prestashop" in sys.argv
    resumen = comprobar_todos(sincronizar_prestashop=sync)
    print(f"Comprobados: {resumen['comprobados']} | "
          f"Disponibles: {resumen['disponibles']} | "
          f"Incidencias nuevas: {len(resumen['incidencias'])}")
    for d in resumen["incidencias"]:
        print(f"  ⚠ {d['estado'].upper()}: {d['ean']} - {d.get('nombre') or ''}")
    for e in resumen["errores"]:
        print(f"  ✗ ERROR {e['ean']}: {e['error']}")
