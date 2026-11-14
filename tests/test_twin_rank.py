"""twin/rank.py: every low/high case applies to the plant or the weather and moves only
what it names. The run itself (about 8 minutes) is not part of the tests."""
import numpy as np

from twin import electrical, params, rank, rooftop


def _weather():
    n = 6
    return rooftop.Weather(date="2026-11-15", t_local=np.arange(n), g=np.full(n, 800.0),
                           ta=np.full(n, 30.0), rh=np.full(n, 80.0), wind=np.full(n, 2.0),
                           doy=319, minute=np.arange(n, dtype=float))


def test_every_case_applies_and_changes_one_quantity():
    p, w = params.load(None, None), _weather()
    keys = [r[0] for r in rank.RANGES]
    assert len(keys) == len(set(keys))
    for key, _, why, low, high in rank.RANGES:
        assert why
        for change in (low, high):
            q, ws = rank._apply(p, [w], change)
            if change[0] == "param":
                changed = {n for n in p.meta if q.meta[n].value != p.meta[n].value}
                assert changed <= set(change[1](p)), key
                assert ws == [w]
            else:
                assert q is p and len(ws) == 1


def test_gamma_cases_reach_their_target():
    p = params.load(None, None)
    row = next(r for r in rank.RANGES if r[0] == "gamma")
    for change, target in ((row[3], -0.0030), (row[4], -0.0050)):
        q, _ = rank._apply(p, [], change)
        assert abs(electrical.gamma_pmp(q) - target) < 1e-6


def test_humidity_is_capped_at_100():
    _, ws = rank._apply(params.load(None, None), [_weather()],
                        next(r for r in rank.RANGES if r[0] == "humidity")[4])
    assert ws[0].rh.max() == 100.0
