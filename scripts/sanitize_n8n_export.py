"""Clean an n8n workflow export before it is committed to the public repo.

Usage: python3 scripts/sanitize_n8n_export.py "/path/to/Job Engine Workflow.json"
Writes n8n/job-engine-workflow.json.
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "n8n" / "job-engine-workflow.json"


def sanitize(workflow: dict) -> dict:
    for node in workflow["nodes"]:
        params = node["parameters"]
        node.pop("webhookId", None)

        if node["type"] == "n8n-nodes-base.telegram":
            params["chatId"] = "YOUR_TELEGRAM_CHAT_ID"
            params["text"] = "Hello from n8n"

        for key in ("documentId", "sheetName"):
            ref = params.get(key)
            if isinstance(ref, dict):
                ref.pop("cachedResultUrl", None)

        doc = params.get("documentId")
        if isinstance(doc, dict):
            doc["value"] = "YOUR_GOOGLE_SHEET_ID"
            doc["mode"] = "id"
            doc.pop("cachedResultName", None)

    workflow["meta"].pop("instanceId", None)
    workflow.pop("id", None)
    workflow.pop("versionId", None)
    # Pinned test data can hold real emails and job content.
    workflow["pinData"] = {}
    return workflow


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as f:
        workflow = sanitize(json.load(f))
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(workflow, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
