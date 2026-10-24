"""Prescriptions Service — призначення ліків з перевіркою алергій.

Синхронно звертається до Patients Service (через Consul discovery),
щоб отримати алергії пацієнта перед створенням призначення.
"""
from fastapi import HTTPException
from pydantic import BaseModel, Field

from common import broker
from common.config import whoami
from common.db import connect, init_schema
from common.discovery import CircuitOpenError, ServiceClient, ServiceUnavailableError
from common.web import create_app

PREFIX = "/api/prescriptions"

DDL = """
CREATE TABLE IF NOT EXISTS prescriptions (
    id          SERIAL PRIMARY KEY,
    patient_id  INT NOT NULL,
    drug        TEXT NOT NULL,
    drug_group  TEXT,
    dose        TEXT NOT NULL,
    days        INT NOT NULL,
    overridden  BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Невеликий довідник: препарат -> фармакологічна група (для перевірки алергій)
DRUG_GROUPS = {
    "амоксицилін": "пеніциліни",
    "ампіцилін": "пеніциліни",
    "аугментин": "пеніциліни",
    "цефтріаксон": "цефалоспорини",
    "цефуроксим": "цефалоспорини",
    "азитроміцин": "макроліди",
    "ібупрофен": "нпзп",
    "диклофенак": "нпзп",
    "аспірин": "нпзп",
    "парацетамол": "анальгетики",
    "метформін": "бігуаніди",
    "лізиноприл": "іапф",
    "амлодипін": "блокатори кальцієвих каналів",
}

patients = ServiceClient("patients")
app = create_app("Prescriptions Service", PREFIX, on_startup=lambda: init_schema(DDL))


class PrescriptionIn(BaseModel):
    patient_id: int
    drug: str = Field(examples=["Ібупрофен"])
    dose: str = Field(examples=["400 мг 3 рази на день"])
    days: int = Field(gt=0, examples=[5])
    override_allergy: bool = Field(default=False, description="Лікар свідомо ігнорує попередження")


def allergy_conflict(drug: str, allergies: list[str]) -> str | None:
    """Повертає назву алергену, якщо препарат протипоказаний, інакше None."""
    name = drug.strip().lower()
    group = DRUG_GROUPS.get(name)
    allergies = [a.lower() for a in allergies]
    if name in allergies:
        return name
    if group and group in allergies:
        return group
    return None


def fetch_patient(patient_id: int) -> dict:
    """Синхронний виклик Patients Service."""
    try:
        resp = patients.get(f"/api/patients/{patient_id}")
    except CircuitOpenError as e:
        raise HTTPException(503, f"Сервіс пацієнтів тимчасово відключено: {e}") from e
    except ServiceUnavailableError as e:
        raise HTTPException(503, f"Сервіс пацієнтів недоступний: {e}") from e
    if resp.status_code == 404:
        raise HTTPException(404, "Пацієнта не знайдено")
    return resp.json()


@app.post(PREFIX, status_code=201, tags=["prescriptions"])
def create_prescription(rx: PrescriptionIn):
    patient = fetch_patient(rx.patient_id)
    allergen = allergy_conflict(rx.drug, patient["allergies"])
    if allergen and not rx.override_allergy:
        raise HTTPException(
            409,
            f"УВАГА: у пацієнта {patient['full_name']} алергія на «{allergen}». "
            f"Призначення «{rx.drug}» заблоковано.",
        )
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO prescriptions (patient_id, drug, drug_group, dose, days, overridden)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (rx.patient_id, rx.drug, DRUG_GROUPS.get(rx.drug.lower()), rx.dose, rx.days, bool(allergen)),
        ).fetchone()
    broker.publish("prescription.created", {
        "prescription_id": row["id"], "patient_id": rx.patient_id,
        "patient_name": patient["full_name"], "drug": rx.drug, "dose": rx.dose, "days": rx.days,
    })
    return row


@app.get(PREFIX, tags=["prescriptions"])
def list_prescriptions(patient_id: int | None = None):
    with connect() as conn:
        if patient_id:
            return conn.execute("SELECT * FROM prescriptions WHERE patient_id=%s ORDER BY id", (patient_id,)).fetchall()
        return conn.execute("SELECT * FROM prescriptions ORDER BY id").fetchall()


@app.get(PREFIX + "/chain", tags=["demo"])
def chain(msg: str = "запит"):
    """Середина ланцюжка: дописує себе і викликає patients."""
    msg = f"{msg} -> {whoami()}"
    try:
        return patients.get("/api/patients/chain", params={"msg": msg}).json()
    except (CircuitOpenError, ServiceUnavailableError) as e:
        return {"message": f"{msg} -> [patients недоступний: {e}]"}


@app.get(PREFIX + "/circuit", tags=["infra"])
def circuit_state():
    """Стан circuit breaker для виклику Patients Service."""
    b = patients.breaker
    return {"target": b.name, "state": b.state, "failures": b.failures}
