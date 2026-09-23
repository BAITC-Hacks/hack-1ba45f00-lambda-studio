# Грибница в контейнере: docker compose up --build  →  http://localhost:8000
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python -m mycelium.pipeline
EXPOSE 8000
CMD ["python", "-m", "mycelium.serve", "--host", "0.0.0.0", "--port", "8000"]
