"""Alpha101 Python DSL cases using the project 7 v2 analysis contract.

The formulas are transcribed from Appendix A of *101 Formulaic Alphas*.
Fractional time windows are floored as required by the paper.  Arena-specific
input conventions are intentionally explicit here:

* ``vwap`` is ``(high + low + close) / 3``;
* ``returns`` is the one-period close-to-close simple return;
* ``advN`` is the N-period rolling mean of ``vol``;
* sector/industry/subindustry use ``industry_l0``/``industry_l1``/``industry_l3``.
"""

from __future__ import annotations

import json
from typing import Final

from core.utils.dsl import compile_python_dsl
from core.utils.dsl_source import (
    FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES,
)


PROJECT_7_V2_START_DATE: Final = "2012-01-01"
PROJECT_7_V2_END_DATE: Final = "2027-01-01"
PROJECT_7_V2_LOOKBACK: Final = "P400D"
PROJECT_7_V2_RETURN_COLUMNS: Final = tuple(f"ret{index}" for index in range(10))
PROJECT_7_V2_NEGATED_NUMBERS: Final[frozenset[int]] = frozenset(
    {1, 7, 12, 22, 43, 45, 48, 58, 61, 62, 65, 74, 75, 78, 81, 85, 98, 99, 101}
)


ALPHA101_PRELUDE: Final = '''
def add(left, right):
    return DIRECT.binary.add(left=left, right=right)

def sub(left, right):
    return DIRECT.binary.sub(left=left, right=right)

def mul(left, right):
    return DIRECT.binary.mul(left=left, right=right)

def div(left, right):
    return DIRECT.binary.div(left=left, right=right)

def power(left, right):
    return DIRECT.binary.pow(left=left, right=right)

def minimum(left, right):
    return DIRECT.binary.minimum(left=left, right=right)

def maximum(left, right):
    return DIRECT.binary.maximum(left=left, right=right)

def neg(col):
    return DIRECT.unary.neg(col=col)

def abs_value(col):
    return DIRECT.unary.abs(col=col)

def log_value(col):
    return DIRECT.unary.log(col=col)

def sign(col):
    return DIRECT.unary.sign(col=col)

def less(left, right):
    return DIRECT.binary.lt(left=left, right=right)

def less_equal(left, right):
    return DIRECT.binary.le(left=left, right=right)

def greater(left, right):
    return DIRECT.binary.gt(left=left, right=right)

def equal(left, right):
    return DIRECT.binary.eq(left=left, right=right)

def logic_or(left, right):
    return DIRECT.binary.or_(left=left, right=right)

def where(condition, if_true, if_false):
    return DIRECT.ternary.where(
        condition=condition,
        if_true=if_true,
        if_false=if_false,
    )

def rank(col):
    return CS.unary.rank_pct(
        col=col,
        ascending=True,
        ties_method="average",
        on=tradable,
    )

def delay(col, periods):
    return TS.unary.shift(col=col, periods=periods)

def delta(col, periods):
    return TS.unary.diff(col=col, periods=periods)

def correlation(left, right, window):
    return TS.binary.rolling_corr(
        left=left,
        right=right,
        window=window,
        min_periods=window,
    )

def covariance(left, right, window):
    return TS.binary.rolling_cov(
        left=left,
        right=right,
        window=window,
        min_periods=window,
    )

def scale(col):
    return CS.unary.normalize_l1(col=col, on=tradable)

def signed_power(left, right):
    return DIRECT.binary.mul(
        left=DIRECT.unary.sign(col=left),
        right=DIRECT.binary.pow(
            left=DIRECT.unary.abs(col=left),
            right=right,
        ),
    )

def decay_linear(col, window):
    return TS.unary.decay_linear(
        col=col,
        window=window,
        min_periods=window,
    )

def neutralize(col, by):
    return CS.grouped.demean(col=col, by=by, on=tradable)

def ts_min(col, window):
    return TS.unary.rolling_min(
        col=col,
        window=window,
        min_periods=window,
    )

def ts_max(col, window):
    return TS.unary.rolling_max(
        col=col,
        window=window,
        min_periods=window,
    )

def ts_argmin(col, window):
    return TS.unary.rolling_argmin(
        col=col,
        window=window,
        min_periods=window,
    )

def ts_argmax(col, window):
    return TS.unary.rolling_argmax(
        col=col,
        window=window,
        min_periods=window,
    )

def ts_rank(col, window):
    return TS.unary.rolling_rank_pct(
        col=col,
        window=window,
        min_periods=window,
        ascending=True,
        ties_method="average",
    )

def ts_sum(col, window):
    return TS.unary.rolling_sum(
        col=col,
        window=window,
        min_periods=window,
    )

def ts_product(col, window):
    return TS.unary.rolling_prod(
        col=col,
        window=window,
        min_periods=window,
    )

def stddev(col, window):
    return TS.unary.rolling_std(
        col=col,
        window=window,
        min_periods=window,
    )

tradable = DIRECT.multiary.and_(
    "tradable",
    cols=[
        "stock_pool_member",
        DIRECT.binary.eq(left="is_st", right=0),
        DIRECT.multiary.or_(
            cols=[
                DIRECT.binary.le(left="limit_status", right=1),
                DIRECT.binary.eq(left="limit_status", right=4),
            ],
        ),
    ],
)

vwap = DIRECT.binary.div(
    "alpha101_vwap",
    left=DIRECT.multiary.add(cols=["high", "low", "close"]),
    right=3,
)
returns = TS.unary.pct_change(
    "alpha101_returns",
    col="close",
    periods=1,
)
adv5 = TS.unary.rolling_mean("alpha101_adv5", col="vol", window=5, min_periods=5)
adv10 = TS.unary.rolling_mean("alpha101_adv10", col="vol", window=10, min_periods=10)
adv15 = TS.unary.rolling_mean("alpha101_adv15", col="vol", window=15, min_periods=15)
adv20 = TS.unary.rolling_mean("alpha101_adv20", col="vol", window=20, min_periods=20)
adv30 = TS.unary.rolling_mean("alpha101_adv30", col="vol", window=30, min_periods=30)
adv40 = TS.unary.rolling_mean("alpha101_adv40", col="vol", window=40, min_periods=40)
adv50 = TS.unary.rolling_mean("alpha101_adv50", col="vol", window=50, min_periods=50)
adv60 = TS.unary.rolling_mean("alpha101_adv60", col="vol", window=60, min_periods=60)
adv81 = TS.unary.rolling_mean("alpha101_adv81", col="vol", window=81, min_periods=81)
adv120 = TS.unary.rolling_mean("alpha101_adv120", col="vol", window=120, min_periods=120)
adv150 = TS.unary.rolling_mean("alpha101_adv150", col="vol", window=150, min_periods=150)
adv180 = TS.unary.rolling_mean("alpha101_adv180", col="vol", window=180, min_periods=180)
cap = "total_mv"
sector = "industry_l0"
industry = "industry_l1"
subindustry = "industry_l3"
'''


