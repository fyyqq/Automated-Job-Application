import re
from datetime import datetime
from zoneinfo import ZoneInfo

JOB_ID = re.compile(r"/jobs/view/(\d+)")


def normalize(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def dedupe_key(company: str, title: str) -> str:
    return f"{normalize(company)}|{normalize(title)}"


def url_id(url: str) -> str:
    # LinkedIn links differ in tracking params; the numeric job id is the stable part.
    match = JOB_ID.search(url or "")
    if match:
        return match.group(1)
    return normalize(url).split("?")[0].rstrip("/")


def sheet_row(job: dict, score="", status="New") -> dict:
    return {
        "Date": datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%Y-%m-%d"),
        "Company": job.get("company", ""),
        "Position": job.get("title", ""),
        "URL": job.get("url", ""),
        "Dedupe Key": job.get("dedupe_key") or dedupe_key(job.get("company"), job.get("title")),
        "Source": job.get("source", ""),
        "Match Score": score,
        "Status": status,
    }


def split_new_jobs(jobs: list[dict], existing: list[dict]) -> tuple[list[dict], list[dict]]:
    sheet_keys = {normalize(row.get("Dedupe Key")) for row in existing} - {""}
    sheet_urls = {url_id(row.get("URL")) for row in existing} - {""}
    batch_keys, batch_urls = set(), set()

    new, duplicates = [], []
    for job in jobs:
        key = dedupe_key(job.get("company"), job.get("title"))
        uid = url_id(job.get("url"))

        if key in sheet_keys or (uid and uid in sheet_urls):
            duplicates.append({**job, "reason": "already in the sheet"})
        elif key in batch_keys or (uid and uid in batch_urls):
            duplicates.append({**job, "reason": "repeated in this batch"})
        else:
            batch_keys.add(key)
            batch_urls.add(uid)
            new.append({**job, "dedupe_key": key})

    return new, duplicates


def explain_no_new_jobs(duplicates: list[dict], existing: list[dict]) -> dict:
    causes, solution = [], []

    if any(job["reason"] == "already in the sheet" for job in duplicates):
        causes.append("These jobs are already logged in the sheet (normal when you re-run the same day)")
        solution.append("Nothing to fix. Wait for tomorrow's alerts, or delete the rows in the sheet to test again")
    if any(job["reason"] == "repeated in this batch" for job in duplicates):
        causes.append("The same job arrived more than once in this batch (several alerts list it)")
        solution.append("Nothing to fix. Only the first copy is kept")

    has_columns = any(row.get("Dedupe Key") or row.get("URL") for row in existing)
    if existing and not has_columns and any(row for row in existing):
        causes.append("'existing' rows have no 'Dedupe Key' or 'URL' column, so the sheet could not be compared")
        solution.append("Check the Google Sheets node reads the 'Applications' tab and the header row is Date, Company, Position, URL, Dedupe Key, ...")

    return {
        "error_message": f"No new jobs: all {len(duplicates)} were duplicates",
        "causes": causes,
        "solution": solution,
    }
