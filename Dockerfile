FROM python:3.12-slim
WORKDIR /srv
COPY requirements.lock .
COPY upstream/OmniRank /opt/OmniRank
RUN pip install --no-cache-dir -r requirements.lock -e /opt/OmniRank/scripts/py
COPY app.py fusion.py auth_local.py georank_bridge.py network_audit.py ./
COPY vendor ./vendor
COPY frontend ./frontend
RUN mkdir -p /srv/data && chown -R 65534:65534 /srv/data
USER 65534:65534
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health')"
CMD ["uvicorn", "fusion:app", "--host", "0.0.0.0", "--port", "8090"]
