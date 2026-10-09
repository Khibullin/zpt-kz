"""Render cron: request at most two protected AI draft rewrites per day."""
import os
import urllib.error
import urllib.request


def main():
    token = os.getenv('EDITORIAL_CRON_TOKEN', '').strip()
    if len(token) < 32:
        raise RuntimeError('Editorial AI cron token missing')
    url = 'https://zpt-kz-backend.onrender.com/internal/editorial/ai/'
    for item in range(2):
        req = urllib.request.Request(
            url, method='POST', data=b'',
            headers={'Authorization': 'Bearer ' + token,
                     'User-Agent': 'ZPT-editorial-AI/1'},
        )
        try:
            with urllib.request.urlopen(req, timeout=27) as resp:
                if resp.status != 200:
                    raise RuntimeError('Unexpected editorial AI status')
                print(f'AI iteration {item + 1}: HTTP {resp.status}')
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'AI iteration {item + 1} failed: HTTP {exc.code}') from None


if __name__ == '__main__':
    main()
