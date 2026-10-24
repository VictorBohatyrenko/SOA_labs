# Офіційний легкий образ Python
FROM python:3.12-slim

# Не створювати .pyc і одразу виводити логи
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVICE_NAME=patients \
    SERVICE_PORT=8000

WORKDIR /app

# Спершу залежності — цей шар кешується, поки не зміниться requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Потім код (змінюється часто)
COPY common ./common
COPY services/patients/app.py ./app.py

# Запуск не від root
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
