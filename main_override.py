#!/usr/bin/env python3

"""Run the bundled EUserv job with a cache-safe CAPTCHA fetch.

The upstream image fetches securimage_show.php through a fixed URL without
request headers. A cached image can then be submitted against a different
PHP session, which makes every solver answer fail even when the OCR result is
otherwise plausible.
"""

import base64
import os
import sys
import time
from urllib.parse import urlencode

import main


CHALLENGE_GATEWAY_URL = os.environ.get(
    "CHALLENGE_GATEWAY_URL", "https://challenge.cool.pp.ua"
).rstrip("/")
CHALLENGE_GATEWAY_KEY = os.environ.get("CHALLENGE_GATEWAY_KEY", "")
CHALLENGE_GATEWAY_POLL_INTERVAL = float(
    os.environ.get("CHALLENGE_GATEWAY_POLL_INTERVAL", "1")
)
CHALLENGE_GATEWAY_MAX_POLLS = int(
    os.environ.get("CHALLENGE_GATEWAY_MAX_POLLS", "90")
)


def captcha_solver(captcha_image_url: str, session):
    query = [("_", str(time.time_ns()))]
    sess_id = session.cookies.get("PHPSESSID")
    if sess_id:
        query.insert(0, ("sess_id", sess_id))
    separator = "&" if "?" in captcha_image_url else "?"
    cache_busted_url = f"{captcha_image_url}{separator}{urlencode(query)}"
    headers = {
        "User-Agent": main.user_agent,
        "Referer": "https://support.euserv.com/index.iphp",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
    }
    response = session.get(cache_busted_url, headers=headers, timeout=30)
    response.raise_for_status()
    if not response.content:
        raise ValueError("EUserv returned an empty CAPTCHA image.")

    encoded_image = base64.b64encode(response.content).decode()
    provider = main.get_captcha_provider()
    if provider == "capsolver":
        return main.capsolver_solver(encoded_image)
    if provider in {"challenge", "challenge-gateway", "challenge_gateway", "gateway"}:
        return challenge_gateway_solver(encoded_image)
    if provider == "truecaptcha":
        return main.truecaptcha_solver(encoded_image)
    raise ValueError(f"Unsupported captcha provider: {provider}")


def challenge_gateway_solver(encoded_image: str) -> dict:
    """Solve an ImageToTextTask through the self-hosted CapSolver-compatible API."""
    if not CHALLENGE_GATEWAY_KEY:
        raise ValueError("CHALLENGE_GATEWAY_KEY is not configured.")

    create_response = main.requests.post(
        f"{CHALLENGE_GATEWAY_URL}/createTask",
        json={
            "clientKey": CHALLENGE_GATEWAY_KEY,
            "task": {
                "type": "ImageToTextTask",
                "module": "common",
                "websiteURL": "https://support.euserv.com",
                "body": encoded_image,
            },
        },
        timeout=30,
    )
    create_response.raise_for_status()
    created = create_response.json()
    if created.get("errorId", 0) != 0:
        raise ValueError(
            created.get("errorDescription")
            or created.get("errorCode")
            or "Challenge gateway task creation failed."
        )
    task_id = str(created.get("taskId") or "")
    if not task_id:
        raise ValueError("Challenge gateway returned no taskId.")

    for _ in range(CHALLENGE_GATEWAY_MAX_POLLS):
        result_response = main.requests.post(
            f"{CHALLENGE_GATEWAY_URL}/getTaskResult",
            json={"clientKey": CHALLENGE_GATEWAY_KEY, "taskId": task_id},
            timeout=30,
        )
        result_response.raise_for_status()
        result = result_response.json()
        if result.get("errorId", 0) != 0:
            raise ValueError(
                result.get("errorDescription")
                or result.get("errorCode")
                or "Challenge gateway task failed."
            )
        status = str(result.get("status") or "").lower()
        if status == "ready":
            solution = result.get("solution") or {}
            text = str(solution.get("text") or "").strip()
            if not text:
                raise ValueError("Challenge gateway returned an empty OCR result.")
            return {"errorId": 0, "taskId": task_id, "solution": {"text": text}}
        if status in {"failed", "error"}:
            raise ValueError("Challenge gateway OCR task failed.")
        time.sleep(CHALLENGE_GATEWAY_POLL_INTERVAL)

    raise TimeoutError("Challenge gateway OCR task timed out.")


def run_job():
    # main.login is decorated in the bundled module. Its original function
    # resolves captcha_solver from main's globals, so replace it before use.
    main.captcha_solver = captcha_solver

    if not main.USERNAME or not main.PASSWORD:
        main.log("[EUserv] 你没有添加任何账户")
        return 1

    user_list = main.USERNAME.strip().split()
    passwd_list = main.PASSWORD.strip().split()
    mailparser_targets = main.MAILPARSER_DOWNLOAD_URL.strip().split()
    if len(user_list) != len(passwd_list):
        main.log("[EUserv] The number of usernames and passwords do not match!")
        return 1
    if len(mailparser_targets) != len(user_list):
        main.log(
            "[Mailparser] The number of mailparser download targets and usernames do not match!"
        )
        return 1

    for index, (username, password) in enumerate(zip(user_list, passwd_list), 1):
        print("*" * 30)
        main.log(f"[EUserv] 正在续费第 {index} 个账号")
        sess_id, session = main.login(username, password)
        if sess_id == "-1":
            main.log(f"[EUserv] 第 {index} 个账号登陆失败，请检查登录信息")
            continue

        servers = main.get_servers(sess_id, session)
        main.log(
            f"[EUserv] 检测到第 {index} 个账号有 {len(servers)} 台 VPS，正在尝试续期"
        )
        for order_id, needs_renewal in servers.items():
            if not needs_renewal:
                main.log(f"[EUserv] ServerID: {order_id} does not need to be renewed")
                continue
            renewed = main.renew(
                sess_id,
                session,
                password,
                order_id,
                mailparser_targets[index - 1],
            )
            if renewed:
                main.log(
                    f"[EUserv] ServerID: {order_id} has been successfully renewed!"
                )
            else:
                main.log(f"[EUserv] ServerID: {order_id} Renew Error!")
        time.sleep(15)
        main.check(sess_id, session)
        time.sleep(5)

    if main.TG_BOT_TOKEN and main.TG_USER_ID and main.TG_API_HOST:
        main.telegram()
    if main.RECEIVER_EMAIL and main.YD_EMAIL and main.YD_APP_PWD:
        main.email()
    print("*" * 30)
    return 0


if __name__ == "__main__":
    sys.exit(run_job())
