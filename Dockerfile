# Use the last public runtime image as the base so builds do not require the
# private GHCR package or a separate packages:read token.
FROM circling0635/euserv-renew:2026.10.08

USER root
RUN pip install --no-cache-dir Pillow
COPY main_override.py /app/main_override.py
COPY entrypoint.override.sh /usr/local/bin/entrypoint.sh

RUN chmod 0555 /usr/local/bin/entrypoint.sh /app/main_override.py
USER app
