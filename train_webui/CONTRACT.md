# train_webui 実装の取り決め（CONTRACT）

`design.md` を実装に落とすための、担当間の境界の取り決め。**各担当はここに書かれたインターフェースに
合わせて実装し、勝手に変えない**。変更が必要だと判断した場合は変更せず、報告に「CONTRACT変更提案」
として書くこと。仕様の背景・理由は `design.md` を参照。

## 0. ディレクトリ構成と担当

```
train_webui/
  design.md, CONTRACT.md                 … 仕様（変更しない）
  server.example.toml, requirements.txt  … 共通（変更しない）
  __init__.py                            … 共通（空）
  client/            … 担当A  Pythonクライアント（学習用PCで動く。標準ライブラリのみ）
    tests/
  backend/
    __init__.py, settings.py             … 共通（変更しない）
    remote/types.py                      … 共通（B・Cの境界。変更しない）
    remote/（types.py以外）              … 担当C  学習用PC操作（rsync / ssh）
    remote/tests/
    （remote/以外すべて）                 … 担当B  FastAPI・DB・project検出・ジョブ管理
    tests/
  frontend/          … 担当D  React（Vite + TypeScript）
```

- Python は 3.10（`tomllib` は無いので TOML は `tomli`）。依存は `requirements.txt` のもの以外を増やさない。
- テストは pytest。リポジトリ直下（worktree直下）で
  `/home/user/td_plan/venv/bin/python -m pytest train_webui/<担当dir>` で走ること。
- `train_webui` は `python -m train_webui.backend` のようにリポジトリ直下を起点にパッケージとして import する。

## 1. 用語・ID

- **project**: リポジトリ直下からのディレクトリパス（例 `src/world_model_v1`）。モジュールパスは `/`→`.`。
- **group**: そのrunの `config_toml` の `[wandb].group` の値。
- **run_id**: `YYYYMMDD_HHMMSS`（ローカル時刻）。同一秒に衝突したら `_2`, `_3` … を付ける。
- **job**: 学習用PCで動くプロセス1つ。`kind` は `train` か `infer`。
  `job_id` = `<run_id>__<kind>__<HHMMSS>`（衝突したら同様に `_2` …）。
  学習用PC上の `job_dir` = `<run_root>/<run_id>/<job_id>`。
- **status**（run）: `draft` / `running` / `finished` / `failed` / `stopped` / `lost`
- **status**（job）: `running` / `finished` / `failed` / `stopped`
- **origin**（run）: `webui`（WebUIの「学習開始」）/ `cli`（clientの `init(new_run=True)`）
- 日時はすべて ISO 8601 文字列（ローカルタイムゾーン付き、例 `2026-09-25T14:30:12+09:00`）。

## 2. HTTP API（backend＝担当B が実装、client＝A と frontend＝D が利用）

- ベースパスは `/api`。リクエスト・レスポンスは JSON（UTF-8）。
- エラーは `{"detail": "<日本語メッセージ>"}` と下記のステータスコード。
  400 入力不正（TOMLパースエラー等）/ 404 無い / 409 状態が許さない（GPU使用中・draft以外の編集等）。

### 2.1 共通オブジェクト

```jsonc
// RunSummary
{"run_id": "20260925_143012", "project": "src/world_model_v1", "group": "wm_v1_d128_L6",
 "origin": "webui", "status": "running",
 "created_at": "...", "started_at": "..." | null, "ended_at": "..." | null}

// Job
{"job_id": "20260925_143012__infer__150102", "run_id": "...", "kind": "infer",
 "status": "running", "started_at": "...", "ended_at": null, "exit_code": null, "error": null}

// Run（詳細） = RunSummary + 以下
{"config_toml": "...", "git_commit": "..." | null, "git_diff": "..." | null,
 "git_untracked": "{...json...}" | null, "git_info_source": "sync" | "last_sync" | null,
 "hostname": "..." | null, "pid": 1234 | null, "last_heartbeat_at": "..." | null,
 "exit_code": 0 | null, "error": "traceback等" | null,
 "jobs": [Job, ...]}          // 古い順

// Result（metric）
{"id": 1, "run_id": "...", "kind": "metric", "label": "train/loss", "step": 100,
 "value": 0.123, "source": "train", "sent_at": "..."}
// Result（image）
{"id": 2, "run_id": "...", "kind": "image", "label": "infer/xy", "step": null,
 "image_url": "/media/images/<run_id>/<id>.png", "source": "infer", "sent_at": "..."}

// Plot（軸つきグラフ、design.md §5.3）: 1ラベル分。series は最初に現れた順、points は id 順（=送信順）
{"run_id": "...", "label": "infer/xy", "kind": "line" | "scatter", "defined": true,
 "title": "推論結果", "xlabel": "x", "ylabel": "y", "xscale": "linear" | "log", "yscale": "linear" | "log",
 "xlim": [0.0, 1.0] | null, "ylim": null, "updated_at": "...",
 "series": [{"name": "pred", "points": [[x, y], ...],
             "total_points": 52000,      // この系列のDB上の全点数
             "downsampled": true}, ...], // true なら points は間引き済みの系列全体（差分ではない）
 "last_point_id": 123 | null}   // このラベルのDB上の最大の点id（点が無ければ null）。間引きで返していない点も含む
```

