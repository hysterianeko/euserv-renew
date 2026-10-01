FROM ghcr.io/hysterianeko/eu-ex-docker:latest

COPY main_override.py /app/main_override.py
COPY entrypoint.override.sh /usr/local/bin/entrypoint.sh

USER root
RUN chmod 0555 /usr/local/bin/entrypoint.sh /app/main_override.py
USER app
