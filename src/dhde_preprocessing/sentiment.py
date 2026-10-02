"""
Language and sentiment of short public posts (social mentions, their
comments, Instagram captions), so the collectors can keep a language
and a score without keeping the text.

Language: social-collector's detect_language (Anugra's collector, see
sources/social_listening.py), run on every item, comments included,
since a comment's language often differs from its post's. Script rules
for Japanese, Korean and Chinese, Traditional (Taiwan, Hong Kong) told
apart from Simplified with OpenCC, and the lingua detector for the rest
(Arabic, Thai, Vietnamese, English and other Latin-script languages).
Its rule calls kanji with little or no kana Chinese, so "#東尋坊 #福井"
came out Chinese; lang.fix_chinese keeps Chinese only where a character
or word only Chinese uses says so, else Japanese.

Sentiment model: lxyuan/distilbert-base-multilingual-cased-sentiments-
student (Apache 2.0, 0.1B parameters, runs on a CPU), trained on 12
languages (NATIVE). Each item goes one of three routes:
  - native:     a NATIVE language, scored as it is
  - converted:  Traditional Chinese, turned into Simplified first (OpenCC),
                the script the model was trained on
  - translated: anything else (Korean, Thai, Vietnamese, ...) translated
                to English first with a Helsinki-NLP opus-mt model (Apache
                2.0): ko-en for Korean, mul-en for the rest
Translations stay in memory and are never saved.

Score = P(positive) - P(negative), from -1 to 1, the scale the app's
Sentiment layer draws. Label from the score: positive from +NEUTRAL_BAND,
negative from -NEUTRAL_BAND, neutral between. Not the model's most likely
label: it almost never picks neutral (29 of 1,552 items on the first run,
while a third scored between -0.3 and +0.3), so a mild -0.14 came out
"negative" and both shares were overstated. A first model to replace later: check it against
hand-labelled items (scripts/collect_social.py --sample-out) before
trusting small differences.

Needs requirements-social.txt. The pipeline's own build never imports
this: scores are worked out at collection time and stored as numbers.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

MODEL = "lxyuan/distilbert-base-multilingual-cased-sentiments-student"
# The model's training languages (tyqiangz/multilingual-sentiments).
NATIVE = {"ar", "zh", "zh-Hans", "en", "fr", "de", "hi", "id", "it", "ja", "ms", "pt", "es"}
TRANSLATORS = {"ko": "Helsinki-NLP/opus-mt-ko-en"}
TRANSLATOR_ANY = "Helsinki-NLP/opus-mt-mul-en"
LABELS = ("positive", "neutral", "negative")
NEUTRAL_BAND = 0.2
MAX_TOKENS = 256  # long YouTube descriptions: the start carries the tone

_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"(?<!\w)[@＠][\w.\-]+")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Result:
    score: float
    label: str
    language: str | None
    route: str  # native | converted | translated


# texts (+ optional platform language hints) -> one Result, or None for nothing to score
Scorer = Callable[..., list[Result | None]]


def clean(text) -> str:
    """Links and @mentions out (no tone, and a handle is personal); hashtags
    stay, since #最高 or #disappointed carry tone."""
    if not isinstance(text, str):
        return ""
    return _SPACE.sub(" ", _MENTION.sub(" ", _URL.sub(" ", text))).strip()


def label_of(score) -> str | None:
    """positive / neutral / negative from a score, None for a missing one."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s != s:  # NaN
        return None
    return "positive" if s >= NEUTRAL_BAND else "negative" if s <= -NEUTRAL_BAND else "neutral"


def from_probs(probs: dict[str, float]) -> tuple[float, str]:
    """{label: probability} -> (score, label)."""
    p = {k.lower(): float(v) for k, v in probs.items()}
    score = round(p.get("positive", 0.0) - p.get("negative", 0.0), 4)
    return score, label_of(score)


def route(language: str | None) -> str:
    if language == "zh-Hant":
        return "converted"
    if language is None or language in NATIVE:
        return "native"  # undetected: mostly emoji and tags, the model copes
    return "translated"


def lang_group(language: str | None) -> str:
    """Language -> the column group the daily table counts it in."""
    if language in ("ja", "en", "ko", "ar", "zh-Hant", "zh-Hans"):
        return language.lower().replace("-", "_")
    if language == "zh":
        return "zh_hans"  # undecidable Chinese: shared characters, most often Simplified
    return "other"


def load_scorer(model: str = MODEL, batch_size: int = 32) -> Scorer:
    """Language detection, routing and the model, as a Scorer.

    Raises ImportError without requirements-social.txt. Translators load
    the first time a language needs one.
    """
    from collector.core.text import detect_language

    from .lang import fix_chinese
    from opencc import OpenCC
    from transformers import pipeline

    clf = pipeline("text-classification", model=model, top_k=None, truncation=True,
                   max_length=MAX_TOKENS, device=-1)
    t2s = OpenCC("t2s")
    translators: dict[str, tuple] = {}

    def translate(texts: list[str], language: str) -> list[str]:
        # The model itself, not pipeline("translation"), so this still works on
        # transformers 5, which dropped that task. requirements-social.txt pins <5
        # for now anyway (untested on 5 in CI).
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        name = TRANSLATORS.get(language, TRANSLATOR_ANY)
        if name not in translators:
            translators[name] = (AutoTokenizer.from_pretrained(name), AutoModelForSeq2SeqLM.from_pretrained(name))
        tok, mt = translators[name]
        out = []
        for start in range(0, len(texts), batch_size):
            batch = tok(texts[start:start + batch_size], return_tensors="pt", padding=True, truncation=True,
                        max_length=MAX_TOKENS)
            with torch.no_grad():
                ids = mt.generate(**batch, max_new_tokens=MAX_TOKENS)
            out += tok.batch_decode(ids, skip_special_tokens=True)
        return out

    def score(texts: Sequence[str | None], hints: Sequence[str | None] | None = None) -> list[Result | None]:
        hints = hints or [None] * len(texts)
        cleaned = [clean(t) for t in texts]
        langs = [fix_chinese(detect_language(t, h), t) if t else None for t, h in zip(cleaned, hints)]
        ready = list(cleaned)
        for i, (t, lang) in enumerate(zip(cleaned, langs)):
            if t and route(lang) == "converted":
                ready[i] = t2s.convert(t)
        by_lang: dict[str, list[int]] = {}
        for i, (t, lang) in enumerate(zip(cleaned, langs)):
            if t and route(lang) == "translated":
                by_lang.setdefault(lang, []).append(i)
        for lang, idx in by_lang.items():
            for i, en in zip(idx, translate([cleaned[i] for i in idx], lang)):
                ready[i] = en

        out: list[Result | None] = [None] * len(texts)
        todo = [i for i, t in enumerate(ready) if t]
        for start in range(0, len(todo), batch_size):
            idx = todo[start:start + batch_size]
            for i, res in zip(idx, clf([ready[i] for i in idx])):
                s, lab = from_probs({r["label"]: r["score"] for r in res})
                out[i] = Result(s, lab, langs[i], route(langs[i]))
        return out

    return score


def try_load_scorer() -> Scorer | None:
    """load_scorer(), or None (with a warning) where it can't load, so a
    collector still saves its counts without sentiment."""
    try:
        return load_scorer()
    except Exception as e:  # noqa: BLE001 - missing package, no network, bad download
        print(f"::warning::sentiment model not loaded ({type(e).__name__}: {e}); "
              "saving counts without sentiment. Install requirements-social.txt.")
        return None
