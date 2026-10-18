FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY pg/ pg/
COPY data/ data/
COPY static/ static/
COPY experimental/ experimental/

# The cache lives on a volume so it survives restarts. Settings and the optional AI key come from
# the host's environment (never baked into the image).
ENV PORT=7001 PG_DB=/data/cache.sqlite3
RUN mkdir -p /data && chown nobody /data
VOLUME /data
EXPOSE 7001

USER nobody
CMD ["python", "server.py"]
