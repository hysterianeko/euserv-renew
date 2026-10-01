# EUserv Auto Renew

Dockerized EUserv renewal job with session-safe CAPTCHA fetching and a
self-hosted `challenge-gateway` OCR provider.

## Configuration

Create the private configuration file from the example:

```bash
cp config/private.env.example config/private.env
```

Fill in the EUserv credentials, Mailparser target, notification settings, and
the shared `CHALLENGE_GATEWAY_KEY`. Do not commit `config/private.env`.

## Run

```bash
docker compose up -d --build
docker compose logs -f euserv-renew
```

The container runs once on startup and then repeats every 86400 seconds. The
default CAPTCHA provider is the self-hosted gateway configured by
`CHALLENGE_GATEWAY_URL` and `CHALLENGE_GATEWAY_KEY`.

## CAPTCHA flow

The job downloads the CAPTCHA with the current EUserv `PHPSESSID`, sends the
image to the gateway using the CapSolver-compatible `ImageToTextTask` API, and
polls `/getTaskResult` until the OCR result is ready. The session cookie is
kept for the subsequent EUserv login submission.

Never upload EUserv passwords, Mailparser URLs containing private access data,
Telegram tokens, gateway keys, or SSH keys.
