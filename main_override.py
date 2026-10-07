#!/usr/bin/env python3

"""Run the bundled EUserv job with a cache-safe CAPTCHA fetch.

The upstream image fetches securimage_show.php through a fixed URL without
request headers. A cached image can then be submitted against a different
PHP session, which makes every solver answer fail even when the OCR result is
otherwise plausible.
"""

import base64
import io
import os
import re
import sys
import time
from urllib.parse import urlencode

import main

try:
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover - the Docker image installs Pillow.
    Image = None
    ImageFilter = None


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
CAPTCHA_PREPROCESS_ENABLED = (
    os.environ.get("CAPTCHA_PREPROCESS_ENABLED", "true").strip().lower() == "true"
)
CAPTCHA_MAX_CANDIDATES = max(
    1, int(os.environ.get("CAPTCHA_MAX_CANDIDATES", "4"))
)


def fetch_captcha(captcha_image_url: str, session, sess_id: str | None = None) -> bytes:
    """Fetch the image bound to the same EUserv session used for submission."""
    current_sess_id = sess_id or session.cookies.get("PHPSESSID")
    query = [("_", str(time.time_ns()))]
    if current_sess_id:
        query.insert(0, ("sess_id", current_sess_id))
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
    return response.content


def preprocess_captcha(image_bytes: bytes) -> bytes:
    """Remove small orange specks before a second OCR attempt.

    EUserv uses orange text and draws thin orange interference over it. A
    small morphological opening keeps the thicker glyphs while removing much
    of the dot noise. The original image remains the first candidate.
    """
    if Image is None or ImageFilter is None:
        raise RuntimeError("Pillow is required for CAPTCHA preprocessing.")

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    pixels = []
    for red, green, blue in image.getdata():
        is_orange = red - green > 45 and red - blue > 45 and green < 220
        pixels.append(0 if is_orange else 255)

    mask = Image.new("L", image.size)
    mask.putdata(pixels)
    opened = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    width, height = opened.size
    top = max(0, round(height * 0.14))
    bottom = min(height, round(height * 0.82))
    cropped = opened.crop((0, top, width, bottom))
    scaled = cropped.resize((width * 4, (bottom - top) * 4), Image.Resampling.NEAREST)

    output = io.BytesIO()
    scaled.save(output, format="PNG")
    return output.getvalue()


def solve_encoded_image(encoded_image: str, provider: str) -> dict:
    if provider == "capsolver":
        return main.capsolver_solver(encoded_image)
    if provider in {"challenge", "challenge-gateway", "challenge_gateway", "gateway"}:
        return challenge_gateway_solver(encoded_image)
    if provider == "truecaptcha":
        return main.truecaptcha_solver(encoded_image)
    raise ValueError(f"Unsupported captcha provider: {provider}")


def result_to_code(solved: dict) -> str:
    return result_to_candidates(solved)[0]


def result_to_candidates(solved: dict) -> list[str]:
    if solved.get("errorId", 0) != 0:
        raise ValueError(
            solved.get("errorDescription") or solved.get("errorCode") or "CAPTCHA solver failed."
        )

    solution = solved.get("solution") or {}
    raw_values = [solution.get("text")]
    alternatives = solution.get("alternatives") or []
    if isinstance(alternatives, list):
        raw_values.extend(alternatives)

    candidates = []
    for raw_value in raw_values:
        text = re.sub(r"\s+", "", str(raw_value or "")).strip()
        if not text:
            continue
        code = str(main.normalize_captcha_text(text))
        if code not in candidates:
            candidates.append(code)

    if not candidates:
        raise ValueError("CAPTCHA solver returned an empty result.")
    return candidates


def captcha_candidates(captcha_image_url: str, session, sess_id: str):
    """Return the original OCR result plus one noise-reduced fallback."""
    image_bytes = fetch_captcha(captcha_image_url, session, sess_id)
    provider = main.get_captcha_provider()
    encoded_image = base64.b64encode(image_bytes).decode()
    candidates = []

    solved = solve_encoded_image(encoded_image, provider)
    for index, code in enumerate(result_to_candidates(solved)):
        if len(candidates) >= CAPTCHA_MAX_CANDIDATES:
            break
        label = provider if index == 0 else f"{provider}+alternative-{index}"
        candidates.append((label, code))

    if (
        CAPTCHA_PREPROCESS_ENABLED
        and provider in {"challenge", "challenge-gateway", "challenge_gateway", "gateway"}
        and len(candidates) < CAPTCHA_MAX_CANDIDATES
    ):
        processed = preprocess_captcha(image_bytes)
        processed_result = solve_encoded_image(
            base64.b64encode(processed).decode(), provider
        )
        for index, processed_code in enumerate(result_to_candidates(processed_result)):
            if len(candidates) >= CAPTCHA_MAX_CANDIDATES:
                break
            if processed_code in {code for _, code in candidates}:
                continue
            suffix = "preprocessed" if index == 0 else f"preprocessed-{index}"
            candidates.append((f"{provider}+{suffix}", processed_code))

    return candidates


