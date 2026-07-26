"""
db.py
-----
Capa de datos SQLite para AZETA Manager.

Tablas:
  - vigilados : productos que se vigilan a diario (por EAN)
  - historico : registro de cada comprobación de disponibilidad
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "azeta_manager.db"


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    """Crea las tablas si no existen."""
    with _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS vigilados (
                ean            TEXT PRIMARY KEY,
                nombre         TEXT,
                origen         TEXT DEFAULT 'manual',   -- 'manual' | 'prestashop'
                id_prestashop  INTEGER,                 -- id del producto en PS (si aplica)
                ultimo_estado  TEXT,                    -- 'disponible' | 'desaparecido' | 'desconocido'
                ultima_revision TEXT,
                activo         INTEGER DEFAULT 1,        -- se sigue vigilando (1) o no (0)
                creado         TEXT
            );

            CREATE TABLE IF NOT EXISTS historico (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ean       TEXT,
                fecha     TEXT,
                estado    TEXT,
                nombre    TEXT,
                precio    REAL
            );
            """
        )


# --------------------------------------------------------------------------- #
# Vigilados
# --------------------------------------------------------------------------- #
def añadir_vigilado(ean: str, nombre: str | None = None, origen: str = "manual",
                    id_prestashop: int | None = None):
    ahora = datetime.now().isoformat(timespec="seconds")
    with _conn() as con:
        con.execute(
            """
            INSERT INTO vigilados (ean, nombre, origen, id_prestashop, ultimo_estado, activo, creado)
            VALUES (?, ?, ?, ?, 'desconocido', 1, ?)
            ON CONFLICT(ean) DO UPDATE SET
                nombre = COALESCE(excluded.nombre, vigilados.nombre),
                origen = excluded.origen,
                id_prestashop = COALESCE(excluded.id_prestashop, vigilados.id_prestashop),
                activo = 1
            """,
            (ean, nombre, origen, id_prestashop, ahora),
        )


def quitar_vigilado(ean: str):
    with _conn() as con:
        con.execute("DELETE FROM vigilados WHERE ean = ?", (ean,))


def listar_vigilados(solo_activos: bool = False) -> list[dict]:
    q = "SELECT * FROM vigilados"
    if solo_activos:
        q += " WHERE activo = 1"
    q += " ORDER BY (ultimo_estado='desaparecido') DESC, nombre"
    with _conn() as con:
        return [dict(r) for r in con.execute(q).fetchall()]


def actualizar_estado(ean: str, estado: str, nombre: str | None = None, precio: float | None = None):
    ahora = datetime.now().isoformat(timespec="seconds")
    with _conn() as con:
        con.execute(
            "UPDATE vigilados SET ultimo_estado=?, ultima_revision=?, nombre=COALESCE(?, nombre) WHERE ean=?",
            (estado, ahora, nombre, ean),
        )
        con.execute(
            "INSERT INTO historico (ean, fecha, estado, nombre, precio) VALUES (?,?,?,?,?)",
            (ean, ahora, estado, nombre, precio),
        )


def historico_de(ean: str, limite: int = 50) -> list[dict]:
    with _conn() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT * FROM historico WHERE ean=? ORDER BY fecha DESC LIMIT ?", (ean, limite)
            ).fetchall()
        ]


def resumen() -> dict:
    with _conn() as con:
        total = con.execute("SELECT COUNT(*) FROM vigilados WHERE activo=1").fetchone()[0]
        desap = con.execute(
            "SELECT COUNT(*) FROM vigilados WHERE activo=1 AND ultimo_estado IN ('desaparecido','agotado')"
        ).fetchone()[0]
    return {"total": total, "desaparecidos": desap}


if __name__ == "__main__":
    init_db()
    print(f"Base de datos lista en {DB_PATH}")
