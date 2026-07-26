"""본문을 한국어로 옮긴다 — **이 PC 안에서** 도는 LLM 으로.

논문 원문을 바깥 서비스에 올리지 않는다. 출판사 저작권이 걸린 글이고, 병원
PC 에서 쓰는 물건이다. 그래서 클라우드 API 를 쓰지 않는다.

── 무엇으로 도는가 ─────────────────────────────────────────────────
OpenAI 호환 `/v1/chat/completions` 를 말하는 로컬 서버면 무엇이든 된다.
지금은 Ollama(11434)를 쓰지만 llama.cpp 의 llama-server 로 바꿔도 코드는 그대로다
— 배포할 때 무엇을 동봉할지는 그때 정하면 된다.

기본 모델은 **EXAONE 3.5 7.8B**(LG AI). RTX 2060(VRAM 6GB) 에서 다섯을 실제로
돌려 보고 골랐다 — 같은 문단, 예열 후 측정:

  | 모델            | 문단당 | 결과                                    |
  |-----------------|-------:|-----------------------------------------|
  | EXAONE 3.5 7.8B |  7.8초 | 문체·용어·인용 모두 정확. **가장 자연스럽다** |
  | gemma3:4b       |  4.4초 | 읽기는 좋으나 **인용을 지어낸다**(없는 [15][20]) |
  | qwen2.5:3b      |  4.0초 | 영어가 섞여 나온다('요인의 Con tribution은') |
  | qwen2.5:7b      |  6.5초 | **중국어로 답했다**                      |
  | qwen3:4b        | 67.5초 | 사고 모드를 못 꺼 16배 느리다            |

논문 한 편(40문단)에 약 5분. 라이선스는 비상업(NC) 이라 **원장 본인 사용은 되지만
제품에 실어 파는 것은 안 된다** — 그때는 config 의 model 을 바꿔 끼우면 된다.
번역 품질은 모델마다 크게 갈리므로, 바꾸기 전에 반드시 같은 문단으로 재 볼 것.

── 어떻게 정확도를 지키는가 ────────────────────────────────────────
작은 모델은 의학 용어를 제멋대로 옮긴다('vitiligo' 를 '백반' 이라 하거나
'segmental' 을 '분할' 이라 하거나). 그래서 **용어집을 문단마다 주입**한다
(`glossary.json`, 원장이 감수한 277개). 그 문단에 실제로 나오는 용어만 골라
넣는다 — 전부 넣으면 프롬프트가 길어져 느려지고 엉뚱한 데 끌려간다.

── 무엇을 옮기지 않는가 ────────────────────────────────────────────
참고문헌(원문 그대로가 찾기 쉽다), 표 안의 값, 그림 캡션, 제목·서지정보.
본문·초록·절 제목만 옮긴다. 번역은 읽기를 돕는 것이지 원문을 대신하지 않는다.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

from . import store, utils

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "exaone3.5:7.8b"
TIMEOUT = 240.0

# 본문에 박힌 인용 표시. 번역이 이것을 잃거나 **지어내면** 그 문단은 버린다.
_CITE_RE = re.compile(r"\[\s*\d{1,3}\s*\]")

_GLOSSARY: dict[str, str] | None = None
_GLOSSARY_LOCK = threading.Lock()


def glossary() -> dict[str, str]:
    """EN(소문자) → KO 의학용어. ResearchMap 에서 쓰던 원장 감수본."""
    global _GLOSSARY
    with _GLOSSARY_LOCK:
        if _GLOSSARY is None:
            raw: dict[str, Any] = {}
            # exe 로 묶이면 이 모듈은 아카이브 안이라 __file__ 옆에 파일이 없다.
            # 동봉본(_MEIPASS)을 먼저 본다 — utils.load_config 와 같은 규약.
            import sys
            cands = [Path(__file__).with_name("glossary.json")]
            bundled = getattr(sys, "_MEIPASS", None)
            if bundled:
                cands.insert(0, Path(bundled) / "glossary.json")
            for path in cands:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    break
                except Exception:                # noqa: BLE001 — 없어도 번역은 된다
                    continue
            _GLOSSARY = {k.lower(): v for k, v in raw.items()
                         if not k.startswith("_") and isinstance(v, str)}
        return _GLOSSARY


def terms_in(text: str, limit: int = 24) -> list[tuple[str, str]]:
    """이 문단에 실제로 나오는 용어만. 긴 표현이 먼저다.

    'segmental vitiligo' 가 있으면 'vitiligo' 는 넣지 않는다 — 짧은 쪽이 긴 쪽을
    덮어써 '분절백반증' 이 '분절 백반증' 으로 갈라진다.
    """
    low = (text or "").lower()
    hits: list[tuple[str, str]] = []
    for en, ko in sorted(glossary().items(), key=lambda kv: -len(kv[0])):
        if len(en) < 3 or en not in low:
            continue
        if any(en in seen for seen, _ in hits):  # 이미 넣은 긴 표현의 일부
            continue
        hits.append((en, ko))
        if len(hits) >= limit:
            break
    return hits


_SYSTEM = (
    "You are a professional medical translator. Translate the English text of a "
    "dermatology research paper into natural Korean for a specialist reader.\n"
    "Rules:\n"
    "- Output ONLY the Korean translation. No preface, no notes, no explanation.\n"
    "- Keep the meaning exact. Do not add, drop, or summarize anything.\n"
    "- Keep citation markers like [15] exactly where they are.\n"
    "- Keep numbers, units, p-values, CIs, gene and drug names as in the original.\n"
    "- Keep abbreviations (NB-UVB, VASI, JAK) in the original form.\n"
    "- Write in the plain declarative style of a Korean medical journal (…한다/…이다)."
)


def _prompt(text: str) -> list[dict[str, str]]:
    terms = terms_in(text)
    msg = ""
    if terms:
        pairs = "\n".join(f"- {en} → {ko}" for en, ko in terms)
        msg += ("Use exactly these Korean terms for the following expressions:\n"
                f"{pairs}\n\n")
    msg += "Translate into Korean:\n\n" + text
    return [{"role": "system", "content": _SYSTEM},
            {"role": "user", "content": msg}]


# 생각 과정을 내보내는 모델(Qwen3 등)이 있다. 결과에서 걷어낸다.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


def _clean(out: str) -> str:
    out = _THINK_RE.sub("", out or "").strip()
    # 지시를 되풀이하는 버릇을 지운다("다음은 한국어 번역입니다:" 따위).
    out = re.sub(r"^(?:다음은|아래는)[^\n:]{0,30}:\s*", "", out).strip()
    return out.strip('"').strip()


def is_alive(url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(url.rstrip("/") + "/api/tags", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def models(url: str = DEFAULT_URL) -> list[str]:
    try:
        r = requests.get(url.rstrip("/") + "/api/tags", timeout=5)
        return [m.get("name", "") for m in (r.json().get("models") or [])]
    except Exception:                            # noqa: BLE001
        return []


def translate_one(text: str, *, url: str = DEFAULT_URL,
                  model: str = DEFAULT_MODEL) -> str:
    """문단 하나. 실패하면 빈 문자열 — 부르는 쪽이 원문을 그대로 쓴다.

    인용 표시가 어긋나면 **한 번 더 시킨다.** 없는 번호를 지어내는 것은 늘
    그러는 것이 아니라 이따금 있는 일이라, 다시 시키면 대개 바로잡힌다
    (실측 19단위 중 3건 → 재시도 후 대부분 살아난다). 그래도 어긋나면 버린다.
    """
    for attempt in (0, 1):
        msgs = _prompt(text)
        if attempt:
            msgs[-1]["content"] += (
                "\n\nIMPORTANT: copy the bracketed citation numbers exactly as "
                "they appear in the source. Do not add any that are not there.")
        body = {
            "model": model, "messages": msgs, "stream": False,
            # 번역은 창작이 아니다. 낮게 잡아 원문에서 멀어지지 않게 한다.
            "temperature": 0.2 if not attempt else 0.0,
            "top_p": 0.9,
        }
        try:
            r = requests.post(url.rstrip("/") + "/v1/chat/completions",
                              json=body, timeout=TIMEOUT)
            r.raise_for_status()
            out = (r.json()["choices"][0]["message"]["content"] or "")
        except Exception as e:                   # noqa: BLE001
            utils.log(f"      ! 번역 실패: {type(e).__name__}: {e}")
            return ""
        ko = _clean(out)
        if not ko:
            continue
        # 원문에 인용이 **하나도 없는데** 지어낸 것이라면 그 표시만 지운다.
        # 지운다고 근거가 어긋나지 않는다(원래 없던 것이다). 원문에 인용이
        # 있는데 어긋난 것은 어느 것이 맞는지 알 수 없으니 손대지 않는다.
        if not _CITE_RE.search(text) and _CITE_RE.search(ko):
            ko = re.sub(r"\s*" + _CITE_RE.pattern, "", ko).strip()
        if _keeps_citations(text, ko, quiet=not attempt):
            return ko
    return ""


def _keeps_citations(src: str, ko: str, quiet: bool = False) -> bool:
    """인용 표시가 원문과 같은가. 다르면 그 문단은 쓰지 않는다.

    작은 모델은 `(Harris et al. 2012)` 같은 저자-연도 인용을 **없는 번호**로
    바꿔 놓기도 한다(실측: gemma3:4b 가 '[15][20]' 을 지어냈다). 근거가 어긋난
    번역은 안 하느니만 못하다 — 이 프로젝트의 기준이다.
    """
    a, b = sorted(_CITE_RE.findall(src)), sorted(_CITE_RE.findall(ko))
    if a == b:
        return True
    if not quiet:
        utils.log(f"      · 번역 폐기(인용 어긋남 {a} → {b})")
    return False


# ── 문서 한 편 ───────────────────────────────────────────────────────
def _par_key(par: dict, si: int, pi: int) -> str:
    """문단의 이름표. 캐시와 화면이 같은 것을 써야 짝이 맞는다."""
    return str(par.get("id") or f"p:{si}:{pi}")


def _units(doc: dict) -> list[tuple[str, str]]:
    """번역할 조각들 (키, 원문). 키는 캐시와 화면이 함께 쓰는 이름표다.

    참고문헌·표·그림 캡션·제목은 넣지 않는다(모듈 설명 참고).
    """
    out: list[tuple[str, str]] = []
    abs_ = (doc.get("abstract") or "").strip()
    if abs_:
        out.append(("abstract", abs_))
    seen_head: set[str] = set()
    for si, sec in enumerate(doc.get("body_text") or []):
        for h in (sec.get("path") or []):
            h = (h or "").strip()
            if h and h.lower() not in seen_head:
                seen_head.add(h.lower())
                out.append((f"h:{h}", h))
        for pi, par in enumerate(sec.get("paragraphs") or []):
            t = (par.get("text") or "").strip()
            if len(t) >= 2:
                out.append((_par_key(par, si, pi), t))
    return out


def unit_count(doc: dict) -> int:
    return len(_units(doc))


def apply(doc: dict, cache: dict[str, str]) -> dict:
    """정본에 번역을 끼운 **사본**을 돌려준다. 원본은 건드리지 않는다.

    번역을 HTML 에 덧칠하지 않고 **정본 단계에서 갈아 끼우는** 이유: 그 뒤의
    렌더·화면 코드가 하나뿐이어야 하기 때문이다. 두 벌이 되면 어긋난다.
    아직 번역되지 않은 문단은 원문 그대로 남는다(번역 중에도 읽을 수 있다).
    """
    if not cache:
        return doc
    out = dict(doc)
    if cache.get("abstract"):
        out["abstract"] = cache["abstract"]
    secs = []
    for si, sec in enumerate(doc.get("body_text") or []):
        s = dict(sec)
        s["path"] = [cache.get(f"h:{(h or '').strip()}", h)
                     for h in (sec.get("path") or [])]
        pars = []
        for pi, par in enumerate(sec.get("paragraphs") or []):
            ko = cache.get(_par_key(par, si, pi))
            pars.append(dict(par, text=ko) if ko else par)
        s["paragraphs"] = pars
        secs.append(s)
    out["body_text"] = secs
    return out


def load_cache(sha1: str, lang: str = "ko") -> dict[str, str]:
    try:
        return json.loads(store.trans_path(sha1, lang).read_text(encoding="utf-8"))
    except Exception:                            # noqa: BLE001
        return {}


def save_cache(sha1: str, data: dict[str, str], lang: str = "ko") -> None:
    p = store.trans_path(sha1, lang)
    p.parent.mkdir(parents=True, exist_ok=True)
    utils.write_json(p, data)


def translate_doc(doc: dict, sha1: str, *, url: str = DEFAULT_URL,
                  model: str = DEFAULT_MODEL,
                  on_progress: Callable[[int, int], None] | None = None,
                  should_stop: Callable[[], bool] | None = None,
                  ) -> dict[str, str]:
    """문서 한 편을 옮긴다. 이미 옮긴 문단은 건너뛴다(캐시).

    중간에 멈춰도 여태 한 것은 남는다 — 논문 한 편이 몇 분이라, 껐다 켰다고
    처음부터 다시 하면 쓸 수 없는 물건이 된다.
    """
    cache = load_cache(sha1)
    units = _units(doc)
    todo = [(k, t) for k, t in units if k not in cache]
    total = len(units)
    if on_progress:
        on_progress(total - len(todo), total)
    done_since_save = 0
    for i, (key, text) in enumerate(todo, 1):
        if should_stop and should_stop():
            break
        ko = translate_one(text, url=url, model=model)
        if ko:
            cache[key] = ko
            done_since_save += 1
            if done_since_save >= 5:             # 중간에 죽어도 남게
                save_cache(sha1, cache)
                done_since_save = 0
        if on_progress:
            on_progress(total - len(todo) + i, total)
    if done_since_save:
        save_cache(sha1, cache)
    return cache


__all__ = ["glossary", "terms_in", "is_alive", "models", "translate_one",
           "translate_doc", "load_cache", "save_cache",
           "DEFAULT_URL", "DEFAULT_MODEL"]
