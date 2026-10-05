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

## Docker Hub 镜像

项目默认使用已经发布的 Docker Hub 镜像：

```text
circling0635/euserv-renew:2026.10.02
```

镜像只包含程序代码，不包含账号、密码、Mailparser 地址、Telegram Token
或 challenge-gateway key。所有敏感配置都在运行时通过 `private.env` 注入。

## 配置

先创建私有配置：

```bash
cp config/private.env.example config/private.env
chmod 600 config/private.env
```

至少需要填写：

```env
USERNAME=your_euserv_email@example.com
PASSWORD=replace_with_euserv_password
MAILPARSER_DOWNLOAD_URL=https://files.mailparser.io/d/replace_me
CAPTCHA_PROVIDER=challenge-gateway
CHALLENGE_GATEWAY_URL=https://challenge.cool.pp.ua
CHALLENGE_GATEWAY_KEY=replace_with_shared_gateway_key
```

`CHALLENGE_GATEWAY_KEY` 必须和 challenge-gateway 服务器上的 `CLIENTT_KEY`
完全一致。Telegram 配置是可选的；多个账号和密码使用空格分隔，并保持数量对应。

注意：Docker Compose 会解析 `env_file` 中未加引号的 `$`。如果 EUserv 密码
包含 `$`，请把整个密码值用单引号包起来，例如：

```env
PASSWORD='your_password_with_$ characters'
```

单引号可以保留密码中的 `$`；不要把真实的 `private.env` 上传到 GitHub 或 Docker Hub。

## Run

```bash
docker compose pull
docker compose up -d
docker compose logs -f euserv-renew
```

The container runs once on startup and then repeats every 86400 seconds. The
default CAPTCHA provider is the self-hosted gateway configured by
`CHALLENGE_GATEWAY_URL` and `CHALLENGE_GATEWAY_KEY`.

查看状态：

```bash
docker compose ps
docker compose logs --tail 200 euserv-renew
```

更新镜像：

```bash
docker compose pull
docker compose up -d
```

回滚时，把 `docker-compose.yaml` 的镜像版本改回之前的日期标签，再执行同样的
`pull` 和 `up -d`。不要删除 `private.env`。

## CAPTCHA flow

The job downloads the CAPTCHA with the current EUserv `PHPSESSID`, sends the
image to the gateway using the CapSolver-compatible `ImageToTextTask` API, and
polls `/getTaskResult` until the OCR result is ready. The session cookie is
kept for the subsequent EUserv login submission.

验证码处理是异步的：先调用 `/createTask` 获取 `taskId`，再轮询
`/getTaskResult`。这也是为什么不能只把 `CHALLENGE_GATEWAY_URL` 改成一个地址，
客户端还必须使用正确的 key 和轮询流程。

Never upload EUserv passwords, Mailparser URLs containing private access data,
Telegram tokens, gateway keys, or SSH keys.
