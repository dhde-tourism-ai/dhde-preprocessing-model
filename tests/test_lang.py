import pytest

from dhde_preprocessing import lang


@pytest.mark.parametrize("language,text,out", [
    ("zh", "大本山永平寺参拝", "ja"),          # kanji-only Japanese the detector calls Chinese
    ("zh-Hant", "#東尋坊 #福井", "ja"),         # shared characters say nothing
    ("zh-Hans", "永平寺 紅葉 #福井旅行", "ja"),
    ("zh", "东寻坊太美了", "zh-Hans"),
    ("zh-Hans", "臺灣人來福井", "zh-Hant"),     # one-sided markers beat the detector's guess
    ("zh-Hant", "東尋坊風景很美", "zh-Hant"),   # 很: Chinese, script left to the detector
    ("ja", "东寻坊", "ja"),                     # only a Chinese answer is second-guessed
    ("zh", "東尋坊好美", "zh"),                 # Taiwanese tourist words (review of #25)
    ("zh", "福井恐龍博物館超讚", "zh-Hant"),
    ("ko", "도진보", "ko"),
    (None, "永平寺", None),
])
def test_fix_chinese(language, text, out):
    assert lang.fix_chinese(language, text) == out


def test_markers_have_no_japanese_characters():
    # Characters Japanese writes the same way would turn Japanese posts Chinese.
    japanese = set("東館時間問見門車進国会来体点区湾園頭現華線買該過個遊龍竜尋県駅旅行温泉")
    assert not (lang.SIMPLIFIED_ONLY | lang.TRADITIONAL_ONLY) & japanese
