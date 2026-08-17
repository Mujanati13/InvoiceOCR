FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py celery_worker.py ./

RUN mkdir -p /app/uploads/original /app/exports

EXPOSE 5000

CMD ["python", "run.py"]
