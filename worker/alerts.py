import re

SEPARATOR = re.compile(r"^-{20,}\s*$", re.MULTILINE)
JOB_ID = re.compile(r"/jobs/view/(\d+)")
BADGE_PREFIXES = ("this company is", "apply with", "easy apply", "be an early applicant")
# Digest emails open with "Your job alert for ..."; the confirmation email opens with two header lines.
HEADER_PREFIXES = ("your job alert", "you’ll receive notifications", "you'll receive notifications")


def parse_linkedin_alert(text: str) -> list[dict]:
    jobs = []
    seen = set()

    for block in SEPARATOR.split(text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        lines = [line for line in lines if not line.lower().startswith(HEADER_PREFIXES)]

        link = next((line for line in lines if line.startswith("View job:")), None)
        if link is None:
            continue

        match = JOB_ID.search(link)
        if match is None or match.group(1) in seen:
            continue

        details = lines[: lines.index(link)]
        if len(details) < 2:
            continue

        location = details[2] if len(details) > 2 else ""
        if location.lower().startswith(BADGE_PREFIXES):
            location = ""

        seen.add(match.group(1))
        jobs.append(
            {
                "title": details[0],
                "company": details[1],
                "location": location,
                # Drop the tracking query string; it carries per-user tokens.
                "url": f"https://www.linkedin.com/jobs/view/{match.group(1)}/",
            }
        )

    return jobs
