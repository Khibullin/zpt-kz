"""Small Render cron client: no database connection or API keys except bridge token."""
import os
import urllib.error
import urllib.request


def main():
    token = os.environ.get("EDITORIAL_CRON_TOKEN", "").strip()
    if len(token) < 32:
        raise RuntimeError("EDITORIAL_CRON_TOKEN not configured")
    target = "https://zpt-kz-backend.onrender.com/internal/editorial/daily/"
    req = urllib.request.Request(
        target, method="POST",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "ZPT-editorial-cron/1"},
        data=b"",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            print(f"Editorial cron HTTP {response.status}")
            if response.status != 200:
                raise RuntimeError("Editorial cron did not complete")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Editorial cron failed HTTP {exc.code}") from None


if __name__ == "__main__":
    main()
