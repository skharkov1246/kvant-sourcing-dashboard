# Бэкенд «КВАНТ Скан». Один образ на всё: API, воркеры, поллер Bitrix —
# различаются командой запуска, а не сборкой (одинаковый код в проде и в
# воркерах — обязательное условие воспроизводимости приёмки).
# Зеркало ECR вместо docker.io: анонимные лимиты Docker Hub (429) уже
# срывали автообновление на сервере.
FROM public.ecr.aws/docker/library/python:3.11-slim

# libGL/libglib нужны OpenCV (масштаб по опорному маркеру, §05.6);
# без них import cv2 падает на чистом slim-образе.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "anthropic>=0.40" boto3 opencv-python-headless

COPY . .

# Порт внутренний: наружу смотрит Caddy с TLS, приложение HTTPS не знает.
EXPOSE 8000

# 2 воркера на 2 vCPU: приёмка ходит в модель по 6 секунд, один процесс
# на это время занят целиком.
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
