import re
import json
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

URL = "https://www.dtek-krem.com.ua/ua/shutdowns"

# === ТВОЇ ПАРАМЕТРИ (як ти просив — без “уточнень”) ===
CITY   = "Шевченкове"
STREET = "Сонячна"
HOUSE  = "10"

# Пишемо результат у КОРІНЬ репозиторію (без public/)
OUTDIR = Path(".")

def minutes_to_hhmm(m: int) -> str:
    h = (m // 60) % 24
    mm = m % 60
    return f"{h:02d}:{mm:02d}"

def merge_ranges(ranges):
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [list(ranges[0])]
    for s, e in ranges[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]

def table_html_to_blackouts(table_html: str):
    soup = BeautifulSoup(table_html, "html.parser")

    # У таблиці перші 2 td — службові (colspan="2"), далі 24 клітинки годин
    tds = soup.select("tbody tr td")
    hour_cells = tds[2:2+24]

    if len(hour_cells) < 24:
        raise ValueError(f"Expected 24 hour cells, got {len(hour_cells)}")

    blackouts = []
    for hour, td in enumerate(hour_cells):
        cls = set(td.get("class", []))
        start = hour * 60

        if "cell-scheduled" in cls:
            # Світла немає всю годину
            blackouts.append((start, start + 60))
        elif "cell-first-half" in cls:
            # Світла немає перші 30 хв
            blackouts.append((start, start + 30))
        elif "cell-second-half" in cls:
            # Світла немає другі 30 хв
            blackouts.append((start + 30, start + 60))
        # cell-non-scheduled -> світло є

    return merge_ranges(blackouts)

def blackouts_to_text(blackouts):
    if not blackouts:
        return "Світло буде весь день."
    parts = [f"{minutes_to_hhmm(s)}–{minutes_to_hhmm(e)}" for s, e in blackouts]
    return "Світла не буде: " + ", ".join(parts)

def pick(page, label_text: str, value: str):
    """
    Стабільніша логіка вибору з автокомпліта:
    - клікаємо інпут біля label
    - вводимо value
    - пробуємо клікнути перший пункт, що містить value
    - якщо не вийшло — Enter (часто вибирає перший варіант)
    """
    page.get_by_text(label_text, exact=False).scroll_into_view_if_needed()
    block = page.get_by_text(label_text, exact=False).locator(
        "xpath=ancestor::*[self::div or self::label][1]"
    )
    inp = block.locator("input").first
    inp.click()
    inp.fill(value)

    # даємо списку підвантажитися
    page.wait_for_timeout(500)

    try:
        opt = page.locator("[role='option'], li, .option, .select__option, div").filter(
            has_text=re.compile(re.escape(value), re.I)
        ).first
        if opt.count() > 0:
            opt.click()
            return
    except Exception:
        pass

    # fallback: Enter (часто підхоплює перший результат)
    inp.press("Enter")

def write_outputs(final_text: str, status: dict):
    (OUTDIR / "result.txt").write_text(final_text, encoding="utf-8")
    (OUTDIR / "result.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def main():
    status = {
        "ok": False,
        "when_utc": datetime.now(timezone.utc).isoformat(),
        "tomorrow_tab_found": False,
        "tomorrow_date": None,
        "text": None,
        "error": None,
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})

            page.goto(URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            pick(page, "Введіть нас. пункт", CITY)
            page.wait_for_timeout(700)

            pick(page, "Введіть вулицю", STREET)
            page.wait_for_timeout(700)

            pick(page, "Номер будинку", HOUSE)
            page.wait_for_timeout(1200)

            tab = page.locator("div.date").filter(
                has_text=re.compile(r"\bна завтра\b", re.I)
            ).first

            if tab.count() == 0:
                raise RuntimeError("Tomorrow tab not found")

            status["tomorrow_tab_found"] = True

            d = tab.locator("span[rel='date']").first
            if d.count():
                status["tomorrow_date"] = d.inner_text().strip()

            tab.click()
            page.wait_for_timeout(1200)

            header = page.get_by_text("Графік відключень", exact=False).first
            section = header.locator("xpath=ancestor::section[1]").first
            table = section.locator("table").first

            table_html = table.evaluate("el => el.outerHTML")

            blackouts = table_html_to_blackouts(table_html)
            text = blackouts_to_text(blackouts)

            prefix = "Графік на завтра"
            if status["tomorrow_date"]:
                prefix += f" ({status['tomorrow_date']})"

            final = f"{prefix}: {text}"

            status["ok"] = True
            status["text"] = final

            write_outputs(final, status)
            browser.close()

    except Exception as e:
        status["error"] = str(e)
        fallback = "Графік на завтра: дані недоступні / перевір пізніше."
        status["text"] = fallback
        write_outputs(fallback, status)

if __name__ == "__main__":
    main()