ALPHA101_EXPRESSIONS: Final[dict[int, str]] = {
    1: '''sub(
        rank(ts_argmax(signed_power(
            where(less(returns, 0), stddev(returns, 20), "close"),
            2,
        ), 5)),
        0.5,
    )''',
    2: '''neg(correlation(
        rank(delta(log_value("vol"), 2)),
        rank(div(sub("close", "open"), "open")),
        6,
    ))''',
    3: '''neg(correlation(rank("open"), rank("vol"), 10))''',
    4: '''neg(ts_rank(rank("low"), 9))''',
    5: '''mul(
        rank(sub("open", div(ts_sum(vwap, 10), 10))),
        neg(abs_value(rank(sub("close", vwap)))),
    )''',
    6: '''neg(correlation("open", "vol", 10))''',
    7: '''where(
        less(adv20, "vol"),
        mul(neg(ts_rank(abs_value(delta("close", 7)), 60)), sign(delta("close", 7))),
        -1,
    )''',
    8: '''neg(rank(sub(
        mul(ts_sum("open", 5), ts_sum(returns, 5)),
        delay(mul(ts_sum("open", 5), ts_sum(returns, 5)), 10),
    )))''',
    9: '''where(
        greater(ts_min(delta("close", 1), 5), 0),
        delta("close", 1),
        where(
            less(ts_max(delta("close", 1), 5), 0),
            delta("close", 1),
            neg(delta("close", 1)),
        ),
    )''',
    10: '''rank(where(
        greater(ts_min(delta("close", 1), 4), 0),
        delta("close", 1),
        where(
            less(ts_max(delta("close", 1), 4), 0),
            delta("close", 1),
            neg(delta("close", 1)),
        ),
    ))''',
    11: '''mul(
        add(rank(ts_max(sub(vwap, "close"), 3)), rank(ts_min(sub(vwap, "close"), 3))),
        rank(delta("vol", 3)),
    )''',
    12: '''mul(sign(delta("vol", 1)), neg(delta("close", 1)))''',
    13: '''neg(rank(covariance(rank("close"), rank("vol"), 5)))''',
    14: '''mul(neg(rank(delta(returns, 3))), correlation("open", "vol", 10))''',
    15: '''neg(ts_sum(rank(correlation(rank("high"), rank("vol"), 3)), 3))''',
    16: '''neg(rank(covariance(rank("high"), rank("vol"), 5)))''',
    17: '''mul(
        mul(neg(rank(ts_rank("close", 10))), rank(delta(delta("close", 1), 1))),
        rank(ts_rank(div("vol", adv20), 5)),
    )''',
    18: '''neg(rank(add(
        add(stddev(abs_value(sub("close", "open")), 5), sub("close", "open")),
        correlation("close", "open", 10),
    )))''',
    19: '''mul(
        neg(sign(add(sub("close", delay("close", 7)), delta("close", 7)))),
        add(1, rank(add(1, ts_sum(returns, 250)))),
    )''',
    20: '''mul(
        mul(neg(rank(sub("open", delay("high", 1)))), rank(sub("open", delay("close", 1)))),
        rank(sub("open", delay("low", 1))),
    )''',
    21: '''where(
        less(add(div(ts_sum("close", 8), 8), stddev("close", 8)), div(ts_sum("close", 2), 2)),
        -1,
        where(
            less(div(ts_sum("close", 2), 2), sub(div(ts_sum("close", 8), 8), stddev("close", 8))),
            1,
            where(logic_or(greater(div("vol", adv20), 1), equal(div("vol", adv20), 1)), 1, -1),
        ),
    )''',
    22: '''neg(mul(
        delta(correlation("high", "vol", 5), 5),
        rank(stddev("close", 20)),
    ))''',
    23: '''where(
        less(div(ts_sum("high", 20), 20), "high"),
        neg(delta("high", 2)),
        0,
    )''',
    24: '''where(
        less_equal(div(delta(div(ts_sum("close", 100), 100), 100), delay("close", 100)), 0.05),
        neg(sub("close", ts_min("close", 100))),
        neg(delta("close", 3)),
    )''',
    25: '''rank(mul(mul(mul(neg(returns), adv20), vwap), sub("high", "close")))''',
    26: '''neg(ts_max(correlation(ts_rank("vol", 5), ts_rank("high", 5), 5), 3))''',
    27: '''where(
        greater(rank(div(ts_sum(correlation(rank("vol"), rank(vwap), 6), 2), 2.0)), 0.5),
        -1,
        1,
    )''',
    28: '''scale(sub(add(correlation(adv20, "low", 5), div(add("high", "low"), 2)), "close"))''',
    29: '''add(
        ts_min(
            rank(rank(scale(log_value(
                ts_min(rank(rank(neg(rank(delta(sub("close", 1), 5))))), 2)
            )))),
            5,
        ),
        ts_rank(delay(neg(returns), 6), 5),
    )''',
    30: '''div(
        mul(
            sub(1.0, rank(add(
                add(sign(sub("close", delay("close", 1))), sign(sub(delay("close", 1), delay("close", 2)))),
                sign(sub(delay("close", 2), delay("close", 3))),
            ))),
            ts_sum("vol", 5),
        ),
        ts_sum("vol", 20),
    )''',
    31: '''add(
        add(
            rank(rank(rank(decay_linear(neg(rank(rank(delta("close", 10)))), 10)))),
            rank(neg(delta("close", 3))),
        ),
        sign(scale(correlation(adv20, "low", 12))),
    )''',
    32: '''add(
        scale(sub(div(ts_sum("close", 7), 7), "close")),
        mul(20, scale(correlation(vwap, delay("close", 5), 230))),
    )''',
    33: '''rank(neg(power(sub(1, div("open", "close")), 1)))''',
    34: '''rank(add(
        sub(1, rank(div(stddev(returns, 2), stddev(returns, 5)))),
        sub(1, rank(delta("close", 1))),
    ))''',
    35: '''mul(
        mul(ts_rank("vol", 32), sub(1, ts_rank(sub(add("close", "high"), "low"), 16))),
        sub(1, ts_rank(returns, 32)),
    )''',
    36: '''add(
        add(
            add(
                add(
                    mul(2.21, rank(correlation(sub("close", "open"), delay("vol", 1), 15))),
                    mul(0.7, rank(sub("open", "close"))),
                ),
                mul(0.73, rank(ts_rank(delay(neg(returns), 6), 5))),
            ),
            rank(abs_value(correlation(vwap, adv20, 6))),
        ),
        mul(0.6, rank(mul(sub(div(ts_sum("close", 200), 200), "open"), sub("close", "open")))),
    )''',
    37: '''add(
        rank(correlation(delay(sub("open", "close"), 1), "close", 200)),
        rank(sub("open", "close")),
    )''',
    38: '''mul(neg(rank(ts_rank("close", 10))), rank(div("close", "open")))''',
    39: '''mul(
        neg(rank(mul(
            delta("close", 7),
            sub(1, rank(decay_linear(div("vol", adv20), 9))),
        ))),
        add(1, rank(ts_sum(returns, 250))),
    )''',
    40: '''mul(neg(rank(stddev("high", 10))), correlation("high", "vol", 10))''',
    41: '''sub(power(mul("high", "low"), 0.5), vwap)''',
    42: '''div(rank(sub(vwap, "close")), rank(add(vwap, "close")))''',
    43: '''mul(
        ts_rank(div("vol", adv20), 20),
        ts_rank(neg(delta("close", 7)), 8),
    )''',
    44: '''neg(correlation("high", rank("vol"), 5))''',
    45: '''neg(mul(
        mul(
            rank(div(ts_sum(delay("close", 5), 20), 20)),
            correlation("close", "vol", 2),
        ),
        rank(correlation(ts_sum("close", 5), ts_sum("close", 20), 2)),
    ))''',
    46: '''where(
        greater(sub(
            div(sub(delay("close", 20), delay("close", 10)), 10),
            div(sub(delay("close", 10), "close"), 10),
        ), 0.25),
        -1,
        where(
            less(sub(
                div(sub(delay("close", 20), delay("close", 10)), 10),
                div(sub(delay("close", 10), "close"), 10),
            ), 0),
            1,
            neg(sub("close", delay("close", 1))),
        ),
    )''',
    47: '''sub(
        mul(
            div(mul(rank(div(1, "close")), "vol"), adv20),
            div(mul("high", rank(sub("high", "close"))), div(ts_sum("high", 5), 5)),
        ),
        rank(sub(vwap, delay(vwap, 5))),
    )''',
    48: '''div(
        neutralize(div(
            mul(
                correlation(delta("close", 1), delta(delay("close", 1), 1), 250),
                delta("close", 1),
            ),
            "close",
        ), subindustry),
        ts_sum(power(div(delta("close", 1), delay("close", 1)), 2), 250),
    )''',
    49: '''where(
        less(sub(
            div(sub(delay("close", 20), delay("close", 10)), 10),
            div(sub(delay("close", 10), "close"), 10),
        ), -0.1),
        1,
        neg(sub("close", delay("close", 1))),
    )''',
    50: '''neg(ts_max(rank(correlation(rank("vol"), rank(vwap), 5)), 5))''',
    51: '''where(
        less(sub(
            div(sub(delay("close", 20), delay("close", 10)), 10),
            div(sub(delay("close", 10), "close"), 10),
        ), -0.05),
        1,
        neg(sub("close", delay("close", 1))),
    )''',
    52: '''mul(
        mul(
            add(neg(ts_min("low", 5)), delay(ts_min("low", 5), 5)),
            rank(div(sub(ts_sum(returns, 240), ts_sum(returns, 20)), 220)),
        ),
        ts_rank("vol", 5),
    )''',
    53: '''neg(delta(div(
        sub(sub("close", "low"), sub("high", "close")),
        sub("close", "low"),
    ), 9))''',
    54: '''div(
        neg(mul(sub("low", "close"), power("open", 5))),
        mul(sub("low", "high"), power("close", 5)),
    )''',
    55: '''neg(correlation(
        rank(div(
            sub("close", ts_min("low", 12)),
            sub(ts_max("high", 12), ts_min("low", 12)),
        )),
        rank("vol"),
        6,
    ))''',
    56: '''neg(mul(
        rank(div(ts_sum(returns, 10), ts_sum(ts_sum(returns, 2), 3))),
        rank(mul(returns, cap)),
    ))''',
    57: '''neg(div(
        sub("close", vwap),
        decay_linear(rank(ts_argmax("close", 30)), 2),
    ))''',
    58: '''neg(ts_rank(decay_linear(
        correlation(neutralize(vwap, sector), "vol", 3),
        7,
    ), 5))''',
    59: '''neg(ts_rank(decay_linear(
        correlation(
            neutralize(add(mul(vwap, 0.728317), mul(vwap, sub(1, 0.728317))), industry),
            "vol",
            4,
        ),
        16,
    ), 8))''',
    60: '''neg(sub(
        mul(2, scale(rank(mul(
            div(
                sub(sub("close", "low"), sub("high", "close")),
                sub("high", "low"),
            ),
            "vol",
        )))),
        scale(rank(ts_argmax("close", 10))),
    ))''',
    61: '''less(
        rank(sub(vwap, ts_min(vwap, 16))),
        rank(correlation(vwap, adv180, 17)),
    )''',
    62: '''mul(less(
        rank(correlation(vwap, ts_sum(adv20, 22), 9)),
        rank(less(
            add(rank("open"), rank("open")),
            add(rank(div(add("high", "low"), 2)), rank("high")),
        )),
    ), -1)''',
    63: '''mul(sub(
        rank(decay_linear(delta(neutralize("close", industry), 2), 8)),
        rank(decay_linear(correlation(
            add(mul(vwap, 0.318108), mul("open", sub(1, 0.318108))),
            ts_sum(adv180, 37),
            13,
        ), 12)),
    ), -1)''',
    64: '''mul(less(
        rank(correlation(
            ts_sum(add(mul("open", 0.178404), mul("low", sub(1, 0.178404))), 12),
            ts_sum(adv120, 12),
            16,
        )),
        rank(delta(add(
            mul(div(add("high", "low"), 2), 0.178404),
            mul(vwap, sub(1, 0.178404)),
        ), 3)),
    ), -1)''',
    65: '''mul(less(
        rank(correlation(
            add(mul("open", 0.00817205), mul(vwap, sub(1, 0.00817205))),
            ts_sum(adv60, 8),
            6,
        )),
        rank(sub("open", ts_min("open", 13))),
    ), -1)''',
    66: '''mul(add(
        rank(decay_linear(delta(vwap, 3), 7)),
        ts_rank(decay_linear(div(
            sub(add(mul("low", 0.96633), mul("low", sub(1, 0.96633))), vwap),
            sub("open", div(add("high", "low"), 2)),
        ), 11), 6),
    ), -1)''',
    67: '''mul(power(
        rank(sub("high", ts_min("high", 2))),
        rank(correlation(
            neutralize(vwap, sector),
            neutralize(adv20, subindustry),
            6,
        )),
    ), -1)''',
    68: '''mul(less(
        ts_rank(correlation(rank("high"), rank(adv15), 8), 13),
        rank(delta(add(
            mul("close", 0.518371),
            mul("low", sub(1, 0.518371)),
        ), 1)),
    ), -1)''',
    69: '''mul(power(
        rank(ts_max(delta(neutralize(vwap, industry), 2), 4)),
        ts_rank(correlation(
            add(mul("close", 0.490655), mul(vwap, sub(1, 0.490655))),
            adv20,
            4,
        ), 9),
    ), -1)''',
    70: '''mul(power(
        rank(delta(vwap, 1)),
        ts_rank(correlation(neutralize("close", industry), adv50, 17), 17),
    ), -1)''',
    71: '''maximum(
        ts_rank(decay_linear(correlation(
            ts_rank("close", 3),
            ts_rank(adv180, 12),
            18,
        ), 4), 15),
        ts_rank(decay_linear(power(rank(sub(
            add("low", "open"),
            add(vwap, vwap),
        )), 2), 16), 4),
    )''',
    72: '''div(
        rank(decay_linear(correlation(div(add("high", "low"), 2), adv40, 8), 10)),
        rank(decay_linear(correlation(ts_rank(vwap, 3), ts_rank("vol", 18), 6), 2)),
    )''',
    73: '''mul(maximum(
        rank(decay_linear(delta(vwap, 4), 2)),
        ts_rank(decay_linear(mul(div(
            delta(add(mul("open", 0.147155), mul("low", sub(1, 0.147155))), 2),
            add(mul("open", 0.147155), mul("low", sub(1, 0.147155))),
        ), -1), 3), 16),
    ), -1)''',
    74: '''mul(less(
        rank(correlation("close", ts_sum(adv30, 37), 15)),
        rank(correlation(
            rank(add(mul("high", 0.0261661), mul(vwap, sub(1, 0.0261661)))),
            rank("vol"),
            11,
        )),
    ), -1)''',
    75: '''less(
        rank(correlation(vwap, "vol", 4)),
        rank(correlation(rank("low"), rank(adv50), 12)),
    )''',
    76: '''mul(maximum(
        rank(decay_linear(delta(vwap, 1), 11)),
        ts_rank(decay_linear(ts_rank(
            correlation(neutralize("low", sector), adv81, 8),
            19,
        ), 17), 19),
    ), -1)''',
    77: '''minimum(
        rank(decay_linear(sub(
            add(div(add("high", "low"), 2), "high"),
            add(vwap, "high"),
        ), 20)),
        rank(decay_linear(correlation(div(add("high", "low"), 2), adv40, 3), 5)),
    )''',
    78: '''power(
        rank(correlation(
            ts_sum(add(mul("low", 0.352233), mul(vwap, sub(1, 0.352233))), 19),
            ts_sum(adv40, 19),
            6,
        )),
        rank(correlation(rank(vwap), rank("vol"), 5)),
    )''',
    79: '''less(
        rank(delta(neutralize(add(
            mul("close", 0.60733),
            mul("open", sub(1, 0.60733)),
        ), sector), 1)),
        rank(correlation(ts_rank(vwap, 3), ts_rank(adv150, 9), 14)),
    )''',
    80: '''mul(power(
        rank(sign(delta(neutralize(add(
            mul("open", 0.868128),
            mul("high", sub(1, 0.868128)),
        ), industry), 4))),
        ts_rank(correlation("high", adv10, 5), 5),
    ), -1)''',
    81: '''mul(less(
        rank(log_value(ts_product(rank(power(
            rank(correlation(vwap, ts_sum(adv10, 49), 8)),
            4,
        )), 14))),
        rank(correlation(rank(vwap), rank("vol"), 5)),
    ), -1)''',
    82: '''mul(minimum(
        rank(decay_linear(delta("open", 1), 14)),
        ts_rank(decay_linear(correlation(
            neutralize("vol", sector),
            add(mul("open", 0.634196), mul("open", sub(1, 0.634196))),
            17,
        ), 6), 13),
    ), -1)''',
    83: '''div(
        mul(
            rank(delay(div(sub("high", "low"), div(ts_sum("close", 5), 5)), 2)),
            rank(rank("vol")),
        ),
        div(
            div(sub("high", "low"), div(ts_sum("close", 5), 5)),
            sub(vwap, "close"),
        ),
    )''',
    84: '''signed_power(
        ts_rank(sub(vwap, ts_max(vwap, 15)), 20),
        delta("close", 4),
    )''',
    85: '''power(
        rank(correlation(
            add(mul("high", 0.876703), mul("close", sub(1, 0.876703))),
            adv30,
            9,
        )),
        rank(correlation(
            ts_rank(div(add("high", "low"), 2), 3),
            ts_rank("vol", 10),
            7,
        )),
    )''',
    86: '''mul(less(
        ts_rank(correlation("close", ts_sum(adv20, 14), 6), 20),
        rank(sub(add("open", "close"), add(vwap, "open"))),
    ), -1)''',
    87: '''mul(maximum(
        rank(decay_linear(delta(add(
            mul("close", 0.369701),
            mul(vwap, sub(1, 0.369701)),
        ), 1), 2)),
        ts_rank(decay_linear(abs_value(correlation(
            neutralize(adv81, industry),
            "close",
            13,
        )), 4), 14),
    ), -1)''',
    88: '''minimum(
        rank(decay_linear(sub(
            add(rank("open"), rank("low")),
            add(rank("high"), rank("close")),
        ), 8)),
        ts_rank(decay_linear(correlation(
            ts_rank("close", 8),
            ts_rank(adv60, 20),
            8,
        ), 6), 2),
    )''',
    89: '''sub(
        ts_rank(decay_linear(correlation(
            add(mul("low", 0.967285), mul("low", sub(1, 0.967285))),
            adv10,
            6,
        ), 5), 3),
        ts_rank(decay_linear(delta(neutralize(vwap, industry), 3), 10), 15),
    )''',
    90: '''mul(power(
        rank(sub("close", ts_max("close", 4))),
        ts_rank(correlation(neutralize(adv40, subindustry), "low", 5), 3),
    ), -1)''',
    91: '''mul(sub(
        ts_rank(decay_linear(decay_linear(correlation(
            neutralize("close", industry),
            "vol",
            9,
        ), 16), 3), 4),
        rank(decay_linear(correlation(vwap, adv30, 4), 2)),
    ), -1)''',
    92: '''minimum(
        ts_rank(decay_linear(less(
            add(div(add("high", "low"), 2), "close"),
            add("low", "open"),
        ), 14), 18),
        ts_rank(decay_linear(correlation(rank("low"), rank(adv30), 7), 6), 6),
    )''',
    93: '''div(
        ts_rank(decay_linear(correlation(
            neutralize(vwap, industry),
            adv81,
            17,
        ), 19), 7),
        rank(decay_linear(delta(add(
            mul("close", 0.524434),
            mul(vwap, sub(1, 0.524434)),
        ), 2), 16)),
    )''',
    94: '''mul(power(
        rank(sub(vwap, ts_min(vwap, 11))),
        ts_rank(correlation(ts_rank(vwap, 19), ts_rank(adv60, 4), 18), 2),
    ), -1)''',
    95: '''less(
        rank(sub("open", ts_min("open", 12))),
        ts_rank(power(rank(correlation(
            ts_sum(div(add("high", "low"), 2), 19),
            ts_sum(adv40, 19),
            12,
        )), 5), 11),
    )''',
    96: '''mul(maximum(
        ts_rank(decay_linear(correlation(rank(vwap), rank("vol"), 3), 4), 8),
        ts_rank(decay_linear(ts_argmax(correlation(
            ts_rank("close", 7),
            ts_rank(adv60, 4),
            3,
        ), 12), 14), 13),
    ), -1)''',
    97: '''mul(sub(
        rank(decay_linear(delta(neutralize(add(
            mul("low", 0.721001),
            mul(vwap, sub(1, 0.721001)),
        ), industry), 3), 20)),
        ts_rank(decay_linear(ts_rank(correlation(
            ts_rank("low", 7),
            ts_rank(adv60, 17),
            4,
        ), 18), 15), 6),
    ), -1)''',
    98: '''sub(
        rank(decay_linear(correlation(vwap, ts_sum(adv5, 26), 4), 7)),
        rank(decay_linear(ts_rank(ts_argmin(correlation(
            rank("open"),
            rank(adv15),
            20,
        ), 8), 6), 8)),
    )''',
    99: '''mul(less(
        rank(correlation(
            ts_sum(div(add("high", "low"), 2), 19),
            ts_sum(adv60, 19),
            8,
        )),
        rank(correlation("low", "vol", 6)),
    ), -1)''',
    100: '''neg(mul(
        sub(
            mul(1.5, scale(neutralize(neutralize(rank(mul(
                div(
                    sub(sub("close", "low"), sub("high", "close")),
                    sub("high", "low"),
                ),
                "vol",
            )), subindustry), subindustry))),
            scale(neutralize(sub(
                correlation("close", rank(adv20), 5),
                rank(ts_argmin("close", 30)),
            ), subindustry)),
        ),
        div("vol", adv20),
    ))''',
    101: '''div(sub("close", "open"), add(sub("high", "low"), 0.001))''',
}


