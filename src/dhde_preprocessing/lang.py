"""
Telling Japanese from Chinese when a text has kanji but no kana.

Short Japanese posts are often kanji only: "大本山永平寺参拝",
"恐竜博物館最高", "#東尋坊 #福井". A script rule ("Chinese characters,
no kana: Chinese") counts those as Chinese, and at Japanese sites that
inflates exactly the Taiwan / China share the international plan reads.

Rule: kanji-only text is Japanese unless it has a character or word that
only Chinese uses. Simplified forms Japanese never adopted (东, 龙, 馆,
这, 们), Traditional forms Japanese replaced (臺, 灣, 這, 們, 國), or
Chinese-only words (很, 吗, 旅游, 好玩). Characters both languages write
the same (東, 館, 時, 旅行) say nothing.

The trade-off: a short Chinese post with none of these ("東尋坊 夕陽")
counts as Japanese, so the Chinese share is a floor, not an estimate.
At Japanese sites that's the safer error.
"""
from __future__ import annotations

import re

# Simplified forms that aren't Japanese (Japanese has 東 龍 館 這 for these).
SIMPLIFIED_ONLY = set("们这说对吗还东龙馆门车时间觉问见边进么爱买卖钱线经应该让从华乐头发现实欢岛览观园汤鱼鸟红绿风飞寻个过荐赞")
# Traditional forms Japanese replaced (Japanese writes 台 湾 国 楽 来 会 for these).
TRADITIONAL_ONLY = set("們這說對嗎還臺灣觀國樂歡實體點會來邊麼覺寶舊處聽畫賣錢經區應讓從發覽綠讚")
# Chinese words or particles, either script, that Japanese doesn't use. 好美 is also a
# Japanese given name (Yoshimi), rare in place posts; Taiwanese captions use it a lot.
CHINESE_WORDS = ("很", "呢", "吧", "啊", "哦", "喔", "旅游", "旅遊", "好玩", "景点", "景點", "打卡", "必去", "好美")

_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def chinese_variant(text) -> str | None:
    """'zh-Hans', 'zh-Hant' or 'zh' when the text has a Chinese-only marker,
    else None (nothing says it isn't Japanese)."""
    if not isinstance(text, str):
        return None
    simp = sum(c in SIMPLIFIED_ONLY for c in text)
    trad = sum(c in TRADITIONAL_ONLY for c in text)
    if simp > trad:
        return "zh-Hans"
    if trad > simp:
        return "zh-Hant"
    return "zh" if simp or any(w in text for w in CHINESE_WORDS) else None


def is_han_only(text) -> bool:
    """Kanji, no kana."""
    return isinstance(text, str) and bool(_HAN.search(text)) and not _KANA.search(text)


def fix_chinese(language: str | None, text) -> str | None:
    """A detector's answer, with Chinese kept only where the text shows it.

    For detectors that call kanji-plus-little-kana Chinese (social-collector's
    detect_language): a 'zh*' answer without a Chinese-only marker becomes
    'ja'; with one, the marker's script wins over the detector's guess when
    the markers are one-sided."""
    if not language or not language.startswith("zh"):
        return language
    variant = chinese_variant(text)
    if variant is None:
        return "ja"
    return variant if variant != "zh" else language
