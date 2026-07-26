"""
monitor.py — Vigila stock, precio y disponibilidad de los productos vigilados
en el portal B2B de CS Papelería (Liderpapel).

Estados:
  disponible   — la ficha existe y hay stock (> 0).
  agotado      — la ficha existe pero sin stock.
  desaparecido — la búsqueda ya no devuelve producto.

Cada comprobación que cambie estado / stock / precio se anota en el histórico.

Uso CLI:
  python monitor.py --add 3086123594197        # añadir a vigilancia
  python monitor.py --list                      # listar vigilados
  python monitor.py --run                        # comprobar todos e informar cambios
"""
import argparse

import db
from cs_login import CSSession, CSError
from cs_producto import buscar_producto


def estado_de(ficha):
    if not ficha.get("encontrado"):
        return "desaparecido"
    return "disponible" if ficha.get("disponible") else "agotado"


def comprobar_todos(sesion=None, on_item=None):
    """Comprueba todos los vigilados. Devuelve resumen con la lista de cambios."""
    db.init_db()
    ses = sesion or CSSession()
    vigilados = db.listar_vigilados()
    resumen = {"total": len(vigilados), "cambios": [], "errores": [], "ok": 0}

    for v in vigilados:
        ean = v["ean"]
        try:
            ficha = buscar_producto(ean, sesion=ses)
        except CSError as e:
            resumen["errores"].append({"ean": ean, "error": str(e)})
            continue

        estado = estado_de(ficha)
        stock = ficha.get("stock")
        precio = ficha.get("precio_neto")
        coste = ficha.get("coste_real")
        nombre = ficha.get("nombre") or v.get("nombre")

        cambio = {}
        if v.get("estado") != estado:
            cambio["estado"] = (v.get("estado"), estado)
        if v.get("stock") != stock:
            cambio["stock"] = (v.get("stock"), stock)
        if v.get("precio_neto") != precio:
            cambio["precio_neto"] = (v.get("precio_neto"), precio)

        primera_vez = v.get("last_check") is None
        if cambio or primera_vez:
            db.registrar_historico(ean, estado, stock, precio, coste)

        db.actualizar_vigilado(ean, ficha.get("codProduct"), nombre, estado,
                               stock, precio, coste)

        item = {"ean": ean, "nombre": nombre, "estado": estado,
                "stock": stock, "precio_neto": precio, "coste_real": coste,
                "cambio": cambio, "primera_vez": primera_vez}
        if cambio and not primera_vez:
            resumen["cambios"].append(item)
        else:
            resumen["ok"] += 1
        if on_item:
            on_item(item)

    return resumen


def main():
    ap = argparse.ArgumentParser(description="Monitor de stock/precio de CS Papelería.")
    ap.add_argument("--add", metavar="EAN", help="Añadir un EAN a vigilancia")
    ap.add_argument("--rm", metavar="EAN", help="Quitar un EAN de vigilancia")
    ap.add_argument("--list", action="store_true", help="Listar vigilados")
    ap.add_argument("--run", action="store_true", help="Comprobar todos e informar")
    args = ap.parse_args()
    db.init_db()

    if args.add:
        db.add_vigilado(args.add)
        print("Añadido a vigilancia:", args.add)
    if args.rm:
        db.quitar_vigilado(args.rm)
        print("Quitado:", args.rm)
    if args.list:
        for v in db.listar_vigilados():
            print(f"  {v['ean']} | {v.get('estado') or '-'} | stock {v.get('stock')} "
                  f"| {v.get('precio_neto')} € | {v.get('nombre') or ''}")
    if args.run:
        res = comprobar_todos()
        print(f"\nComprobados {res['total']} | cambios {len(res['cambios'])} "
              f"| errores {len(res['errores'])}")
        for c in res["cambios"]:
            print(f"  CAMBIO {c['ean']} {c['nombre']}: {c['cambio']}")
        for e in res["errores"]:
            print(f"  ERROR {e['ean']}: {e['error']}")


if __name__ == "__main__":
    main()
