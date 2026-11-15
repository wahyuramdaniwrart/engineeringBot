"""Set bot.public_url via dashboard, lalu sinkronkan Sheets."""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser

import requests

def _env_value(key: str) -> str:
    with open(".env", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} tidak ada di .env")


BASE = os.environ.get("DASH_URL", "http://192.168.54.90:8080")
PASSWORD = os.environ.get("DASH_PASSWORD") or _env_value("BOT_DASHBOARD_PASSWORD")
NEW_URL = os.environ.get("NEW_URL", "http://192.168.54.90:8080")


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.fields: list[tuple[str, str]] = []
        self._select: str | None = None
        self._select_chosen: list[str] = []
        self._select_current: str | None = None
        self._textarea: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            if a.get("action") == "/settings":
                self.in_form = True
            return
        if not self.in_form:
            return
        if tag == "input":
            name = a.get("name")
            if not name:
                return
            itype = (a.get("type") or "text").lower()
            if itype in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields.append((name, a.get("value", "on")))
            else:
                self.fields.append((name, a.get("value", "")))
        elif tag == "select":
            self._select = a.get("name")
            self._select_chosen = []
            self._select_current = None
        elif tag == "option":
            if self._select is not None:
                self._select_current = a.get("value", "")
                if "selected" in a:
                    self._select_chosen.append(self._select_current)
        elif tag == "textarea":
            self._textarea = a.get("name")
            self._textarea_buf = ""

    def handle_endtag(self, tag):
        if not self.in_form:
            return
        if tag == "select" and self._select:
            if not self._select_chosen and self._select_current is not None:
                self._select_chosen.append(self._select_current)
            for v in self._select_chosen:
                self.fields.append((self._select, v))
            self._select = None
        elif tag == "textarea" and self._textarea:
            self.fields.append((self._textarea, self._textarea_buf))
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self._textarea_buf = getattr(self, "_textarea_buf", "") + data


def main() -> None:
    s = requests.Session()
    r = s.post(f"{BASE}/login", data={"password": PASSWORD}, allow_redirects=True)
    print("login:", r.status_code, r.url)

    r = s.get(f"{BASE}/settings")
    print("settings:", r.status_code)
    p = FormParser()
    p.feed(r.text)
    fields = [(n, v) for n, v in p.fields if n != "bot_public_url"]
    fields.append(("bot_public_url", NEW_URL))
    print("jumlah field:", len(fields))
    print("nama field:", sorted({n for n, _ in fields}))

    r = s.post(f"{BASE}/settings", data=fields, allow_redirects=False)
    print("simpan:", r.status_code, r.headers.get("location"))

    r = s.get(f"{BASE}/settings")
    m = re.search(r'name="bot_public_url"\s+value="([^"]*)"', r.text)
    print("bot.public_url sekarang:", m.group(1) if m else "TIDAK DITEMUKAN")

    r = s.post(f"{BASE}/api/sheets/sync")
    print("sinkron sheets:", r.status_code, r.text[:300])


if __name__ == "__main__":
    main()
