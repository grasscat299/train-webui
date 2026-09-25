from train_webui.backend.decimate import decimate_minmax, decimate_stride


def _points(ys):
    return [(i + 1, float(i), float(y)) for i, y in enumerate(ys)]


def test_under_limit_returns_all():
    pts = _points([1, 2, 3])
    assert decimate_minmax(pts, 10) == pts
    assert decimate_stride(pts, 10) == pts


def test_minmax_keeps_spike_first_last_and_order():
    ys = [0.0] * 1000
    ys[537] = 100.0  # 一瞬のスパイク
    ys[821] = -50.0
    pts = _points(ys)
    out = decimate_minmax(pts, 20)
    assert len(out) <= 20
    assert out[0] == pts[0] and out[-1] == pts[-1]
    assert pts[537] in out and pts[821] in out
    ids = [p[0] for p in out]
    assert ids == sorted(ids)
    assert set(out) <= set(pts)  # 新しい点を作らない


def test_minmax_respects_limit_on_varied_data():
    pts = _points([(i * 7919) % 101 for i in range(5000)])
    for m in (4, 5, 100, 1000):
        out = decimate_minmax(pts, m)
        assert len(out) <= m
        assert out[0] == pts[0] and out[-1] == pts[-1]


def test_stride_picks_evenly_spaced():
    pts = _points(range(100))
    out = decimate_stride(pts, 10)
    assert out == [pts[i * 10] for i in range(10)]
