"""Clinical Notes Service — записи прийому лікаря у форматі SOAP (core-контекст).

Під час створення запису сервіс виступає ОРКЕСТРАТОРОМ:
  1) синхронно перевіряє пацієнта в Patients Service;
  2) синхронно створює призначення через Prescriptions Service;
  3) зберігає запис і публікує подію note.created у RabbitMQ.
Далі інші сервіси реагують на подію самостійно (ХОРЕОГРАФІЯ).
"""
from datetime import date

from fastapi import HTTPException
from pydantic import BaseModel, Field

from common import broker
from common.config import whoami
from common.db import Jsonb, connect, init_schema
from common.discovery import CircuitOpenError, ServiceClient, ServiceUnavailableError
from common.web import create_app

PREFIX = "/api/notes"

DDL = """
CREATE TABLE IF NOT EXISTS notes (
    id            SERIAL PRIMARY KEY,
    patient_id    INT NOT NULL,
    subjective    TEXT NOT NULL,
    objective     TEXT,
    assessment    TEXT,
    icd10         TEXT,
    diagnosis     TEXT,
    plan          TEXT,
    follow_up     DATE,
    prescriptions JSONB NOT NULL DEFAULT '[]',
    warnings      JSONB NOT NULL DEFAULT '[]',
    version       INT NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Фрагмент довідника МКХ-10
ICD10 = {
    "J06.9": "Гостра інфекція верхніх дихальних шляхів, неуточнена",
    "J18.9": "Пневмонія, неуточнена",
    "I10": "Есенціальна (первинна) гіпертензія",
    "E11": "Цукровий діабет 2 типу",
    "K29.7": "Гастрит, неуточнений",
    "M54.5": "Біль у нижній частині спини",
    "R51": "Головний біль",
}

patients = ServiceClient("patients")
prescriptions = ServiceClient("prescriptions")
app = create_app("Clinical Notes Service", PREFIX, on_startup=lambda: init_schema(DDL))


class RxIn(BaseModel):
    drug: str
    dose: str
    days: int = Field(gt=0)


class NoteIn(BaseModel):
    patient_id: int
    subjective: str = Field(description="S — скарги пацієнта", examples=["Біль у горлі, температура 38.2 два дні"])
    objective: str | None = Field(default=None, description="O — дані огляду", examples=["Гіперемія зіву, ЧСС 88"])
    assessment: str | None = Field(default=None, description="A — оцінка/діагноз", examples=["ГРВІ"])
    icd10: str | None = Field(default=None, examples=["J06.9"])
    plan: str | None = Field(default=None, description="P — план лікування", examples=["Рясне пиття, контроль через 7 днів"])
    follow_up: date | None = Field(default=None, description="Дата повторного огляду")
    prescriptions: list[RxIn] = Field(default_factory=list)


class NoteUpdate(BaseModel):
    assessment: str | None = None
    icd10: str | None = None
    plan: str | None = None
    follow_up: date | None = None


def _icd_name(code: str | None) -> str | None:
    if code is None:
        return None
    code = code.upper().strip()
    if code not in ICD10:
        raise HTTPException(422, f"Невідомий код МКХ-10 «{code}». Доступні: {', '.join(ICD10)}")
    return ICD10[code]


def _get_patient(patient_id: int) -> dict:
    try:
        resp = patients.get(f"/api/patients/{patient_id}")
    except (CircuitOpenError, ServiceUnavailableError) as e:
        raise HTTPException(503, f"Сервіс пацієнтів недоступний: {e}") from e
    if resp.status_code == 404:
        raise HTTPException(404, "Пацієнта не знайдено")
    return resp.json()


@app.post(PREFIX, status_code=201, tags=["notes"])
def create_note(note: NoteIn):
    patient = _get_patient(note.patient_id)          # крок 1: синхронно
    diagnosis = _icd_name(note.icd10)

    created_rx, warnings = [], []
    for rx in note.prescriptions:                     # крок 2: синхронно
        try:
            resp = prescriptions.post("/api/prescriptions", json={"patient_id": note.patient_id, **rx.model_dump()})
            if resp.status_code == 201:
                created_rx.append(resp.json())
            else:
                warnings.append(resp.json().get("detail", f"Помилка {resp.status_code}"))
        except (CircuitOpenError, ServiceUnavailableError) as e:
            warnings.append(f"Призначення «{rx.drug}» не створено: {e}")

    with connect() as conn:                           # крок 3: зберігаємо
        row = conn.execute(
            """INSERT INTO notes (patient_id, subjective, objective, assessment, icd10, diagnosis,
                                  plan, follow_up, prescriptions, warnings)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (note.patient_id, note.subjective, note.objective, note.assessment,
             note.icd10.upper() if note.icd10 else None, diagnosis, note.plan, note.follow_up,
             Jsonb([{"id": r["id"], "drug": r["drug"], "dose": r["dose"], "days": r["days"]} for r in created_rx]),
             Jsonb(warnings)),
        ).fetchone()

    broker.publish("note.created", {                  # крок 4: асинхронно
        "note_id": row["id"], "patient_id": note.patient_id, "patient_name": patient["full_name"],
        "icd10": row["icd10"], "diagnosis": diagnosis, "follow_up": row["follow_up"],
    })
    return row


@app.get(PREFIX, tags=["notes"])
def list_notes(patient_id: int | None = None):
    with connect() as conn:
        if patient_id:
            return conn.execute("SELECT * FROM notes WHERE patient_id=%s ORDER BY id DESC", (patient_id,)).fetchall()
        return conn.execute("SELECT * FROM notes ORDER BY id DESC").fetchall()


@app.get(PREFIX + "/chain", tags=["demo"])
def chain(msg: str = "запит"):
    """Початок синхронного ланцюжка: notes -> prescriptions -> patients.
    Кожен сервіс дописує до рядка своє ім'я та інстанс."""
    msg = f"{msg} -> {whoami()}"
    try:
        return prescriptions.get("/api/prescriptions/chain", params={"msg": msg}).json()
    except (CircuitOpenError, ServiceUnavailableError) as e:
        return {"message": f"{msg} -> [prescriptions недоступний: {e}]"}


@app.get(PREFIX + "/{note_id}", tags=["notes"])
def get_note(note_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id=%s", (note_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Запис не знайдено")
    return row


@app.patch(PREFIX + "/{note_id}", tags=["notes"])
def update_note(note_id: int, upd: NoteUpdate):
    changes = upd.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "Немає змін")
    if "icd10" in changes:
        changes["diagnosis"] = _icd_name(changes["icd10"])
        changes["icd10"] = changes["icd10"].upper() if changes["icd10"] else None
    sets = ", ".join(f"{k}=%s" for k in changes)
    with connect() as conn:
        row = conn.execute(
            f"UPDATE notes SET {sets}, version=version+1, updated_at=now() WHERE id=%s RETURNING *",
            (*changes.values(), note_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Запис не знайдено")
    broker.publish("note.updated", {
        "note_id": row["id"], "patient_id": row["patient_id"], "changed": list(changes),
        "version": row["version"], "follow_up": row["follow_up"],
    })
    return row
