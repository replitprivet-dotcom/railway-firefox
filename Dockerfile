FROM lscr.io/linuxserver/firefox:latest

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv nginx ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/railway-firefox
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY nginx.conf.template /opt/railway-firefox/nginx.conf.template
COPY start.sh /opt/railway-firefox/start.sh
RUN chmod +x /opt/railway-firefox/start.sh \
    && mkdir -p /data /run/nginx

# Keep the LinuxServer image's /init entrypoint so Firefox and its s6 services start normally.
COPY custom-cont-init.d/10-railway-data /etc/cont-init.d/10-railway-data
COPY custom-cont-init.d/30-railway-gateway /etc/cont-init.d/30-railway-gateway
RUN chmod +x /etc/cont-init.d/10-railway-data /etc/cont-init.d/30-railway-gateway

ENV API_PORT=5000
EXPOSE 3000
