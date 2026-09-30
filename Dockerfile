FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
RUN useradd -m -u 1000 appuser && mkdir -p /data && chown -R appuser:appuser /app /data
COPY --from=builder /root/.local /home/appuser/.local
COPY ./app ./app
ENV PATH=/home/appuser/.local/bin:$PATH PYTHONUNBUFFERED=1 CREDENTIALS_PATH=/data/credentials.json
USER appuser
EXPOSE 8000
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
