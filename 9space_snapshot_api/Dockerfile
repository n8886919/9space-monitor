FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache ffmpeg python3 py3-pip tzdata \
    && cp /usr/share/zoneinfo/Asia/Taipei /etc/localtime \
    && echo "Asia/Taipei" > /etc/timezone

ENV TZ=Asia/Taipei

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir fastapi uvicorn

COPY main.py /app/main.py
COPY background.py /app/background.py
COPY channel_state.py /app/channel_state.py
COPY live_probe.py /app/live_probe.py
COPY recording_query.py /app/recording_query.py
COPY telemetry.py /app/telemetry.py
COPY log_config.json /app/log_config.json
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENV PATH="/opt/venv/bin:${PATH}"

CMD ["/run.sh"]
