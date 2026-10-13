"""軸つきグラフ（line）の「x を一定の幅ごとにまとめて平均する」表示用の集計（design.md §9）。

間引き（decimate.py）は表示用に点を減らすだけだが、こちらは全点から平均を作る。
step ごとの loss を epoch ごとに平均する、といった用途。
"""
from __future__ import annotations

import math

Point = tuple[int, float, float]


def bin_mean(points: list[Point], width: float) -> list[tuple[int, float, int]]:
    """x を width ごとの区間 [k*width, (k+1)*width) に分け、区間ごとの y の平均を返す。

    戻り値は (k, 平均, 点数) の列（k の昇順）。点が1つも無い区間は出さない。
    最後の区間は、まだ途中（学習中の epoch など）でも、そこまでの点の平均を出す。
    """
    if not width > 0:
        raise ValueError("width は正の数にしてください")
    sums: dict[int, list[float]] = {}  # k -> [合計, 点数]
    for _id, x, y in points:
        k = math.floor(x / width)
        acc = sums.setdefault(k, [0.0, 0])
        acc[0] += y
        acc[1] += 1
    return [(k, s / n, n) for k, (s, n) in sorted(sums.items())]
