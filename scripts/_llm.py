"""Какой LLM-бэкенд взять для opinions.py и expand.py.

По умолчанию — открытый scripts/llm_backend.py (OpenRouter и другие OpenAI-совместимые API).
Свой модуль с тем же интерфейсом (llm, _norm, _s, STATS, _TL, spend, WRITER, WRITER_FALLBACK, QUOTER, CANON_CHEAP,
по желанию SOCIAL_EXTRACTORS) — путь к папке, где лежит survey.py: переменная SOCIAL_RESEARCH_SURVEY или файл
survey_path в папке ключей (одна строка с путём). Путь живёт только на вашей машине, в код и git не попадает.
Свои модели без своего модуля — файл llm_local.json в папке ключей: {"SOCIAL_EXTRACTORS": [["основная", "запасная"],
[...]], "TOPIC_FALLBACK": ["модель"], "WRITER": "…", "QUOTER": ["…"]} — значения подставляются в выбранный бэкенд.
"""
import json, os, sys
from pathlib import Path

from _keys import KEY_DIR


def backend():
    b = _pick()
    f = KEY_DIR / "llm_local.json"
    if f.exists():
        try:
            for k, v in json.loads(f.read_text()).items():
                setattr(b, k, tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list) else v)
        except (ValueError, AttributeError) as e:
            print(f"[_llm] {f.name} не прочитан: {e}", file=sys.stderr)
    return b


def _pick():
    p = os.environ.get("SOCIAL_RESEARCH_SURVEY")
    f = KEY_DIR / "survey_path"
    if not p and f.exists():
        p = f.read_text().strip()
    if p:
        d = Path(p).expanduser()
        if (d / "survey.py").exists():
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))
            import survey  # noqa: E402
            return survey
    import llm_backend  # noqa: E402
    return llm_backend
