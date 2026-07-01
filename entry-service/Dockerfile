FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY entry_service.py .

# Participant mapping (email/password only -- chat identity is discovered
# live from Open WebUI's own /api/v1/chats/list, see entry_service.py).
VOLUME ["/data"]
ENV DB_PATH=/data/participants.sqlite3

EXPOSE 9000

CMD ["uvicorn", "entry_service:app", "--host", "0.0.0.0", "--port", "9000"]