### 2.2 frontend 向け

| メソッド・パス | 内容 |
|---|---|
| `GET /api/tree` | `[{"project": str, "groups": [{"group": str, "runs": [RunSummary]}]}]`。検出した全project（runが無いものも `groups: []` で含む）。projectはパス順、groupは名前順、runは `created_at` 新しい順 |
| `POST /api/rescan` | project検出をやり直す。`GET /api/tree` と同じ形を返す |
| `GET /api/state` | `{"busy": bool, "active": {"run_id", "job_id" \| null, "kind", "origin"} \| null}`。GPU使用中か（webuiのjobが running、または cli の run が running） |
| `POST /api/runs/draft` | body `{"project": str, "group": str, "new_group": bool}`。§3（design.md）の規則で初期configを決めて draft を作る。`new_group=true` なら `[wandb].group` の値を `group` に書き換える。返り値 Run |
| `GET /api/runs/{run_id}` | Run |
| `PUT /api/runs/{run_id}/config` | body `{"config_toml": str}`。draftのみ（それ以外409）。TOMLとして不正なら400。`[wandb].group` が変わっていればgroupも更新。返り値 Run |
| `POST /api/runs/{run_id}/start` | draft を起動（コード同期→config書き込み→trainジョブ起動）。busyなら409。返り値 Run（同期・起動は裏で進み、失敗したら run は failed・`error` に理由） |
| `POST /api/runs/{run_id}/stop` | そのrunで running のジョブ（train か infer）を停止。無ければ409。返り値 Run |
| `DELETE /api/runs/{run_id}` | draft または終了済みrunを結果ごと削除。running なら409。204 |
| `POST /api/groups/infer` | body `{"project": str, "group": str}`。そのgroupで最後に起動された train の run（draft以外、origin不問）を対象に infer ジョブを起動。対象runが無ければ404、busyなら409。返り値 Job |
| `GET /api/jobs/{job_id}/log?offset=N` | `{"data": str, "offset": int, "done": bool}`。ローカルに取り込み済みのログの offset バイト目以降（UTF-8デコード、不正バイトは置換）。`done` はジョブが終了済みかつ全文取り込み済み |
| `GET /api/runs/{run_id}/results?kind=&label=` | `[Result]`（`sent_at` 古い順）。kind/label は任意の絞り込み |
| `GET /api/labels?run_ids=a,b,c` | `[{"label": str, "kind": "metric" \| "image" \| "plot"}]`（指定runに現れるラベルの和集合、label順。plot は `plots` テーブルから） |
| `GET /api/runs/{run_id}/plots?label=&after_id=&max_points=` | `[Plot]`（label順）。label は任意の絞り込み。`after_id` を付けると id がそれより大きい点だけを `points` に入れる（軸の定義は毎回全部返す）。差分で点が無い系列は `points: []` で返す。`max_points`（既定1000、1系列あたり）を超える系列は `after_id` を無視して**間引き済みの全体**を返し `downsampled: true`（下記）。runが無ければ404 |
| `GET /media/...` | 画像ファイル（静的配信） |

- frontend は `/api/tree` `/api/state` を 2〜3 秒ごと、表示中の running run の results とログを
  `poll_interval_sec` 程度でポーリングする（WebSocket/SSE は使わない）。
