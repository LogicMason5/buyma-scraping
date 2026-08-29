"""Probe whether ChatGPT cookies work for HTTP session without Chrome."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    raw = json.loads((ROOT / "secrets" / "chatgpt_cookies.json").read_text(encoding="utf-8"))
    cookies = raw["cookies"] if isinstance(raw, dict) else raw
    for c in cookies:
        name = c.get("name")
        if name in {
            "cf_clearance",
            "__Secure-next-auth.session-token",
            "__cf_bm",
            "_cfuvid",
            "oai-did",
        }:
            print(
                name,
                "domain=",
                c.get("domain"),
                "expires=",
                c.get("expires"),
                "val_len=",
                len(str(c.get("value"))),
            )

    jar = httpx.Cookies()
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        domain = (c.get("domain") or "chatgpt.com").lstrip(".")
        path = c.get("path") or "/"
        try:
            jar.set(name, str(value), domain=domain, path=path)
        except Exception as exc:  # noqa: BLE001
            print("skip", name, exc)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Referer": "https://chatgpt.com/",
        "Origin": "https://chatgpt.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    with httpx.Client(cookies=jar, headers=headers, timeout=45.0, follow_redirects=True) as client:
        for url in ("https://chatgpt.com/", "https://chatgpt.com/api/auth/session"):
            r = client.get(url)
            print(
                url,
                r.status_code,
                r.headers.get("content-type"),
                "len",
                len(r.content),
                "cf-ray",
                r.headers.get("cf-ray"),
            )
            if "session" in url:
                print(r.text[:400])
                if r.status_code == 200:
                    data = r.json()
                    print("has_accessToken", bool(data.get("accessToken")))


if __name__ == "__main__":
    main()
