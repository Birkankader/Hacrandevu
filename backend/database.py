"""SQLite veritabanı — hasta profilleri CRUD."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "hacrandevu.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Tablo yoksa oluştur."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            tc_kimlik    TEXT NOT NULL UNIQUE,
            dogum_tarihi TEXT NOT NULL,
            phone        TEXT DEFAULT '',
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def _row_to_dict(row) -> dict:
    return dict(row) if row else None


def get_all_patients() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM patients ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_patient(patient_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def create_patient(name: str, tc_kimlik: str, dogum_tarihi: str, phone: str = "") -> dict:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO patients (name, tc_kimlik, dogum_tarihi, phone) VALUES (?, ?, ?, ?)",
        (name, tc_kimlik, dogum_tarihi, phone),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM patients WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


def update_patient(patient_id: int, **kwargs) -> dict | None:
    allowed = {"name", "tc_kimlik", "dogum_tarihi", "phone"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return get_patient(patient_id)
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [patient_id]
    conn = _get_conn()
    conn.execute(f"UPDATE patients SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def delete_patient(patient_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0