- backend は `train_webui/frontend/dist/` が存在すればそれを `/` で静的配信する（本番用）。
  開発時は Vite の dev server から `/api` と `/media` を backend（`http://127.0.0.1:8000`）へ proxy する。

### 2.3 client 向け

| メソッド・パス | 内容 |
|---|---|
| `POST /api/runs` | body `{"project": str, "group": str, "config_toml": str, "hostname": str, "pid": int}`。origin=cli の run を running で作る（git情報は最後の sync のものを `git_info_source="last_sync"` で記録）。返り値 `{"run_id": str, "warning": str \| null}`（GPU使用中なら warning に理由） |
| `GET /api/runs/resolve?project=&group=` | `{"run_id": str}`。そのgroupで最後に起動された train の run（draft以外、origin不問）。無ければ404 |
| `POST /api/runs/{run_id}/heartbeat` | origin=cli の run の `last_heartbeat_at` を更新（lost なら running に戻す）。204 |
| `POST /api/runs/{run_id}/finish` | body `{"status": "finished" \| "failed", "error": str \| null}`。origin=cli なら status と ended_at を更新。origin=webui なら status は変えず、`error` があれば記録だけする。204 |
| `POST /api/results` | body `{"run_id": str, "source": str, "items": [Item, ...]}`。204。run が無ければ404 |

```jsonc
// Item（metric）
{"kind": "metric", "label": "train/loss", "step": 100, "value": 0.123, "sent_at": "..."}
// Item（image）: PNG を base64 で埋め込む（multipart は使わない）
{"kind": "image", "label": "infer/xy", "step": null, "image_b64": "iVBORw0...", "sent_at": "..."}
// Item（plot_def）: 軸の定義。同じ (run_id, label) があれば上書き（点は残す）
{"kind": "plot_def", "label": "infer/xy", "plot_kind": "line" | "scatter", "title": str | null,
 "xlabel": str, "ylabel": str, "xscale": "linear" | "log", "yscale": "linear" | "log",
 "xlim": [a, b] | null, "ylim": [a, b] | null, "sent_at": "..."}
// Item（plot_points）: 点の追加。xs と ys は同じ長さ（1点でも配列）
{"kind": "plot_points", "label": "infer/xy", "series": "pred", "xs": [0.1, 0.2], "ys": [1.0, 0.8],
 "step": null, "sent_at": "..."}
```

- plot_points の label に plot_def がまだ無ければ、既定の定義（`plot_kind="line"`, title=null,
  xlabel/ylabel=`""`, linear, lim=null, `defined=0`）で `plots` 行を作る。後から plot_def が来たら上書きして `defined=1`。
- 同じ run で、plot のラベルが metric/image として既に使われている（またはその逆）なら 400。
- xs と ys の長さ不一致・非有限値は 400（client 側で弾いている前提の保険）。
- 1リクエスト内の items は送られた順に処理する（同じバッチ内の plot_def → plot_points の順序を保つ）。

## 3. backend 内部の境界（担当B ⇔ 担当C）

`train_webui/backend/remote/types.py` に定義済み。要点：

- 担当C は `train_webui/backend/remote/controller.py` に
  `class RemoteController(RemoteControllerProtocol)` を実装し、`remote/__init__.py` から export する。
  コンストラクタは `RemoteController(settings: Settings, runner: CommandRunner | None = None)`
  （`None` なら `subprocess.run` を使う実装を内部で用意）。
- 各メソッドは**同期・ブロッキング**。担当B は FastAPI から呼ぶ際にスレッド（`run_in_threadpool` 等）で呼ぶ。
- 失敗（ssh/rsync の非0終了・タイムアウト）は `RemoteError(Exception)`（`remote/errors.py`、担当C）を送出。
  メッセージは原因が分かる日本語＋コマンドのstderr末尾。担当B は `from .remote.errors import RemoteError`
  を前提にしてよい（Bのテストでは自前の偽 RemoteController を使う）。
- **起動コマンド・環境変数**は design.md §4 のとおり。起動時に渡す環境変数：
  `PYTHONUNBUFFERED=1`, `TRAIN_WEBUI_URL=<server.public_url>`, `TRAIN_WEBUI_RUN_ID`,
  `TRAIN_WEBUI_PROJECT`, `TRAIN_WEBUI_SOURCE`（= kind）。
