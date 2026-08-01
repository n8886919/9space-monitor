FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache ffmpeg python3 py3-pip tzdata \
    && cp /usr/share/zoneinfo/Asia/Taipei /etc/localtime \
    && echo "Asia/Taipei" > /etc/timezone

ENV TZ=Asia/Taipei

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir fastapi uvicorn

COPY main.py /app/main.py
COPY log_config.json /app/log_config.json
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENV PATH="/opt/venv/bin:${PATH}"

CMD ["/run.sh"]
