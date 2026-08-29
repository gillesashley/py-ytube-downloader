FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY app/ .

EXPOSE 8000

# Single worker: background download threads live in the worker process, and
# the status poll must always reach them. Add a DB-claim worker process before
# scaling workers.
CMD ["sh", "-c", "python manage.py migrate && python manage.py ensure_admin && exec gunicorn config.wsgi:application -b 0.0.0.0:8000 -w 1 --threads 4"]
