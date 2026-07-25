# Only needed if you run Echo itself in Docker (Option B).
# If Python runs on your host, you can ignore this file.
FROM python:3.11-slim

WORKDIR /app

# install deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code
COPY src/ ./src/
COPY run.py .

# db lives in a mounted volume so it survives container restarts
ENV ECHO_DB_PATH=/app/data/echo.db

CMD ["python", "run.py", "--demo"]