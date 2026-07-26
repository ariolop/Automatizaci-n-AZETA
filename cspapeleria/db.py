"""
db.py — Persistencia SQLite para el monitor de CS Papelería (Liderpapel).

Tablas:
  vigilados  — productos a vigilar (por EAN) y su último estado conocido.
  historico  — cada comprobación que suponga un cambio (estado/stock/precio).
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get("CS_DB_PATH") or os.path.join(os.path.dirname(__file__), "cs_manager.db")


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = conectar()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS vigilados (
            ean         TEXT PRIMARY KEY,
            cod_product TEXT,
            nombre      TEXT,
            estado      TEXT,
            stock       INTEGER,
            precio_neto REAL,
            coste_real  REAL,
            added_at    INTEGER,
            last_check  INTEGER
        );
        CREATE TABLE IF NOT EXISTS historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ean         TEXT,
            ts          INTEGER,
            estado      TEXT,
            stock       INTEGER,
            precio_neto REAL,
            coste_real  REAL
        );
        CREATE INDEX IF NOT EXISTS idx_hist_ean ON historico(ean);
        """
    )
    con.commit()
    con.close()


# --- Vigilados -------------------------------------------------------------- #
def add_vigilado(ean, nombre=None):
    con = conectar()
    con.execute(
        "INSERT OR IGNORE INTO vigilados(ean, nombre, added_at) VALUES (?,?,?)",
        (str(ean), nombre, int(time.time())),
    )
    con.commit()
    con.close()


def quitar_vigilado(ean):
    con = conectar()
    con.execute("DELETE FROM vigilados WHERE ean=?", (str(ean),))
    con.commit()
    con.close()


def listar_vigilados():
    con = conectar()
    filas = con.execute("SELECT * FROM vigilados ORDER BY nombre IS NULL, nombre").fetchall()
    con.close()
    return [dict(f) for f in filas]


def get_vigilado(ean):
    con = conectar()
    f = con.execute("SELECT * FROM vigilados WHERE ean=?", (str(ean),)).fetchone()
    con.close()
    return dict(f) if f else None


def actualizar_vigilado(ean, cod_product, nombre, estado, stock, precio_neto, coste_real):
    con = conectar()
    con.execute(
        """UPDATE vigilados SET cod_product=?, nombre=COALESCE(?, nombre),
           estado=?, stock=?, precio_neto=?, coste_real=?, last_check=?
           WHERE ean=?""",
        (cod_product, nombre, estado, stock, precio_neto, coste_real,
         int(time.time()), str(ean)),
    )
    con.commit()
    con.close()


# --- Histórico -------------------------------------------------------------- #
def registrar_historico(ean, estado, stock, precio_neto, coste_real):
    con = conectar()
    con.execute(
        """INSERT INTO historico(ean, ts, estado, stock, precio_neto, coste_real)
           VALUES (?,?,?,?,?,?)""",
        (str(ean), int(time.time()), estado, stock, precio_neto, coste_real),
    )
    con.commit()
    con.close()


def historico_de(ean, limite=100):
    con = conectar()
    filas = con.execute(
        "SELECT * FROM historico WHERE ean=? ORDER BY ts DESC LIMIT ?",
        (str(ean), limite),
    ).fetchall()
    con.close()
    return [dict(f) for f in filas]


if __name__ == "__main__":
    init_db()
    print("BD inicializada en", DB_PATH)
