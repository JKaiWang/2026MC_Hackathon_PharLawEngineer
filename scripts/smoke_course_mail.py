"""用本機 Gemma 讀範例課程信，看它判得對不對，並走完對課表的建議。

需要 Ollama 在跑、模型已拉。信件只在本機處理，不會送雲端。
新地點的 GIS 驗證會打真實成大 GIS，所以 PROVIDER_MODE 強制 live。

用法：
    python scripts/smoke_course_mail.py                         # 跑 fixtures/course_mail/*.txt
    python scripts/smoke_course_mail.py path/to/mail.txt        # 自己的信（第一行主旨，空一行，內文）
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PROVIDER_MODE"] = "live"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from commute_agent.skills.mail_update import apply_course_mail  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "commute_agent" / "fixtures" / "course_mail"


def split_mail(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    subject, _, body = text.partition("\n\n")
    return subject.strip(), body.strip()


def main() -> None:
    files = [Path(p) for p in sys.argv[1:]] or sorted(SAMPLES.glob("*.txt"))
    for path in files:
        subject, body = split_mail(path)
        t0 = time.perf_counter()
        out = apply_course_mail(subject, body)
        dt = time.perf_counter() - t0
        mail = out["mail"]
        print(f"[{path.name}] {dt:.1f}s  status={out['status']}")
        if out["status"] != "ok":
            print(f"   {out['note']}\n")
            continue
        print(f"   kind={mail['kind']} importance={mail['importance']} course={mail['course']!r} "
              f"new_location={mail['new_location']!r} conf={mail['confidence']}")
        print(f"   patch={out['proposed_patch']}")
        print(f"   → {out['note']}\n")


if __name__ == "__main__":
    main()
