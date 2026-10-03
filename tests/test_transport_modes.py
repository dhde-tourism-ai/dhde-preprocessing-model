"""Mode split rules for build_modes.py."""
from __future__ import annotations


import pandas as pd
import pytest

from dhde_preprocessing.transport.modes import counts, shares, split


def test_single_answer():
    assert split("自家用車\n") == {"own_car": 1.0}


def test_several_answers_split_equally():
    assert split("在来線\n路線バス\n") == {"train": 0.5, "bus": 0.5}


def test_route_and_tour_bus_are_one_mode():
    assert split("路線バス\n旅行会社ツアーバス\n") == {"bus": 1.0}


def test_walking_only_counts_alone():
    assert split("自家用車\n徒歩\n") == {"own_car": 1.0}
    assert split("徒歩\n") == {"other": 1.0}


def test_shares_sum_to_one_and_bound_the_interval():
    s = shares(pd.Series(["自家用車\n"] * 6 + ["在来線\n"] * 3 + ["レンタカー\n路線バス\n"]))
    assert s["n"] == 10
    assert sum(v["share"] for v in s["modes"].values()) == pytest.approx(1.0)
    assert s["modes"]["own_car"]["share"] == pytest.approx(0.6)
    for v in s["modes"].values():
        assert 0 <= v["lo"] <= v["share"] <= v["hi"] <= 1


def test_counts_scale_visitors():
    s = shares(pd.Series(["自家用車\n", "在来線\n"]))
    c = counts(1000, s)
    assert c["own_car"]["visitors"] == 500 and c["train"]["visitors"] == 500
    assert counts(None, s) is None