def captcha_solver(captcha_image_url: str, session):
    """Keep the upstream-compatible single-result solver available."""
    image_bytes = fetch_captcha(captcha_image_url, session)
    provider = main.get_captcha_provider()
    return solve_encoded_image(base64.b64encode(image_bytes).decode(), provider)


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
            alternatives = solution.get("alternatives") or []
            if not isinstance(alternatives, list):
                alternatives = []
            return {
                "errorId": 0,
                "taskId": task_id,
                "solution": {
                    "text": text,
                    "alternatives": [str(value) for value in alternatives if value],
                },
            }
        if status in {"failed", "error"}:
            raise ValueError("Challenge gateway OCR task failed.")
        time.sleep(CHALLENGE_GATEWAY_POLL_INTERVAL)

    raise TimeoutError("Challenge gateway OCR task timed out.")


def login_once(username: str, password: str):
    """Attempt one login and try both OCR candidates on the same session."""
    headers = {"user-agent": main.user_agent, "origin": "https://www.euserv.com"}
    url = "https://support.euserv.com/index.iphp"
    captcha_image_url = "https://support.euserv.com/securimage_show.php"
    session = main.requests.Session()

    initial = session.get(url, headers=headers, timeout=30)
    initial.raise_for_status()
    sess_match = re.findall(r"PHPSESSID=(\w{10,100});", str(initial.headers))
    sess_id = sess_match[0] if sess_match else session.cookies.get("PHPSESSID")
    if not sess_id:
        raise ValueError("EUserv did not return a PHP session id.")

    session.get(
        "https://support.euserv.com/pic/logo_small.png",
        headers=headers,
        timeout=30,
    )
    first = session.post(
        url,
        headers=headers,
        data={
            "email": username,
            "password": password,
            "form_selected_language": "en",
            "Submit": "Login",
            "subaction": "login",
            "sess_id": sess_id,
        },
        timeout=30,
    )
    first.raise_for_status()

    success_markers = (
        "Hello" in first.text
        or "Confirm or change your customer data here" in first.text
    )
    captcha_marker = "To finish the login process please solve the following captcha."
    if success_markers:
        return sess_id, session
    if captcha_marker not in first.text:
        return "-1", session

    main.log("[Captcha Solver] 进行验证码识别，provider=challenge-gateway")
    candidates = captcha_candidates(captcha_image_url, session, sess_id)
    for candidate_provider, captcha_code in candidates:
        main.log(
            "[Captcha Solver] provider={} candidate={}".format(
                candidate_provider, captcha_code
            )
        )
        second = session.post(
            url,
            headers=headers,
            data={
                "subaction": "login",
                "sess_id": sess_id,
                "captcha_code": captcha_code,
            },
            timeout=30,
        )
        second.raise_for_status()
        if captcha_marker in second.text:
            main.log(
                "[Captcha Solver] candidate rejected, trying the next candidate"
            )
            continue
        if (
            "Hello" in second.text
            or "Confirm or change your customer data here" in second.text
        ):
            main.log("[Captcha Solver] 验证通过")
            return sess_id, session

        main.log("[EUserv] CAPTCHA passed but the login response was not successful")
        return "-1", session

    main.log("[Captcha Solver] 所有候选验证码均验证失败")
    return "-1", session


def login_with_retry(username: str, password: str):
    """Preserve the upstream retry count without the misleading provider log."""
    max_retry = max(0, int(main.LOGIN_MAX_RETRY_COUNT))
    for attempt in range(max_retry + 1):
        if attempt:
            main.log("[EUserv] Login tried the {}th time".format(attempt + 1))
        try:
            sess_id, session = login_once(username, password)
        except Exception as exc:
            main.log("[EUserv] Login attempt failed: {}".format(exc))
            sess_id, session = "-1", None
        if sess_id != "-1":
            return sess_id, session
    return "-1", session


def run_job():
    # Use the session-aware login above instead of the bundled decorated login.
    main.captcha_solver = captcha_solver
    main.login = login_with_retry

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