- ssh は `ssh -p <port> -i <key_path> -o BatchMode=yes <user>@<host> <command>`、
  rsync は `rsync -az --delete -e "ssh -p <port> -i <key_path> -o BatchMode=yes" --exclude ... <repo_root>/ <user>@<host>:<repo_dir>/`。
  リモートに渡す文字列は `shlex.quote` で必ずクォートする。
- `config.toml` の書き込みは `ssh ... "cat > <repo_dir>/<project_dir>/config.toml"` に stdin で内容を流す。

### ジョブ管理（担当B）

- 担当B は `JobManager`（名前は任意）で、起動フロー（sync_code → launch → DB更新）と、
  バックグラウンドのポーリングループを持つ。ループは `poll_interval_sec` ごとに：
  - running のジョブについて `read_log` で差分を取り、ローカルの `<media_dir>/logs/<job_id>.log` に追記、
    `poll` で終了判定（exit_code 0 → finished、非0 / None → failed、WebUIから停止したものは stopped）。
    train ジョブの終了に合わせて run の status・ended_at・exit_code も更新する。
  - origin=cli の running run で `last_heartbeat_at` が `lost_after_sec` より古いものを lost にする。
- server 起動時、DB上で running のジョブがあれば同じループで確認し直される（特別な処理は不要）。
- 同時に動かせるジョブはシステム全体で1つ（`GET /api/state` の busy と同じ判定で 409 を返す）。

## 4. DB（sqlite、担当B）

design.md §6 のテーブル（`runs` / `jobs` / `results` / `syncs`）。ORMは使わず標準の `sqlite3` で書く。
画像は `<media_dir>/images/<run_id>/<result_id>.png` に保存し、`results.image_path` に `media_dir` からの
相対パスを入れる。run 削除時は画像・ログファイルも消す。

軸つきグラフは `plots` / `plot_points` テーブル（design.md §6）。スキーマ：

```sql
CREATE TABLE IF NOT EXISTS plots (
    run_id TEXT NOT NULL, label TEXT NOT NULL,
    kind TEXT NOT NULL, title TEXT, xlabel TEXT NOT NULL, ylabel TEXT NOT NULL,
    xscale TEXT NOT NULL, yscale TEXT NOT NULL,
    xmin REAL, xmax REAL, ymin REAL, ymax REAL,
    defined INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label)
);
CREATE TABLE IF NOT EXISTS plot_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL, label TEXT NOT NULL, series TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL, step INTEGER,
    source TEXT NOT NULL, sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plot_points_run_label ON plot_points(run_id, label, id);
```

run 削除時は `plots` / `plot_points` の該当行も消す。

### 軸つきグラフの間引き（design.md §9）

`GET /api/runs/{run_id}/plots` で、系列の全点数 `n` が `max_points` を超えたら、その系列の全点を id 順に
読んで以下で間引き、`downsampled: true` で返す（`n <= max_points` なら今まで通り、`downsampled: false`）。
間引きは `train_webui/backend/decimate.py` に純粋関数として置き、単体テストを書く。

- line: `decimate_minmax(points, max_points)`。id 順の点列を `(max_points - 2) // 2` 個の区間に等分し、各区間で
  y 最小・y 最大の点を元の順序で残す（同じ点なら1つ）。最初と最後の点は必ず含める（その2点ぶん区間を減らす）。
  結果は `max_points` 点以下。`max_points` は4以上（API は4未満を422にする）。
- scatter: `decimate_stride(points, max_points)`。`i * n // max_points`（i = 0..max_points-1）番目の点を残す。
- どちらも入力は `(id, x, y)` の列、出力はその部分列（新しい点を作らない）。
- `GET /api/runs/{run_id}/plots` はリクエスト開始時点のその run の最大の点idを取り、それ以下の点だけを
  返す（`total_points`・`last_point_id` も同様）。読んでいる途中に追加された点を `last_point_id` に含めると、
  次回の差分取得で漏れるため。frontend は全ラベルの `last_point_id` の最大値を次回の `after_id` にする。

## 5. client（担当A）

design.md §5 のとおり。追加の取り決め：

- 公開APIは `train_webui/client/__init__.py` から `init, log, log_image, define_plot, log_xy, finish` を関数として、
  `Plot` をクラスとして export
  （モジュールレベルの状態を1つ持つ、wandb と同じ書き味）。`init` 前に `log` 等が呼ばれたら warning で無視。
