FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVICE_NAME=notifications

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY services/notifications/worker.py ./worker.py

RUN useradd --create-home appuser
USER appuser

# HTTP немає — це фоновий споживач черги
CMD ["python", "worker.py"]
