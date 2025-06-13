import os
import asyncio
import aiohttp

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/115.0.0.0 Safari/537.36'
)

def find_chrome_executable():
    """Return path to Chrome executable or None if not found."""
    possible_paths = [
        r'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        r'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe'),
        '/usr/bin/google-chrome',
        '/usr/local/bin/google-chrome',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None


async def solve_turnstile(api_key: str, params: dict, *, max_retries: int = 10, delay: int = 5) -> str:
    """Solve a Cloudflare Turnstile captcha using the 2Captcha API asynchronously."""
    payload = {
        "key": api_key,
        "method": "turnstile",
        "sitekey": params.get("sitekey"),
        "pageurl": params.get("pageurl"),
        "data": params.get("data"),
        "pagedata": params.get("pagedata"),
        "action": params.get("action"),
        "useragent": params.get("userAgent"),
        "json": 1,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://2captcha.com/in.php", data=payload) as resp:
            result = await resp.json()
            captcha_id = result.get("request")

        for _ in range(max_retries):
            await asyncio.sleep(delay)
            async with session.get(
                f"https://2captcha.com/res.php?key={api_key}&action=get&json=1&id={captcha_id}"
            ) as r:
                data = await r.json()
                request_val = data.get("request")
                if request_val == "CAPCHA_NOT_READY":
                    continue
                if "ERROR" in request_val:
                    raise RuntimeError(f"2Captcha error: {request_val}")
                return request_val

    raise RuntimeError("Failed to obtain captcha solution from 2Captcha")
