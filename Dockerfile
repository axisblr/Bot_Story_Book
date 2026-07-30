FROM python:3.12-slim

WORKDIR /app

# Ставим зависимости отдельным слоем — пересобирается только при их изменении
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py core.py sqlite_storage.py ./

# Данные (БД статистики, состояние анкет, Google-креды, временные фото)
# живут в томе, а не в образе — иначе теряются при каждом обновлении.
ENV DATA_DIR=/app/data
VOLUME ["/app/data"]

CMD ["python", "-u", "main.py"]
