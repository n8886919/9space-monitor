FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache python3 tzdata \
    && cp /usr/share/zoneinfo/Asia/Taipei /etc/localtime \
    && echo "Asia/Taipei" > /etc/timezone

ENV TZ=Asia/Taipei

COPY app.py /app/app.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