def alpha101_name(number: int) -> str:
    """Return the stable output name for one paper formula."""
    if number not in ALPHA101_EXPRESSIONS:
        raise KeyError(f"Alpha101 formula is not defined: {number}")
    return f"alpha_{number:03d}"


def alpha101_project_title(number: int) -> str:
    """Return the title used by the independent Arena test project."""
    alpha101_name(number)
    return f"Alpha101 #{number:03d}"


def alpha101_python_source(number: int, *, negate: bool = False) -> str:
    """Build one independently executable Python DSL program."""
    name = alpha101_name(number)
    expression = ALPHA101_EXPRESSIONS[number]
    if negate:
        factor_definition = f'''DIRECT.unary.neg(
    "{name}",
    col=DIRECT.unary.cast(
        col={expression},
        dtype="double",
    ),
)'''
    else:
        factor_definition = f'''DIRECT.unary.cast(
    "{name}",
    col={expression},
    dtype="double",
)'''
    return f'''{ALPHA101_PRELUDE}

alpha = {factor_definition}

FACTORS = []
DERIVATIVES = [alpha]
FILTERS = [tradable]
'''


def compile_alpha101(number: int, *, negate: bool = False) -> dict[str, object]:
    """Compile one case with the managed stock-pool dependency available."""
    return compile_python_dsl(
        alpha101_python_source(number, negate=negate),
        external_derivatives=FACTOR_STOCK_POOL_VALIDATION_DERIVATIVES,
    )


