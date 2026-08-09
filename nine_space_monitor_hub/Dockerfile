FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache python3 py3-pip tzdata \
    && python3 -m venv /opt/venv

COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app/nine_space_monitor_hub
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONPATH=/app

CMD ["/run.sh"]
