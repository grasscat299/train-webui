"""軸つきグラフの点の間引き（design.md §9 / CONTRACT.md §4）。

入力は id 順の `(id, x, y)` の列、出力はその部分列（新しい点は作らない）。
"""
from __future__ import annotations

Point = tuple[int, float, float]


def decimate_minmax(points: list[Point], max_points: int) -> list[Point]:
    """折れ線用。点列を (max_points - 2) // 2 個の区間に等分し、各区間で y 最小・最大の点を元の順序で残す。

    最初と最後の点は必ず含める（その2点ぶん区間数を減らして、結果を max_points 点以下に収める）。
    max_points は 4 以上を前提とする。
    """
    n = len(points)
    if n <= max_points:
        return list(points)
    n_buckets = (max_points - 2) // 2
    keep: set[int] = {0, n - 1}
    for b in range(n_buckets):
        start = b * n // n_buckets
        end = (b + 1) * n // n_buckets
        bucket = range(start, end)
        keep.add(min(bucket, key=lambda i: points[i][2]))
        keep.add(max(bucket, key=lambda i: points[i][2]))
    return [points[i] for i in sorted(keep)]


def decimate_stride(points: list[Point], max_points: int) -> list[Point]:
    """散布図用。i * n // max_points（i = 0..max_points-1）番目の点を残す。"""
    n = len(points)
    if n <= max_points:
        return list(points)
    return [points[i * n // max_points] for i in range(max_points)]
