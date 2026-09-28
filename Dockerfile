FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DELEGATE_DATABASE=/data/calls.sqlite3 DELEGATE_PUBLIC_MODE=true
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 1000 delegate \
    && useradd --uid 1000 --gid delegate --no-create-home delegate \
    && mkdir /data && chown delegate:delegate /data
COPY *.py ./
COPY frontend ./frontend
# Match hosts that run containers as UID/GID 1000.
USER delegate
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import json,os,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.getenv('PORT','8000'), timeout=3)); assert data['public_access_ready']"
# Hosting platforms such as Railway choose the port through $PORT; one worker keeps live-call state in one process.
CMD ["sh", "-c", "exec python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
