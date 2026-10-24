"""Patients Service — картки пацієнтів (контекст «Пацієнти»)."""
from datetime import date

from fastapi import HTTPException
from pydantic import BaseModel, Field

from common import broker
from common.config import whoami
from common.db import Jsonb, connect, init_schema
from common.web import create_app

PREFIX = "/api/patients"

DDL = """
CREATE TABLE IF NOT EXISTS patients (
    id          SERIAL PRIMARY KEY,
    full_name   TEXT NOT NULL,
    birth_date  DATE,
    phone       TEXT,
    allergies   JSONB NOT NULL DEFAULT '[]',
    chronic     JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

app = create_app("Patients Service", PREFIX, on_startup=lambda: init_schema(DDL))


class PatientIn(BaseModel):
    full_name: str = Field(min_length=3, examples=["Іваненко Олена Петрівна"])
    birth_date: date | None = Field(default=None, examples=["1985-04-12"])
    phone: str | None = Field(default=None, examples=["+380671234567"])
    allergies: list[str] = Field(default_factory=list, examples=[["пеніциліни"]])
    chronic: list[str] = Field(default_factory=list, examples=[["гіпертензія"]])


def _normalize(items: list[str]) -> list[str]:
    return [i.strip().lower() for i in items if i.strip()]


@app.post(PREFIX, status_code=201, tags=["patients"])
def create_patient(p: PatientIn):
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO patients (full_name, birth_date, phone, allergies, chronic)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (p.full_name, p.birth_date, p.phone, Jsonb(_normalize(p.allergies)), Jsonb(_normalize(p.chronic))),
        ).fetchone()
    broker.publish("patient.created", {"patient_id": row["id"], "full_name": row["full_name"]})
    return row


@app.get(PREFIX, tags=["patients"])
def list_patients(q: str | None = None):
    with connect() as conn:
        if q:
            return conn.execute("SELECT * FROM patients WHERE full_name ILIKE %s ORDER BY id", (f"%{q}%",)).fetchall()
        return conn.execute("SELECT * FROM patients ORDER BY id").fetchall()


@app.get(PREFIX + "/chain", tags=["demo"])
def chain(msg: str = "запит"):
    """Кінець синхронного ланцюжка notes -> prescriptions -> patients."""
    return {"message": f"{msg} -> {whoami()}"}


@app.get(PREFIX + "/{patient_id}", tags=["patients"])
def get_patient(patient_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM patients WHERE id = %s", (patient_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Пацієнта не знайдено")
    return row


@app.put(PREFIX + "/{patient_id}", tags=["patients"])
def update_patient(patient_id: int, p: PatientIn):
    with connect() as conn:
        row = conn.execute(
            """UPDATE patients SET full_name=%s, birth_date=%s, phone=%s, allergies=%s, chronic=%s,
                      updated_at=now() WHERE id=%s RETURNING *""",
            (p.full_name, p.birth_date, p.phone, Jsonb(_normalize(p.allergies)), Jsonb(_normalize(p.chronic)), patient_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Пацієнта не знайдено")
    broker.publish("patient.updated", {"patient_id": row["id"], "allergies": row["allergies"]})
    return row
