import json
import os
import re

from llm import complete

PASS_SCORE = 75
BATCH_SIZE = 10
RESUME_PATH = os.environ.get("RESUME_PATH", "/app/resume.txt")

PROMPT = """You screen job postings for one candidate. Score how well each job fits the candidate, from 0 to 100.

Only the job title, company and location are known. Do not invent details. If the title is unclear, stay between 50 and 70.

Scoring guide:
- 85-100: backend, API, PHP, Laravel or WordPress role at junior to mid level (senior is fine, just slightly lower), strong skill match
- 70-84: good match but partly different stack, or frontend-leaning
- 40-69: weak or unclear fit
- 0-39: wrong field (design, sales, support-only), unpaid, or clearly lead/manager or 6+ years

Use the PREFERENCES section of the resume. Add up to 5 points when the company is recognisably crypto or AI.

CANDIDATE RESUME
{resume}

JOBS
{jobs}

Reply with ONLY a JSON array, one object per job, no other text:
[{{"id": 1, "score": 80, "reason": "max 15 words"}}]"""


def load_resume(path: str = RESUME_PATH) -> str:
    with open(path, encoding="utf-8") as f:
        lines = [line.rstrip() for line in f if not line.lstrip().startswith("#")]
    resume = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not resume:
        raise ValueError("resume file is empty")
    if "[EXAMPLE" in resume:
        raise ValueError("resume file still has [EXAMPLE] placeholders")
    return resume


def build_prompt(resume: str, pairs: list[tuple[int, dict]]) -> str:
    # Only these three fields reach the model; the url and email id stay local.
    jobs = "\n".join(
        f'{job_id}. {job.get("title", "")} | {job.get("company", "")} | {job.get("location", "")}'
        for job_id, job in pairs
    )
    return PROMPT.format(resume=resume, jobs=jobs)


def parse_scores(text: str, valid_ids: set[int]) -> dict[int, dict]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match is None:
        return {}
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}

    results = {}
    for item in items:
        if not isinstance(item, dict) or item.get("id") not in valid_ids:
            continue
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        if not 0 <= score <= 100:
            continue
        results[item["id"]] = {"score": round(score), "reason": str(item.get("reason", ""))[:200]}
    return results


def score_jobs(jobs: list[dict], resume: str, ask=complete) -> tuple[list[dict], list[dict]]:
    scored, failed = [], []

    for start in range(0, len(jobs), BATCH_SIZE):
        pairs = list(enumerate(jobs[start : start + BATCH_SIZE], 1))
        results: dict[int, dict] = {}

        for _attempt in range(2):
            pending = [(job_id, job) for job_id, job in pairs if job_id not in results]
            if not pending:
                break
            # Reasoning models spend most of this on thinking; the JSON answer itself is small.
            text = ask(build_prompt(resume, pending), max_tokens=6000)
            results.update(parse_scores(text, {job_id for job_id, _ in pending}))

        for job_id, job in pairs:
            if job_id in results:
                score = results[job_id]["score"]
                scored.append({**job, **results[job_id], "passed": score >= PASS_SCORE})
            else:
                failed.append({**job, "reason": "model did not return a valid score"})

    return scored, failed


def explain_no_scores(failed: list[dict]) -> dict:
    return {
        "error_message": f"No job got a valid score ({len(failed)} failed)",
        "causes": [
            "The model answered in a format that is not a JSON array of {id, score, reason}",
            "A free model may add chatter or ignore the format instruction",
            "The reply was cut off because max_tokens ran out",
        ],
        "solution": [
            "Run again; free models are not fully consistent",
            "Check the model name in OPENROUTER_MODEL (.env), or try another free model",
            "Run: docker compose logs worker --tail 50",
        ],
    }