- 送信は daemon スレッド＋キュー。`log` はキューに積むだけで即 return。まとめて `POST /api/results`
  （例: 0.5秒ごと or 50件ごと）。送信タイムアウトは短く（例 5秒）、失敗は warning を出して捨てる。
- `finish()` はキューを吐き出して（最大数秒待つ）スレッドを止める。自分で作った run（new_run経由）なら
  `POST /api/runs/{id}/finish` を送る。`atexit` で `finish()` 未呼び出しなら自動で呼ぶ。
- 自分で作った run の場合だけ、30秒ごとに heartbeat を送り、未処理例外時（`sys.excepthook` をチェーン）は
  traceback を付けて status=failed で finish を送る。
- `log_image` は matplotlib の Figure（`savefig` で PNG 化）か画像ファイルパスを受け付ける。matplotlib は
  import しない（Figure は duck typing で `savefig` を呼ぶ）。
- `define_plot(label, kind="line", title=None, xlabel="", ylabel="", xscale="linear", yscale="linear",
  xlim=None, ylim=None) -> Plot`（design.md §5.3）。引数の値が不正（kind/scale が候補外、lim が2要素の
  数値でない）なら warning を出して送らない。init 前・送信無効でも `Plot` は返す（`add` が黙って無視されるだけ）。
- `log_xy(label, x, y, series="default", step=None)`。x/y はスカラーか配列（`.tolist()` があれば呼ぶ）。
  各要素を `float()` し、長さ不一致・変換失敗は warning を出して呼び出しごと捨てる。NaN/inf の点は
  その点だけ捨て、捨てた件数を warning に出す。残りが0点なら送らない。numpy/torch は import しない。
- `Plot` は `label` を持つだけ。`Plot.add(x, y, series="default", step=None)` は `log_xy(self.label, ...)`。
- project ディレクトリの推定（CLI起動時）: `init` を呼んだファイル（`inspect` で呼び出し元）から親を辿り、
  `main.py` と `config.toml` が両方ある最初のディレクトリ。リポジトリ直下からの相対パスにする
  （リポジトリ直下 = `train_webui` パッケージの親）。
- warning は `warnings.warn` ではなく `print(..., file=sys.stderr)` で `[train_webui] ` 接頭辞付き
  （学習ログに混ざって見えるように）。

## 6. frontend（担当D）

design.md §7〜9 のとおり。追加の取り決め：

- `train_webui/frontend/` に Vite + React + TypeScript。グラフは Recharts。状態管理は React 標準
  （useState/useContext 等）で十分、追加ライブラリは最小限。
- API 呼び出しは `src/api.ts` に集約し、§2.2 の型を TypeScript で定義する。
- backend が無くても開発・確認できるよう、`VITE_MOCK=1` で `src/api.ts` がモックデータを返すモードを持つ。
- config エディタは textarea（等幅）で十分。TOML の検証は backend（400）に任せ、エラーを表示する。
- 軸つきグラフは `src/components/PlotChart.tsx` に置き、Recharts の `ComposedChart` ＋ 数値の `XAxis`/`YAxis`
  （`type="number"`、`scale="log"`/`"linear"`、lim は `domain`、未指定は `['auto','auto']`）で描く。
  line は系列ごとに `Line`（`data` にその系列の点を送信順で渡す、`dot={false}`）、scatter は `Scatter`。
  - ResultPanel で `kind === 'plot'` のラベルは PlotChart に比較対象run全部の Plot を渡して重ね描きする
    （design.md §9 の色・凡例・定義の選び方・log軸の0以下の点の扱い）。
  - 取得は run ごとに `GET /api/runs/{id}/plots`。2回目以降は `after_id=<前回のlast_point_id>` で差分を取り、
    手元の点に追記する。比較対象runが変わったら取り直す。
  - 系列が `downsampled: true` で返ってきたら、手元のその系列を**置き換える**（追記しない）。
    その系列には「間引き表示中（元 {total_points} 点 → 表示 {points.length} 点）」を表示する。
  - `VITE_MOCK=1` のモックモードにも Plot のモックデータ（line と scatter を1つずつ）を足す。
- 見た目は簡素でよいが、左ツリー・タブ・状態表示が一目で分かること。
