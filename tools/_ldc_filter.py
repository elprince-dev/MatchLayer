import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
proc = subprocess.run(
    [sys.executable, "tools/learning_docs_check.py", "--root", ".", "--format", "json"],
    cwd=root,
    capture_output=True,
    text=True,
)

rows = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
mine = [r for r in rows if "usage-quotas-cost-as-dos" in r["file"]]
out = []
out.append(f"EXIT={proc.returncode}")
out.append(f"TOTAL_FINDINGS_ALL_DOCS={len(rows)}")
out.append(f"FINDINGS_FOR_THIS_DOC={len(mine)}")
for r in mine:
    out.append(f"{r['rule_id']} line {r['line']} ({r['requirement']}): {r['message']}")
text = "\n".join(out) + "\n"
Path(root / "tools" / "_ldc_result.txt").write_text(text, encoding="utf-8")
print(text)
