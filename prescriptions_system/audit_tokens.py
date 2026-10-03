"""Аудит и починка: длинные токены [A-Za-z0-9]{28,} — артефакты искажения транспорта.

В src/notifications/service.py такие токены出现在 двух местах и оба должны
быть именем ключа system_config: send_notifications_before_confirmation.
Для остальных файлов — только отчёт.
"""

import pathlib
import re

root = pathlib.Path(__file__).resolve().parent
pattern = re.compile(r"[A-Za-z0-9]{28,}")

report = {}
for path in root.rglob("*.py"):
    rel = path.relative_to(root)
    if ".venv" in str(rel):
        continue
    text = path.read_text(encoding="utf-8")
    hits = pattern.findall(text)
    if hits:
        report[str(rel)] = len(hits)

for rel, count in sorted(report.items()):
    print(rel, count)

key = "send_notifications" + "_before_confirmation"
target = root / "src" / "notifications" / "service.py"
t = target.read_text(encoding="utf-8")
t2 = pattern.sub(key, t)
target.write_text(t2, encoding="utf-8")
print("service fixed:", not pattern.search(t2), "| key present:", key in t2)
