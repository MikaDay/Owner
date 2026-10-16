FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/data
CMD gunicorn web_app:app --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 300
