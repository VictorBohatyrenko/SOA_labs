"""Демонстрація роботи системи через API Gateway (http://localhost:8000).
Запуск:  python demo.py      (потрібен лише Python 3, без бібліотек)
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

GW = "http://localhost:8000"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(GW + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null"), r.headers.get("X-Served-By")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), e.headers.get("X-Served-By")


def title(text):
    print("\n" + "=" * 70 + f"\n{text}\n" + "=" * 70)


def wait_ready():
    print("Очікую готовність сервісів", end="", flush=True)
    for _ in range(90):
        try:
            ok = all(call("GET", f"/api/{s}/whoami")[0] == 200
                     for s in ("patients", "prescriptions", "notes", "audit"))
            if ok:
                print(" — готово")
                return
        except Exception:  # noqa: BLE001
            pass
        print(".", end="", flush=True)
        time.sleep(2)
    sys.exit("\nСервіси не піднялись. Перевірте: docker compose ps / docker compose logs")


if len(sys.argv) > 1 and sys.argv[1] == "circuit":
    # Спершу: docker compose stop patients
    title("Circuit Breaker: виклики prescriptions -> patients, коли patients зупинено")
    for i in range(1, 6):
        t0 = time.time()
        code, body, _ = call("POST", "/api/prescriptions",
                             {"patient_id": 1, "drug": "Ібупрофен", "dose": "400 мг", "days": 3})
        print(f"  запит {i}: HTTP {code} за {time.time() - t0:.2f} с — {body.get('detail', body)}")
        print(f"           стан запобіжника: {call('GET', '/api/prescriptions/circuit')[1]['state']}")
    sys.exit(0)

wait_ready()

title("1. Створення пацієнтів (Patients Service)")
_, p1, by = call("POST", "/api/patients", {
    "full_name": "Іваненко Олена Петрівна", "birth_date": "1985-04-12",
    "phone": "+380671234567", "allergies": ["Пеніциліни"], "chronic": ["гіпертензія"]})
print(f"#{p1['id']} {p1['full_name']}, алергії: {p1['allergies']}   (обробив {by})")
_, p2, by = call("POST", "/api/patients", {"full_name": "Петренко Андрій Іванович", "birth_date": "1990-09-01"})
print(f"#{p2['id']} {p2['full_name']}, алергії: {p2['allergies']}   (обробив {by})")

title("2. Синхронний ланцюжок notes -> prescriptions -> patients (HTTP/REST)")
_, chain, _ = call("GET", "/api/notes/chain?" + urllib.parse.urlencode({"msg": "Запит лікаря"}))
print(chain["message"])

title("3. Запис прийому (SOAP) з призначеннями — оркестрація в Notes Service")
code, note, by = call("POST", "/api/notes", {
    "patient_id": p1["id"],
    "subjective": "Біль у горлі, температура 38.2 °C протягом двох днів",
    "objective": "Гіперемія зіву, мигдалики збільшені, ЧСС 88",
    "assessment": "Гострий тонзиліт",
    "icd10": "J06.9",
    "plan": "Рясне пиття, полоскання, контроль через 7 днів",
    "follow_up": "2026-11-03",
    "prescriptions": [
        {"drug": "Амоксицилін", "dose": "500 мг 3 рази на день", "days": 7},
        {"drug": "Ібупрофен", "dose": "400 мг при температурі", "days": 3},
    ],
})
print(f"HTTP {code}, запис #{note['id']} ({note['diagnosis']}), обробив {by}")
print("Створені призначення:", [rx["drug"] for rx in note["prescriptions"]])
print("Попередження:")
for w in note["warnings"]:
    print("  !", w)

title("4. Оновлення запису (подія note.updated)")
code, upd, _ = call("PATCH", f"/api/notes/{note['id']}", {"plan": "Продовжити лікування", "follow_up": "2026-11-05"})
print(f"HTTP {code}, версія запису: {upd['version']}, повторний огляд: {upd['follow_up']}")

title("5. Горизонтальне масштабування: 12 запитів до /api/patients/whoami")
counts = Counter(call("GET", "/api/patients/whoami")[1]["instance"] for _ in range(12))
for inst, n in counts.items():
    print(f"  інстанс {inst}: {n} запитів")
if len(counts) == 1:
    print("  (працює один інстанс; запустіть: docker compose up -d --scale patients=3)")

title("6. Асинхронна обробка: журнал Audit Service")
time.sleep(2)
_, stats, _ = call("GET", "/api/audit/stats")
for row in stats["by_type"]:
    print(f"  {row['type']:22} {row['n']}")
print("Хто обробив події:")
for row in stats["by_consumer"]:
    print(f"  {row['consumed_by']:30} {row['n']}")
print("\nНагадування дивіться в логах: docker compose logs notifications")
