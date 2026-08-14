FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# Copy project
COPY . .

# Create data directories (for Railway volume or local dev)
RUN mkdir -p /data/athlete /data/workouts/plans /data/workouts/completed \
    /data/workouts/reflections /data/journals /data/overview /data/data

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]