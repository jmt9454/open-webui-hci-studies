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

# Pure Python check (no curl in the slim base image) against the liveness-
# only /health endpoint -- see entry_service.py for why it's separate from
# /enter's own, expected-to-sometimes-503 status.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request as u; u.urlopen('http://localhost:9000/health', timeout=2)" || exit 1

CMD ["uvicorn", "entry_service:app", "--host", "0.0.0.0", "--port", "9000"]
