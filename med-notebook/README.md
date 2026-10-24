# MedNotebook — хмарний записник лікаря (ЛР2, мікросервіси)

## Склад системи

| Сервіс | Що робить | Як спілкується |
| --- | --- | --- |
| **patients** | картки пацієнтів, алергії, хронічні хвороби | REST; публікує `patient.created/updated` |
| **prescriptions** | призначення ліків з перевіркою алергій | синхронно викликає **patients**; публікує `prescription.created` |
| **notes** (core) | записи прийому у форматі SOAP + коди МКХ-10 | оркеструє виклики **patients** і **prescriptions**; публікує `note.created/updated` |
| **audit** | журнал усіх дій з медичними даними | споживає всі події з RabbitMQ (`#`) |
| **notifications** | нагадування про повторний огляд і курс ліків | фоновий споживач черги, без HTTP |
| **gateway** (Traefik) | API Gateway, балансувальник | бере маршрути з Consul |
| **consul** | service discovery, health-checks | реєстр усіх інстансів |
| **rabbitmq** | message broker (topic exchange `medical.events`) | |
| **postgres** | окрема база для кожного сервісу | |

## Запуск

Потрібен Docker Desktop (Windows/macOS) або Docker + Compose (Linux).

```bash
docker compose up -d --build      # зібрати образи й підняти все
docker compose ps                 # перевірити, що все запущено
python demo.py                    # прогнати демонстраційний сценарій
```

Корисні адреси:

- API через шлюз: http://localhost:8000/api/patients
- Swagger кожного сервісу: http://localhost:8000/api/patients/docs, `/api/prescriptions/docs`, `/api/notes/docs`, `/api/audit/docs`
- Consul UI (реєстр сервісів): http://localhost:8500
- Traefik dashboard (маршрути і сервери): http://localhost:8080
- RabbitMQ UI: http://localhost:15672 (guest / guest)

## Горизонтальне масштабування

```bash
docker compose up -d --scale patients=3 --scale audit=2
python demo.py                    # пункт 5 покаже, як запити розійшлись по інстансах
```

Що відбувається:

1. Кожен новий контейнер при старті реєструється в Consul під тим самим ім'ям `patients` з власним IP.
2. Traefik кожні 5 с читає каталог Consul і додає новий інстанс у пул (видно в Traefik dashboard → HTTP Services → patients).
3. Зовнішні запити Traefik розподіляє round-robin (server-side балансування).
4. Внутрішні виклики (prescriptions → patients) беруть список здорових інстансів з Consul і теж перебирають їх по колу (client-side балансування).
5. Два інстанси `audit` читають одну чергу, і RabbitMQ віддає повідомлення по черзі (competing consumers): видно в `GET /api/audit/stats`.

Зменшити кількість: `docker compose up -d --scale patients=1`. Зупинений контейнер знімається з Consul, і Traefik перестає слати на нього запити.

## Демонстрація Circuit Breaker

```bash
docker compose stop patients
python demo.py circuit            # 5 запитів на створення призначення
docker compose start patients     # через ~15 с запобіжник знову замкнеться
```

Перші 3 запити отримують 503 «сервіс недоступний», після чого запобіжник переходить у стан OPEN. Наступні запити відхиляються миттєво, не чекаючи таймаутів. Через 15 с пропускається один пробний запит (HALF_OPEN), і якщо patients знову працює, стан стає CLOSED. Поточний стан: http://localhost:8000/api/prescriptions/circuit

## Корисні команди

```bash
docker compose logs -f notifications    # нагадування від споживача
docker compose logs -f audit            # обробка подій журналом
docker compose logs gateway             # access-лог шлюзу
docker compose down                     # зупинити (дані лишаються)
docker compose down -v                  # зупинити і видалити дані
```