def _return_node(index: int) -> dict[str, object]:
    return {
        "type": "DIRECT",
        "op": "unary.log",
        "fields": {
            "col": {
                "type": "DIRECT",
                "op": "binary.div",
                "fields": {
                    "left": {
                        "type": "TS",
                        "op": "unary.shift",
                        "fields": {"col": "close_hfq"},
                        "params": {"periods": -(index + 1)},
                        "on": None,
                    },
                    "right": {
                        "type": "TS",
                        "op": "unary.shift",
                        "fields": {"col": "close_hfq"},
                        "params": {"periods": -index},
                        "on": None,
                    },
                },
                "params": {},
            },
        },
        "params": {},
    }


def project_7_v2_payload(
    number: int,
    *,
    negate: bool | None = None,
) -> dict[str, object]:
    """Return project 7 v2 parameters with its persisted factor orientation."""
    if negate is None:
        negate = number in PROJECT_7_V2_NEGATED_NUMBERS
    name = alpha101_name(number)
    source = alpha101_python_source(number, negate=negate)
    document = compile_alpha101(number, negate=negate)
    return_derivatives = {
        column: _return_node(index)
        for index, column in enumerate(PROJECT_7_V2_RETURN_COLUMNS)
    }
    member = {
        "type": "DIRECT",
        "op": "binary.gt",
        "fields": {"left": "weight_000300SH", "right": 0},
        "params": {},
    }
    return {
        "n_groups": 10,
        "n_select": 10,
        "preprocess": True,
        "codes_query": {
            "start_date": PROJECT_7_V2_START_DATE,
            "end_date": PROJECT_7_V2_END_DATE,
            "lookback": "PT0S",
            "codes": [],
            "factors": [],
            "derivatives": {"stock_pool_member": member},
            "filters": ["stock_pool_member"],
        },
        "dataset_query": {
            "start_date": PROJECT_7_V2_START_DATE,
            "end_date": PROJECT_7_V2_END_DATE,
            "lookback": PROJECT_7_V2_LOOKBACK,
            "codes": [],
            "factors": ["circ_mv", "industry_l0"],
            "derivatives": {
                **return_derivatives,
                **document["derivatives"],
            },
            "filters": document["filters"],
            "dsl_source": {
                "language": "python",
                "json_source": json.dumps(document, ensure_ascii=False, indent=2),
                "python_source": source,
            },
        },
        "factor_columns": [name],
        "return_columns": list(PROJECT_7_V2_RETURN_COLUMNS),
        "return_specs": {
            column: {"kind": "log", "periods": 1}
            for column in PROJECT_7_V2_RETURN_COLUMNS
        },
        "industry_column": "industry_l0",
        "market_value_column": "circ_mv",
    }
