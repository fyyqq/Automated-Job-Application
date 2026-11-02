import random
import re
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

PAGE_DELAY_SECONDS = (8, 15)
MAX_DESCRIPTION_CHARS = 3000


# On a public job page the Apply button of an outside-site job carries "apply-link-offsite" in its markup.
OFFSITE_MARKER = re.compile(r"apply-link-offsite|offsite-apply-icon")
ONSITE_MARKER = re.compile(r"apply-link-onsite")
EASY_APPLY_TEXT = re.compile(r"easy\s*apply", re.IGNORECASE)


class BlockedError(Exception):
    pass


def apply_type(html: str, button_text: str = "") -> str:
    """'outside_site', 'easy_apply' or 'unknown'. Unknown is never guessed into one of the others."""
    if OFFSITE_MARKER.search(html or ""):
        return "outside_site"
    if ONSITE_MARKER.search(html or "") or EASY_APPLY_TEXT.search(button_text or ""):
        return "easy_apply"
    return "unknown"


def clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def extract_details(page) -> dict:
    description = page.query_selector(".show-more-less-html__markup") or page.query_selector(".description__text")
    if description is None:
        raise BlockedError("the page has no job description (anti-bot challenge, expired job or layout change)")

    criteria = {}
    for item in page.query_selector_all("li.description__job-criteria-item"):
        header = item.query_selector(".description__job-criteria-subheader")
        value = item.query_selector(".description__job-criteria-text")
        if header and value:
            criteria[header.inner_text().strip()] = value.inner_text().strip()

    buttons = page.query_selector(".top-card-layout__cta-container")
    return {
        "apply_type": apply_type(page.content(), buttons.inner_text() if buttons else ""),
        "description": clean(description.inner_text())[:MAX_DESCRIPTION_CHARS],
        "employment_type": criteria.get("Employment type", ""),
        "seniority": criteria.get("Seniority level", ""),
        "industries": criteria.get("Industries", ""),
    }


def read_page(page, url: str) -> dict:
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
    except PlaywrightError as e:
        raise BlockedError(f"the page did not load ({type(e).__name__})")

    if response is None or response.status != 200:
        raise BlockedError(f"LinkedIn answered HTTP {response.status if response else 'none'}")
    if re.search(r"/(authwall|login|checkpoint)", page.url):
        raise BlockedError("LinkedIn asked for a login")
    return extract_details(page)


def fetch_job_details(urls: list[str], pause=time.sleep) -> tuple[dict[str, dict], str | None]:
    """Open public job pages one at a time, no login. Stops at the first sign of a block.

    Returns the details collected so far and, if LinkedIn stopped us, the reason.
    """
    details: dict[str, dict] = {}
    if not urls:
        return details, None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(locale="en-MY")
            for index, url in enumerate(urls):
                if index:
                    pause(random.uniform(*PAGE_DELAY_SECONDS))
                try:
                    details[url] = read_page(page, url)
                except BlockedError as e:
                    return details, str(e)
        finally:
            browser.close()

    return details, None
