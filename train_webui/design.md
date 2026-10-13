# 学習管理WebUI 設計

## 0. 背景・目的

- 組織のGPU学習用PC（sshで到達可能、コード実行はsingularityコンテナ内限定）で、
  `main.py` + `config.toml` 形式の各プロジェクト（`src/world_model_v1` / `src/world_model_v2` /
  `src/enemy_world_model` / 将来的に規約を揃える `scratch/*` 等）を、`td_plan` リポジトリ内から
  横断的に検出してブラウザから起動・停止・監視したい。
- 本リポジトリの規約（`main.py` の `--mode {train,infer}` CLI、`config.toml` の
  `[wandb] project/group`、`checkpoints/model_{group}.pt`、`samples/*.png`）は変更せず、
  WebUIはこれらを外側から検出・操作するだけの薄い層として作る（`train_webui/` 配下、
  `src/` の外・リポジトリ直下に置く）。`[wandb]` というセクション名・`project`/`group`という
  キー名はそのまま維持する（wandbは今後また使う可能性があるため、名前だけの移行はしない）。
- **wandbから完全に乗り換える**（オフライン対応のための代替ではなく、実験管理そのものをこのWebUIに
  一本化する）。全プロジェクトの `wandb.init/log/finish` 呼び出しは §5 のクライアントに置き換え、
  `wandb` パッケージ自体（および各 `main.py` の `_WANDB_AVAILABLE` フォールバック分岐）は不要になる。
- **既存プロジェクトの移行（wandb→client置き換え、`world_model_v1` の ddpo/eval の train への統合等）は
  今回のスコープ外**。WebUI本体を作り、学習プロジェクトのフォーマットを修正して確定させてから行う。

## 1. 全体構成

```
[ローカルPC (WSL2)]                    [学習用PC の singularity instance（--nv）]
 WebUI backend      --rsync/ssh-->      sshd（一般ユーザー, 2222番）  ※§1.6
   - project検出      (tailscale)       リポジトリ(コード同期先)
   - config編集・run管理
   - ジョブ起動/停止/終了検知(ssh)
   - run.logのtail(ssh pull)  --ssh-->  run.log
   - /api/results (受信)   <--http--    main.py / CLIスクリプト
                        (tailscale)     (clientでメトリクス・画像をpush)
 WebUI frontend (React)
   - project/group/runツリー
   - Train / Result タブ
```

- **技術スタック**: frontend は React（Node.js / Vite でビルド）。backend は Python + FastAPI
  （`main.py` のAST解析・TOML読み込みが標準ライブラリで書け、clientとも言語を揃えられるため）。
  DBは sqlite。
  SSH・rsync は `~/.ssh/config` や公開鍵設定をそのまま使えるよう、ライブラリではなく `ssh` / `rsync`
  コマンドをサブプロセスで呼ぶ。
- 学習用PC側に常駐エージェントは置かない。ジョブ起動のたびにSSH接続し、`nohup` + `setsid` で
  SSHセッションから切り離して実行することで、WebUI側の接続が切れてもジョブは継続する。
- **ネットワーク**: ローカルPCはWSL2（NATモード）だが、WSL上でtailscaleを張っているため、学習用PCから
  WSL上のserverへtailscaleのIPで直接到達できる。`server.toml` の `public_url` はtailscaleのIPを書く。
- **認証は無し**（決定事項）。代わりに**VPN（tailscale）内からのみアクセスできる**ようにする：全リクエストの
  接続元IPを確認し、`server.toml` の `allowed_networks`（既定: tailscaleの `100.64.0.0/10`・
  `fd7a:115c:a1e0::/48` と loopback）に入っていなければ403を返す。listenアドレスは `0.0.0.0` のまま
  （tailscale IPだけにbindすると、Windows側ブラウザから `localhost` で開けなくなるため）。

## 1.5 server設定ファイル（接続先などはコードに書かず外出し）

- 学習用PCへの接続情報や実行環境のパスは、WebUI server用の設定ファイル
  `train_webui/server.toml` に書く。リポジトリの `config.toml` 規約と同様に、
  `server.example.toml` をgit管理し、実値の `server.toml` はgit管理しない（`.gitignore`）。
- SSHは公開鍵認証が設定済みの前提。パスワード等の秘密情報はこのファイルに書かない（鍵パスのみ）。

```toml
[remote]                       # 学習用PCの singularity instance 内の sshd（§1.6）
host      = "100.x.x.x"        # instance の tailscale の IP
user      = "student"
port      = 2222
key_path  = "~/.ssh/id_ed25519"
repo_dir  = "/home/user/td_plan"          # 以下のパスはコンテナの中から見たパス
python    = "python3"                         # コンテナ内のpython
run_root  = "/home/user/train_webui_runs"  # run.log / run.pid / run.exit を置く場所（repo_dirの外）

[server]                       # ローカルPCのWebUI server
host        = "0.0.0.0"        # listenアドレス
port        = 8000
public_url  = "http://100.x.x.x:8000"  # 学習用PCから見たこのserverのURL（tailscaleのIP）
db_path     = "train_webui/data/webui.sqlite"
media_dir   = "train_webui/data/media"   # 受信した画像・終了後のrun.logの保存先

[log]
poll_interval_sec = 1.5        # run.log の SSH pull 間隔 / プロセス生存確認の間隔
```

## 1.6 学習用PC側の構成（singularity instance 内で完結させる）

学習用PC本体（コンテナの外）の環境は一切変更しない。sshd・tailscale・rsync・python などはすべて
singularity の sandbox（書き込み可能なディレクトリ形式のイメージ）に入れ、instance として起動する。
WebUI の ssh 接続先はこの instance 内の sshd なので、起動コマンドは singularity で包まず、
コンテナ内の `python3` を直接実行する（§4）。

- **起動**: `singularity instance start --nv --containall --bind <学習用PCの作業dir>:/home/user <sandbox> train_webui`。
  sandbox の `/.singularity.d/startscript` で tailscaled と sshd を自動起動する（`shell`/`exec` では
  startscript は動かないので必ず instance で起動する）。
- **sshd**: 一般ユーザー権限で `Port 2222` / `ListenAddress 127.0.0.1`。設定・ホスト鍵・authorized_keys は
  sandbox 内の `/opt/sshd/` に置く。`SetEnv` で次を渡す：
  - `LD_LIBRARY_PATH=/.singularity.d/libs`（`--nv` のGPUライブラリ。ssh ログインには引き継がれないため）
  - `http_proxy=http://localhost:1055`（clientの送信を tailscale 経由にするため。下記）
  - `TMPDIR=/home/user/tmp`（`--containall` 時の `/tmp` は 16MB の tmpfs しかないため）
- **tailscale**: root も TUN も使わない userspace モード（`tailscaled --tun=userspace-networking`）。状態ファイル
  とソケットは `/home/user/.ts-train/` に置き、instance を起動し直しても再ログイン不要にする。
  - 外から来た接続（WSL → instance の tailscale IP:2222）は localhost に転送されるので、sshd は
    localhost だけで待ち受けてよい（学習用PCのLAN側には開かない）。
  - 外へ出る接続（client → WSL の WebUI server）はそのままでは tailnet に届かないので、tailscaled の
    HTTP proxy（`--outbound-http-proxy-listen=localhost:1055`）を経由させる。client は `urllib` なので
    `http_proxy` 環境変数だけで経由できる。
- instance は明示的に止めるか学習用PCが再起動するまで動き続ける。止まった場合は実行中のジョブも
  止まる（WebUI上は `run.exit` が無いので failed になる）。

## 2. プロジェクト検出

- 探索範囲は `td_plan` リポジトリ全体（`venv/` `.git/` `__pycache__/` `wandb/` `train_webui/` 自身
  などは除外）。`src/` に限定しない（`scratch/*` も対象になり得る）。
- **「project」として認識する条件**は次の2つを両方満たすディレクトリのみ：
  1. 直下に `main.py` があり、`argparse` で `--mode` を定義し `choices` が `{"train", "infer"}`
     である（**モードはこの2つに固定**。既存の `eval` / `ddpo` のような追加モードは持たせず、
     `config.toml` 側の値で train モードの中の挙動を切り替える形に寄せる。例えば現状
     `world_model_v1` にある `--mode ddpo` は「trainモード＋configで学習方式をddpoに切り替え」に
     移行する想定）
  2. 直下に `config.toml`（無ければ `config.example.toml`）がある
- 条件を満たさないディレクトリ（現時点の `scratch/DDPM` `scratch/MNIST` `scratch/VAE`
  `scratch/LatentDDPM` のような、定数直書き・config.tomlなしの旧形式や、現状の `world_model_v1`
  のようにmodeが3つ以上あるもの）は**対象外**。WebUI側には何も手を入れず、各プロジェクト側を
  この条件に合わせて書き換えれば次のスキャンから自動的に一覧に出てくる。
- 検出はリクエスト毎（or 明示的な「再スキャン」操作）に動的に行う。静的な登録リスト・設定ファイルは
  持たない。
- 各 `main.py` は **実行せず** AST解析し、`--mode` の `choices` を静的に確認する（`torch` 等の重い
  import やCUDA初期化を避けるため、importではなくソース解析で行う）。
- **project の識別子はリポジトリ直下からのディレクトリパス**（例: `src/world_model_v1`）。
  `[wandb].project` の値は表示にもキーにも使わない（ツリーの並びと一致させるため）。

## 2.5 プロジェクト > モデル（表示上の分類）

- 画面のツリーは `プロジェクト > モデル（§2 の project。`src/enemy_world_model` などのパスがそのままカテゴリー）
  > group > run` の順。groupまでを常に表示し、group内のrunは初期状態では畳む（groupの矢印で開閉）。
- 最上段のプロジェクトは `train_webui/categories.toml`（git管理）で決める。モデルのパスを `match` の glob に当て、
  上から順に最初に合致したプロジェクトに入れる。どれにも合致しなければ「未分類」。
- DB・CLIの引数は従来のまま。`/api/tree` の各要素に `workspace` が足されるだけ。`cli ls` は `[プロジェクト]` の見出しを出す。

## 3. config は run に1つ紐づく（config.tomlは「今開いている下書き」でしかない）

- **1 run = 1 config スナップショット**が原則。起動した run の config は読み取り専用。
  「編集」は常に「次に作る新しい run の下書きを編集する」という形になる。
- **新規run作成時の下書きの初期値**：
  - 既存groupに追記する場合 → そのgroup内で最新のrunのconfigをコピー
  - 新しいgroupを作る場合 → そのproject全体で直近に実行されたrun（groupを跨いだ直近1件）のconfig
    をコピー。group名（`[wandb].group`）だけ新規入力させる
  - そのprojectでまだ一度もrunが無い場合 → リポジトリ上の `config.toml`（無ければ
    `config.example.toml`）をそのままコピーして初期値にする
- 下書きはUI上のテキストエディタでTOMLとして直接編集する（保存時にパースしてエラーを弾く）。
- **draft はDBに保存**され、ツリーにも表示される（ブラウザを閉じても残る）。
- **group は group_id と group_name を持つ**。group_id は config の `[wandb].group` の値で、checkpoint 名
  （`model_{group_id}.pt`）にも使われるので一度決めたら変えない。group_name は画面（ツリー・凡例）に出す表示名で、
  ツリーの ✎ からいつでも編集できる（DB の `groups` テーブル。group_id は変わらない）。
  - 「+ 新規group」では名前を聞かず、group_id を `g<YYYYMMDD_HHMMSS>`、group_name を project 内の連番
    `group1`, `group2`, ... で自動で振る。
  - CLI の `init(new_run=True)` や config の書き換えで未登録の group_id が現れた場合は、group_name = group_id で登録する。
    既存DBの group も group_name = group_id で移行する。
- **groupは config の `[wandb].group` の値（= group_id）で決まる**。エディタでこの値を書き換えた場合は、保存時に
  その値のgroupへ分類し直す（存在しないgroup名なら新しいgroupになる）。
- 起動時、WebUI backendは①下書き内容をそのrunのconfigスナップショットとして確定し、②同じ内容を
  学習用PC上のプロジェクトディレクトリの `config.toml` に書き込んでから実行する。`config.toml`
  自体は「main.py が読みに行く固定パスの実体ファイル」という役割だけを持ち、履歴管理はWebUI側の
  スナップショットが正とする（システム全体で同時に1ジョブしか動かさない前提＝§4なので、実ファイル
  上書きで競合しない）。
- **run の削除**: draft と、終了済み（finished / failed / stopped）の run は削除できる（紐づく結果も
  削除）。running の run は削除できない。

## 4. ジョブ起動・停止・終了検知

「ジョブ」＝学習用PCで動くプロセス1つ。train（runを起動）と infer（`[infer]`ボタン）の2種類がある。

- projectのモジュールパスはディレクトリパスから機械的に導出する（`src/world_model_v1` →
  `src.world_model_v1`、`scratch/PolylineInterp` → `scratch.PolylineInterp`）。
- **起動手順**: コードをrsync（§4.5）→ SSHで `config.toml` を書き込み → 次を実行：

  ```sh
  cd <repo_dir> && mkdir -p <job_dir> && (
    setsid nohup sh -c '
      env PYTHONUNBUFFERED=1 TRAIN_WEBUI_URL=<public_url> TRAIN_WEBUI_RUN_ID=<run_id> \
          TRAIN_WEBUI_PROJECT=<project_dir> TRAIN_WEBUI_SOURCE=<train|infer> \
        <python> -m <module.path>.main --mode <mode>
      echo $? > <job_dir>/run.exit
    ' > <job_dir>/run.log 2>&1 < /dev/null &
    echo $! > <job_dir>/run.pid
  ) && cat <job_dir>/run.pid
  ```

  - 背景化する部分だけを括弧で囲む（`a && b && c &` と書くと `&` が連結全体にかかり、`mkdir` が
    終わる前に `run.pid` を書こうとする競合が起きる）。
  - `<job_dir>` = `<run_root>/<run_id>/<job_id>`（`job_id` の形式は CONTRACT.md §1）
  - `PYTHONUNBUFFERED=1` は必須（ログのリアルタイム性のため、下記コラム）。
  - `setsid` で新しいプロセスグループにする（停止時にグループごとkillするため）。
  - ssh 先がすでに instance 内（§1.6）なので singularity では包まない。
- **停止**: `run.pid` のプロセスグループ全体へ SIGTERM（`kill -TERM -<pgid>`）、一定時間で終わら
  なければ SIGKILL。python の子プロセス（DataLoader worker 等）まで確実に止めるため、PID単体
  ではなくグループに送る。
- **終了検知**: backend が `poll_interval_sec` ごとにSSHで `kill -0 <pid>` と `run.exit` の有無を
  確認する。プロセスが消えたら `run.exit` の終了コードで状態を決める（0 → finished、非0 → failed、
  WebUIから停止した場合は stopped）。
- **server再起動時の復帰**: DB上で running のジョブがあれば、起動時に同じ方法で生存確認し直し、
  終了していれば状態を更新する。
- **排他制御: システム全体で同時に実行できるジョブは1つまで**（train/inferを問わず、projectを
  またいでも1つ。GPUを1台で共有する前提）。ジョブ実行中は全project・全groupの「学習開始」
  「`[infer]`」ボタンを無効化する。CLIから起動した学習run（§5.2）が running の間も同様に無効化する。
  CLIから手動実行した可視化スクリプト（既存runへの追加）はserverからは実行中か分からないので
  排他の対象外（ユーザーの責任で管理）。
- **他の人のGPU使用の確認（起動前）**: 学習用PCは他の人と共有しているため、「学習開始」「`[infer]`」を
  押したら rsync の前に SSH で GPU の使用状況を確認し、他の人が使っていれば起動しない（409、draft は draft のまま）。
  - instance は `--containall` で PID 名前空間が分かれていて、`ps` では他の人のプロセスは見えない。一方
    `nvidia-smi` のメモリ使用量は GPU 全体の値なので、「GPU全体の使用メモリ − `ps` にも見える（= 自分たちの）
    プロセスの使用メモリ（`--query-compute-apps`）」を他の人の使用量とみなす。
  - これが `server.toml` の `[gpu] busy_threshold_mib`（既定 1024）を超えていたら使用中と判定する（何も動いて
    いない時でも数十MiBは使われているため、0ではなくしきい値で見る）。使用率は瞬間値でぶれるので判定に使わない。
  - SSH・nvidia-smi の失敗で確認できなかった場合も起動しない（502）。
- **起動失敗**: rsync・SSH・プロセス起動のいずれかで失敗した場合、そのジョブは failed とし、
  エラーメッセージを表示する。
- **ログ（`run.log`）はSSH pull、メトリクス/画像はHTTP push、と役割を分ける**（下記コラム）。
- **終了後のログ保存**: ジョブ終了時に `run.log` 全体をローカル（`media_dir`）に取り込み、終了後も
  WebUIから見られるようにする（クラッシュ原因の調査用）。

> **なぜログはpush(HTTP)にしないか**: `/api/results`へのpushは`client.log(...)`を明示的に呼んだ
> タイミングでしか飛ばない構造化データ用のチャネルで、SSHで直接コマンドを叩いた時のような
> 「その場で全部見える」即時性は無い（クラッシュ時のtracebackのような、明示的にpushしていない
> 出力は永遠に届かない）。標準出力の即時性が欲しい`run.log`は、起動・停止で使っているのと同じ
> SSH接続を使って**pull**する：WebUI backendが `ssh <host> tail -c +<前回位置> <job_dir>/run.log`
> のような差分取得を1〜2秒間隔でポーリングし、フロントにストリーム表示する（`tail -f`を張りっぱなしに
> するより、切断・再接続に強い）。`nohup ... > run.log 2>&1` はデフォルトでブロックバッファリング
> される（ttyでないため）ため、`PYTHONUNBUFFERED=1` を付けないとバッファが溜まるまでpull側にも
> 何も出てこない、という別の落とし穴がある点に注意。

## 4.5 学習用PCへのファイルの送り方

DVC（run管理機能・checkpoint/データ管理とも）は使わない。コードもデータも rsync で
ローカル → 学習用PC の一方向に送る。WebUIに同期用のボタンは付けない。

### コード

- **「学習開始」「`[infer]`」を押した時に、起動処理の一部として自動で1回だけ** rsync する
  （常時同期はしない）。差分転送なので2回目以降は数秒で終わる。
- 常時同期にしない理由: 実行中にローカルで編集すると、遅延importやDataLoader workerの起動時に
  ジョブ途中から別のコードで動きうる。起動時の1回に限定することで「ジョブが使ったコード」を固定する。
- `--delete` を付ける（ローカルで消した `.py` が学習用PCに残って import されるのを防ぐ）。その代わり、
  学習用PCにしか無いものは除外する：`venv/`（学習用PC側で別途作る）、`.git/` `wandb/`
  `__pycache__/`、`checkpoints/*` `samples/*`（学習用PCで生成されるもの。中身だけ除外し、保存先のディレクトリ自体は作る）、各projectの `config.toml`
  （起動時にWebUIが書き込む）、データディレクトリ（下記）。
- `train_webui/` は client（§5）を含むので同期対象に含める（server側のコード・`data/` は除外）。
- 送る時に `git rev-parse HEAD`、`git diff`、未追跡（untracked）ファイルの一覧と中身を取り、runに
  保存する（`git diff` だけだと新規ファイルが残らないため）。
- CLIから手動実行するスクリプトは、最後に同期された時点のコードで動く。ローカルの最新コードで
  手動実行したい場合は、ユーザーが手でrsyncする。

### データ

- 大きく（現状 `data/` 833M、`scratch/MNIST/data` 64M、`scratch/prts_survey/data` 179M）
  ほとんど変わらないため、起動時の自動同期には含めず、**必要な時にユーザーが手動でrsyncする**。
  例: `rsync -a --partial data/ <remote>:<repo_dir>/data/`
- `--delete` は付けない（`replays` のように学習用PC側で生成されるデータを消さないため）。
- world_model系は合成データをその場で生成するため、送るデータ自体が無い。
- `data/dataset` は外部リポジトリのsparse clone（`docs/data-structure.md`）なので、学習用PCが
  インターネットに出られるなら学習用PCで直接cloneする方が速い。組織の共有ストレージ（NFS等）が
  両PCから見えるならそれを使ってもよい。

### checkpoint

- 学習用PCに置いたままにする（inferも学習用PCで動くため、ローカルに持ってくる必要はない）。
  画像・lossは §5 の仕組みでWebUIに届くので、生成物をrsyncで戻す処理も不要。
- 規約通り `model_{group}.pt` は同じgroupのrunごとに上書きされる（run単位の重みは残らない）。
  これは現状の運用のまま許容する。

### 学習用PC側の初回準備（手動・1回だけ）

- sandbox への必要パッケージの追加（§1.6 の sshd・tailscale・rsync・util-linux・procps、python と
  学習に必要なpipパッケージ。RTX 5090 のため torch は cu128 版）、`/opt/sshd/` の設定、startscript、
  tailnet への参加、必要なデータの配置。リポジトリの `requirements.txt` は GTX 1080 用の cu118 固定なので
  学習用PCでは使わない。

## 5. 結果送信の仕組み（client）

- 学習スクリプト側の `wandb.init/log/finish` 呼び出し箇所を、`train_webui/client/` に置く
  プロジェクト非依存の小さなPythonクライアントに置き換える（置き換え作業自体は今回スコープ外、§0）。
  数値系列（loss等）も画像（`wandb.Image`相当）もこのクライアント経由で同じ仕組みで送る。
- clientは「server への送信やrunとの紐づけを管理するライブラリ」（wandbのSDKに相当）として作る。
- **API（wandbに近い書き味）**:
  - `client.init(new_run=False, run_id=None, source=None)` … どのrunに結果を送るかを決める（下記）
  - `client.log({"train/loss": 0.1, ...}, step=...)` … 数値
  - `client.log_image("infer/xy", fig_or_path, step=None)` … matplotlibのFigureか画像ファイルパス
  - `client.define_plot("infer/xy", kind="scatter", xlabel=..., ...)` / `client.log_xy("infer/xy", x, y)`
    … 軸を定義して点を逐次追加していくグラフ（§5.3）
  - `client.finish()` … 送信キューを吐き出して終了（`new_run` で作ったrunは finished にする）

### 5.1 `init()` の2つの使い方

| 用途 | 呼び方 | 動き |
|---|---|---|
| **新しいrunを作る**（学習） | `init(new_run=True)` | serverに新しいrunを作らせ、そのrun_idに送る |
| **既存runに追加する**（infer・可視化スクリプト） | `init()` | serverに問い合わせた既存runに送る |

- 学習コード（`main.py` の train）は常に `init(new_run=True)`、infer・可視化スクリプトは常に `init()`
  と書く。**WebUIから起動してもCLIから手で起動しても、同じコードのまま**動くようにする。
- **run_id の決定順（上ほど優先）**:
  1. `init(run_id="...")` の引数、または環境変数 `TRAIN_WEBUI_RUN_ID` があればそれを使う。
     WebUIから起動したジョブ（train・`[infer]`）は起動時にこの環境変数が渡されるので常にここで決まる
     （runはWebUI側で作成済みなので、`new_run=True` でも新しいrunは作らない）。
  2. `new_run=True` なら、`POST /api/runs` で新しいrunを作らせ、返ってきたrun_idを使う（§5.2）。
  3. どちらでもなければ、`GET /api/runs/resolve?project=...&group=...` で問い合わせる。serverは
     「そのgroupで最後に起動されたtrainのrun（draftは含めない、WebUI起動・CLI起動を問わない）」＝
     その時点の `model_{group}.pt` を作ったrun の run_id を返す。該当runが無い場合は送信をスキップ
     してwarningを出す。
- **project / group / source の決め方**:
  - WebUIから起動したジョブ: 環境変数 `TRAIN_WEBUI_PROJECT` / `TRAIN_WEBUI_SOURCE` を使う。
  - CLIから手で起動した場合: 呼び出し元のファイル位置から project ディレクトリを辿り、その
    `config.toml` の `[wandb].group` を読む。`source` は `new_run=True` なら `train`、それ以外は
    実行スクリプト名（`init(source=...)` で上書き可）。
- 送信先 `TRAIN_WEBUI_URL` が無い場合（手動実行で未設定など）は送信をスキップしてwarningを出す
  だけで処理は継続する。手動実行時は学習用PC側で `TRAIN_WEBUI_URL` を自分で設定しておく
  （`~/.bashrc` 等）。

### 5.2 CLIから起動した学習をrunとして記録する

- `init(new_run=True)` は `POST /api/runs` に次を送り、serverがrun_idを発行して `origin = "cli"`
  のrunを running 状態で作る：
  - `project`, `group`
  - `config_toml`: そのとき実際に読まれた学習用PC上の `config.toml` の中身（WebUIを通さず学習用PCで
    直接編集していても、実際に使われた内容が残る）
  - `hostname`, `pid`（表示用）
- **コードの記録**: 学習用PCには `.git` を同期していない（§4.5）ため、client側ではgit情報を取れない。
  serverは「最後にWebUIがコードを同期したときのgit情報（commit・diff・untracked）」をそのrunに
  記録し、`git_info_source = "last_sync"` として区別する（ユーザーが手でrsyncしていた場合は
  実際のコードとずれうる、という注意書きを画面に出す）。
- **状態の判定**: WebUIはこのプロセスを起動していないので、SSHでの生存確認はしない。代わりに:
  - clientが別スレッドで定期的に `POST /api/runs/{run_id}/heartbeat` を送る（例: 30秒ごと）。
  - `client.finish()` → finished。
  - 未処理例外で終了した場合は、client が `sys.excepthook` / `atexit` で traceback を付けて
    `POST /api/runs/{run_id}/finish`（status=failed）を送る。
  - heartbeatが一定時間（例: 3分）途絶えたら、serverが **lost**（SIGKILL・ネットワーク断などで
    終了を報告できなかった）にする。heartbeatが再開したら running に戻す。
- **排他制御**: CLI起動の学習runが running の間も、serverは GPU使用中とみなし、WebUIの
  「学習開始」「`[infer]`」ボタンを無効化する（§4）。逆に、WebUIのジョブ実行中にCLIから
  `init(new_run=True)` された場合は、runは作るが「GPU使用中に起動された」旨のwarningを返して表示する
  （CLI側のプロセスを止める手段はないため）。
- **できないこと**: WebUIから起動していないので、**WebUIからの停止と `run.log` の表示はできない**
  （ログは手元のターミナルに出る）。failed時はclientが送ったtracebackだけ表示する。グラフ・画像・
  configは通常のrunと同じように見られ、`[infer]`・可視化スクリプトの追加先にもなる。
- **送信方式**: `POST <TRAIN_WEBUI_URL>/api/results` に
  `{project, group, run_id?, kind: "metric"|"image", label, step?, value|image, source, sent_at}` を送る。
  - 学習ループを止めないよう、送信は**別スレッドのキューで非同期**に行い、複数件をまとめて送る。
  - 依存を増やさないよう標準ライブラリ（`urllib`）で実装する。
  - 送信失敗時は warning を出してそのデータは捨てる（学習は止めない）。
  - 学習中は `client.log` を呼ぶたびに（適宜）送られ、WebUIでリアルタイムにグラフが更新される。
- **この仕組みは `main.py --mode train/infer` に限らない。** 同じ `config.toml` を読む
  スタンドアロンなCLIスクリプト（例: 既存の `visualize_synth_data.py` のような、合成データや推論結果を
  さらに可視化する追加プログラム）も、このクライアントを使えば全く同じ経路でWebUIに結果を出せる。
  こうしたスクリプトはWebUIから起動するボタンを持たず、**ユーザーがCLIから直接実行する**運用
  （学習用PC上で `venv/bin/python -m <module.path>.<script> ...` を手打ち）を前提とする。
- runが新しく作られるのは、§3 の「学習開始」と、CLIから `init(new_run=True)` されたとき（§5.2）の
  2通りだけ。それ以外（`[infer]`・可視化スクリプト）の結果送信は既存runへの追記として扱う。
- **履歴はすべて保存する**。同じラベルの画像・数値が何度送られても上書きせず、`source` と `sent_at`
  付きで追記する。

### 5.3 軸つきグラフ（plot）: 軸を定義して点を逐次追加する

- `log` の数値グラフは x軸が step 固定、`log_image` は描画済みの画像しか送れない。そこで、
  matplotlib のように**軸（種類・軸ラベル・スケール・範囲）を定義し、その軸に (x, y) の点を
  後から追加していく**グラフを持つ。描画は frontend（Recharts）が行うので、画像と違って拡大・
  ホバーで値確認・複数runの重ね描きができる。
- 対応する種類は **折れ線（line）** と **散布図（scatter）** の2つ。
- 1つのグラフ（= 1ラベル）の中に**複数の系列（series）**を持てる（例: `"pred"` と `"target"`）。
  系列名を省略したら `"default"`。
- **API**:

  ```python
  from train_webui import client

  plot = client.define_plot(
      "infer/xy",            # ラベル（metric/imageと同じ名前空間。同じrunで種類の違う同名ラベルは不可）
      kind="scatter",        # "line" | "scatter"
      title="推論結果",       # 省略時はラベル名
      xlabel="x", ylabel="y",
      xscale="linear", yscale="log",   # "linear" | "log"
      xlim=None, ylim=(1e-3, 1.0),     # None なら自動
  )
  plot.add(x, y, series="pred")                  # 1点
  plot.add(xs, ys, series="target")              # 同じ長さの配列でまとめて追加
  client.log_xy("infer/xy", x, y, series="pred") # ハンドル無しでも同じことができる
  ```

  - `define_plot` は `Plot` ハンドル（`label` を持つだけの薄いオブジェクト。`add` は `log_xy` を呼ぶ）を返す。
  - `define_plot` を同じラベルで再度呼んだら**定義を上書き**（点は消さない）。`define_plot` 無しで
    `log_xy` した場合は既定の定義（line、軸ラベル無し、linear、範囲自動）で表示する。
  - `x`, `y` はスカラー同士か、同じ長さの配列同士（list/tuple/numpy/torch。`.tolist()` があれば使う）。
    長さが違う・数値化できない場合は warning を出してその呼び出しごと捨てる。NaN/inf の点は捨てる
    （JSONに載らないため）。
  - `step` は任意（`log_xy(..., step=N)`）。表示には使わず、記録として残すだけ。
- **折れ線は送った順に結ぶ**（xでソートしない。matplotlibの `plot` と同じ）。軌跡のように x が
  単調でないデータも描ける。
- 送信は他の結果と同じキュー・同じ `POST /api/results` に乗せる（`log_xy` 1回 = 1 item。
  配列はそのまま1 itemに入れるので、点が多くてもリクエスト数は増えない）。

## 6. データモデル（DB: sqlite）

- `runs`: `run_id`, `display_name`（表示名。§7）, `project`, `group`, `origin`（webui / cli）,
  `status`（draft / running / finished / failed / stopped / lost）,
  `config_toml`, `git_commit`, `git_diff`, `git_untracked`, `git_info_source`（sync / last_sync）,
  `created_at`, `started_at`, `ended_at`, `last_heartbeat_at`（cliのみ）, `hostname`, `pid`,
  `exit_code`, `error`（failed時のメッセージ・traceback）
- `syncs`: `id`, `synced_at`, `git_commit`, `git_diff`, `git_untracked`（WebUIがコードを同期した履歴。
  CLI起動runのgit情報の出どころ）
- `jobs`: `job_id`, `run_id`, `kind`（train / infer）, `status`, `pid`, `job_dir`, `started_at`,
  `ended_at`, `exit_code`, `log_path`（終了後にローカルへ取り込んだrun.log）
- `results`: `id`, `run_id`, `kind`（metric / image）, `label`, `step`, `value`（数値）,
  `image_path`（`media_dir` 内）, `source`, `sent_at`
- `plots`: `run_id`, `label`, `kind`（line / scatter）, `title`, `xlabel`, `ylabel`, `xscale`, `yscale`,
  `xmin`, `xmax`, `ymin`, `ymax`（範囲。NULLなら自動）, `defined`（`define_plot` 済みか。0なら既定値）,
  `updated_at`。主キー (`run_id`, `label`)。
- `plot_points`: `id`, `run_id`, `label`, `series`, `x`, `y`, `step`, `source`, `sent_at`。
  点は増え続けるので `results` とは別テーブルにする（`results` の検索を重くしない）。
  既存DBには `CREATE TABLE IF NOT EXISTS` で足されるだけなので移行処理は不要。

## 7. 画面構成: 共通ナビゲーション + Train / Result の2タブ

画面上部に **Train タブ** と **Result タブ** があり、どちらも左サイドバーの project → group の
折りたたみツリー（＋その下のrun一覧）を共有する（画面遷移としては「project選択」「run選択」の2段。
groupはrunに付いた分類タグとして、ツリー上の見出しになっているだけで独立画面は持たない）。

```
▼ src/world_model_v1        ← project（ディレクトリ構造順）
  ▼ wm_v1_d128_L6 [infer]    ← group（group名順、折りたたみ可）。[infer]ボタンは§8参照
      run 20260925_143012     ← 最新
      run 20260920_101533
    [+ 新規run]
  ▶ wm_v1_d256_L8 [infer]
    [+ 新規run]
  [+ 新規group]
▶ src/world_model_v2
▶ scratch/PolylineInterp
```

- **runの表示名**: `run_id`（timestamp）は長く見にくいので、画面上（ツリー・Trainタブの見出し・グラフの凡例）は
  group 内で作成順に自動で振る表示名 `v1`, `v2`, ... で出す（`run_id` はツールチップ等で確認できる）。
  run 作成時（draft作成・CLIの `init(new_run=True)`）に「その group で今ある最大の番号 + 1」を振ってDBに保存する。
  途中の番号が削除で空いても詰めない（既存runの表示名は変わらない）。draft の `[wandb].group` を書き換えて
  別 group に移った場合は、移った先の group で振り直す。Resultタブの凡例は group をまたぐので `v1 (group)` の形にする。
- group内のrunは作成時刻の新しい順に並べる。各runには状態（draft / running / finished / failed /
  stopped / lost）をアイコン等で表示し、CLIから起動したrun（§5.2）には「CLI」バッジを付ける。
- 「+ 新規run」「+ 新規group」の下書き作成・起動フローは §3 の通り。

## 8. Train タブ

runをクリックすると右のメインパネルに、そのrunの**状態に応じた**内容を表示する。

| run の状態 | 表示内容 |
|---|---|
| draft（起動前の下書き） | configエディタ（編集可）＋「学習開始」ボタン＋削除ボタン |
| running（実行中） | config（読み取り専用）／ ログ（`run.log` のリアルタイムtail、SSH pull。§4）／ グラフ・画像（clientから送られてきたものをリアルタイム表示。軸つきグラフ §5.3 も含む）／「中止」ボタン |
| finished / failed / stopped（終了後） | グラフ・画像一覧／ config（読み取り専用）／ ログ（終了時に取り込んだ全文。failedの場合は最初に目立つように表示）／ 削除ボタン |
| CLIから起動したrun（running / finished / failed / lost） | 上と同じだが、**ログ欄と「中止」ボタンは無い**（ログは手元のターミナル）。failedならclientが送ったtracebackを表示。lostなら「終了を報告できずに止まった可能性あり」と表示。git情報が最後のWebUI同期時点のものである旨を注記 |

- グラフ・画像は §5 の仕組みでそのrunに紐づけられた結果を全部表示する。固定スキーマは持たず、
  どんな系列・画像を送るかは実行側のコードが自由に決める。数値はラベルごとに1グラフ（x軸は
  `step`）。画像はラベルごとに最新を表示し、履歴（過去に送られたもの）を `sent_at` 順に遡って
  見られるようにする。

### `[infer]` ボタン（group単位）

- 押すと、そのgroupの `model_{group}.pt` で推論ジョブ（`--mode infer`）を実行する。新規runは作らない。
- **使うconfig**: `model_{group}.pt` を最後に書いた run（＝そのgroupで最後に起動された train の run）の
  configスナップショットを `config.toml` に書き込んでから実行する。checkpointと組になるconfigで
  inferできればよいので、checkpointの中身を読みに行かず、WebUI側のrun履歴から特定する。
  （将来、学習プロジェクトのフォーマットを決める際に checkpoint のメタデータにも `run_id` を
  書き込むようにしておけば、突き合わせで検証できる）
- **結果の追加先**: 上記と同じ run（そのcheckpointを作ったrun）。
- **ログ**: 実行中は、追加先runの画面上部に「infer実行中」のバナーとログのリアルタイムtail・中止
  ボタンを出す。終了後はそのrunの画面に「inferジョブ履歴」として、各inferの状態・開始時刻・ログ
  （取り込み済み全文）を一覧で残す。failedならバナーで通知する。

### CLIから手動実行する可視化スクリプト

- `visualize_synth_data.py` のような、infer結果や合成データをさらに可視化する追加プログラムには
  専用UIを用意しない。§5のクライアントで結果を送っていれば、clientがserverに問い合わせた
  run（既定ではそのgroupで最後に起動されたtrainのrun）の結果として追加表示される。

## 9. Result タブ

- 複数runの結果（数値グラフ・画像）を比較表示するための画面。
- **比較対象の選び方**: 左ナビゲーション（§7のツリー）自体の表示・非表示は変えない。project / group /
  run の各ノードに「比較対象に含めるか」のチェックを付ける。チェックは階層的に効く（projectの
  チェックを外せばそのproject配下のgroup/run全部が比較対象から外れる、group単位・run単位でも同様に
  個別指定できる）。ONになっているrunの結果だけが比較エリアに並ぶ。
- **表示項目の選び方**: 比較対象runに含まれる**ラベルごと**（`train/loss`、`infer/xy` 等）の
  チェックボックスで、何を表示するか選ぶ。
- **数値グラフは重ねて表示**: 同じラベルの系列を、比較対象runぶん1つのグラフに重ねる（run
  ごとに色分け、凡例にrun_idとgroup）。
- **run へのコメント**: 各 run に1つメモ（`runs.comment`）を持たせ、何を試した run か・結果の所感を書く。
  画面右側の開閉できるコメント欄に、Train タブでは選択中の run、Result タブでは比較中の run ごとに1つずつ欄を
  並べ、run の状態によらずいつでも編集できる（欄の幅はドラッグで変えられる）。欄の外をクリックか Ctrl+Enter で保存する。
  `train_webui.cli` の `show` / `compare` にも出る。
- **比較の保存**: Result タブ上部の「比較」バーで、今の構成（比較する run・表示中のラベル・非表示にしているラベル）に
  名前を付けて残せる（新規保存・上書き・名前変更・削除。DB の `comparisons`、API は `/api/comparisons`）。選ぶと
  run のチェックとラベルの表示・非表示がそのまま復元される（その後 run やラベルを変えると「変更あり」と出る）。
  保存した run が後で削除されていても比較自体は残り、復元時は残っている run だけがONになる。
  `cli comparisons` で一覧、`cli view --comparison <名前>` で読める。左ナビの縦スクロールバーは表示しない（スクロールはできる）。
- **モデルの説明（group ごと）**: group（=モデル構成）に Markdown の説明を持たせる（DB の `groups.description`、
  API は `PUT /api/groups/description`、run の詳細に `group_description` が付く）。**読み手は主に Claude**で、モデルが
  どう作られているか（構成・入力と出力・損失・他の group との違い）を知るための知識。Train タブ上部の折りたたみ欄で編集
  （未記入なら開いた状態、欄の外クリックか Ctrl+Enter で保存）。同じ group の run すべてで共通。`cli show` / `view` /
  `compare` の先頭に出し（同じ group は1回）、`cli ls` では未記入の group に `[モデルの説明なし]` を付ける。
  Claude は `ctl describe` で書ける。
- **画面の構成を Claude に見せる**: ブラウザは、今見ている構成（タブ・Train なら選択中の run・Result なら比較中の
  run と表示中のラベル）を、変わるたびに（500ms 待ってから）`PUT /api/view` で送る。DB の `view_state`（1行）に保存され、
  `cli view` がそれを読む（server が止まっていても読める）。送り元のブラウザが複数あれば最後に送った方が残る。
  ツリーの各 run の行（ホバーで出る）の 📋 は、その run の `run_id` をクリップボードにコピーする。Result タブ上部の
  「run_id をコピー」は比較中の run_id を1行ずつコピーする（Claude に貼れば `cli view <run_id>...` で読める）。
  http（tailscale の IP）で開いていると `navigator.clipboard` が使えないので、`execCommand('copy')` にフォールバックする。
- **`[infer]` ボタン**: Train タブと同じく、ツリーの各 group に `[infer]` を出す（§8）。Result タブで結果を見ながら
  infer を追加でき、結果は自動更新で反映される。
- **画像はrunごとに並べる**: ラベルごとに1行、比較対象runを横に並べる。各runの最新の画像を表示する。
- **軸つきグラフ（plot, §5.3）は重ねて表示**: 同じラベルの全系列を、比較対象runぶん1つの軸に重ねる。
  - 系列は (run, series) の組ごとに色を変える。凡例は `series @ run_id (group)`（系列が `default`
    だけなら `run_id (group)`）。
  - 軸の定義（種類・軸ラベル・スケール・範囲・タイトル）は、比較対象runの中で `updated_at` が最新の
    定義を使う。run間で定義が食い違う場合はグラフの上に「runによって軸の定義が異なります」と小さく出す。
  - log スケールの軸に 0 以下の点があれば、その点は描かずに件数を注記する。
  - 実行中に点が増えていくので、他の結果と同じく3秒ごとに更新する。点は差分（前回取得以降に
    追加された分）だけ取得する。
- **点が多い系列は間引いて表示する**: Recharts は SVG で1点ずつ要素を作るので、点が数千を超えると
  描画が重くなる。そこで**1系列あたりの表示点数に上限（既定 1000点）**を設け、超えた系列は backend で
  間引いてから返す（転送量も減らすため frontend ではなく backend で行う）。
  - 間引いた点は**実在する点の中から選ぶ**（平均値などを作らない）。ホバーで出る値は必ず実際に
    送られた値になる。
  - **折れ線（line）: 最小・最大を残す間引き（min-max decimation）**。送信順に点を「上限÷2」個の
    区間に分け、各区間で y が最小の点と最大の点を元の順序のまま残す。単純に等間隔で抜くと loss の
    スパイクのような一瞬の山・谷が消えるが、これなら形が保たれる。最初と最後の点は必ず残す。
  - **散布図（scatter）: 等間隔の間引き**。送信順に等間隔で1点ずつ残す。点の密度の分布（どこに点が
    集まっているか）はおおよそ保たれる。孤立した外れ値は消えることがある。
  - 間引いた系列は、グラフの右上に「間引き表示中（元 N 点 → 表示 M 点）」と出す。
  - 上限以下の系列は今まで通り差分で追記する。上限を超えた系列は差分を足していくと点数が増え
    続けるため、取得のたびに**間引き済みの系列全体を取り直して置き換える**。
  - 拡大して細部を見たい場合に元の点を取りに行く機能（表示範囲だけ取り直す）は今回は持たない（§11）。

## 9.4 自動更新の頻度（負荷を抑える）

- ブラウザのタブが裏にある間は、どの自動更新（ツリー・状態・Train/Result の結果・ログ）も止め、表に戻ったら再開する。
- Result タブは、学習中・infer 中の run（`status=running` か `/api/state` の active）だけ3秒ごとに結果を取り直す。
  それ以外の run は最初に1回取ったら60秒ごとに確認するだけにする（CLI から手で infer した結果も拾えるように、
  止めはしない）。動いていた run が止まった直後は最後の結果を拾うためにもう1回取る。
- Train タブは、学習・infer が動いていない run は30秒ごとの確認にする（動いている間は1.5秒ごと）。
- backend の project 検出（リポジトリ全体の走査と main.py の AST 解析、数十ms）は結果を30秒使い回す
  （`server.toml` の `[log] project_scan_interval_sec`）。「再スキャン」と、一覧に無い project での draft 作成時は即時に探し直す。

## 9.5 結果を CLI で読む（Claude と相談する用）

- `venv/bin/python -m train_webui.cli {ls,show,plot,compare}`（`train_webui/cli.py`）。DB と画像フォルダを
  読み取り専用で直接読むので、server が止まっていても使え、何も書き込まない。
- project はパスの末尾、group は group_name か group_id、run は表示名（v1, ...）か run_id で指定できる
  （run 省略時は group で最後に起動した run）。
- `show` は config（前の run からの差分）・グラフの要約（系列ごとの点数と最初・最小・最後）・画像のパス・ジョブを、
  `plot` は1つのグラフを少ない点数に間引いた表を、`compare` は2つの run の config 差分と数値の表を出す。
- 使い方は `train-webui-results` skill（~/.claude/skills）にまとめてある。

## 10. 決定事項メモ

- 同時実行はシステム全体で1ジョブまで（train/infer問わず、projectをまたいでも1つ、GPU1台共有前提）。
- `run_id` はtimestampベース（例: `20260925_143012`）で十分。
- モードは `train` / `infer` の2つに固定。`eval` / `ddpo` のような追加モードは持たせず、
  `config.toml` 側の設定でtrainモード内の挙動として切り替える（§2）。
- `config.toml` の `[wandb]` セクション名・`project`/`group`キーは維持する。
- ネットワークはWSL上のtailscale経由で学習用PC→serverへ到達する。認証は無しだが、接続元IPで
  VPN（tailscale）内とloopbackに限定する（§1）。
- 既存プロジェクトの移行は今回スコープ外（フォーマット確定後に行う）。
- 軸つきグラフ（§5.3）は line / scatter のみ、点の逐次追加方式、複数runは1つの軸に重ね描き。
- 軸つきグラフの点が多い系列は backend で間引く（1系列 既定1000点、line は min-max、scatter は等間隔）（§9）。
- frontend は React、backend は Python + FastAPI。
- 結果のrunへの紐づけ: WebUIから起動したジョブは環境変数の `run_id`、CLI手動実行はclientが
  serverに問い合わせる（明示指定で上書き可）（§5）。
- CLIから手で起動した学習も `init(new_run=True)` で新しいrunとして記録できる（§5.2）。学習コードは
  WebUI起動・CLI起動で書き分けない。

## 11. オープンな論点

- 軸つきグラフで、1つの軸に折れ線と散布図を混ぜたい（例: 真値は線、サンプルは点）場合の扱い。
  現状は種類をグラフ単位で持つ。必要になったら系列単位の `kind` 上書きを検討する。
- 軸つきグラフの間引き（§9）で、拡大した範囲だけ元の点を取り直す機能。現状は常に系列全体を
  上限点数に間引いて返す。
- 間引き中の系列は3秒ごとに全点を読んで間引き直すので、1系列が数百万点になると backend が重くなり
  うる。遅くなったら SQL 側での間引き（ウィンドウ関数）やキャッシュを検討する。

## PWA（アプリとしてインストールできる）

- `frontend/public/` の `manifest.webmanifest`・`sw.js`・アイコンが、ビルドで `dist/` に入り server が配信する。
  `index.html` で manifest とアイコンを読み、`main.tsx` が本番ビルドの時だけ service worker を登録する。
- service worker は画面の殻（HTML・`/assets/*`）だけをキャッシュし、**`/api` と `/media` はキャッシュしない**
  （学習の状態・結果が古いまま出るのを避ける）。繋がらない時は画面だけ開き、データは取れない。
- **https（か localhost）で開かないとインストールできない**。`http://100.x.x.x:8000`（tailscale の IP）は
  secure context ではないので、service worker が登録されず、「インストール」も出ない（コピーボタンも同じ理由で
  フォールバックに回る）。tailscale の https で出す：
  `tailscale serve --bg --https=<空いているport> http://127.0.0.1:8000` → `https://<マシン名>.<tailnet>.ts.net:<port>`
  を開き、ブラウザの「インストール」（Chrome/Edge）か、iOS なら共有 →「ホーム画面に追加」。
  serve は tailnet 内だけに公開される。server から見た接続元は 127.0.0.1（`allowed_networks` で許可済み）になる。
- アイコンを変える時は `frontend/public/` の4枚（192・512・maskable 512・apple-touch 180）を差し替える。
- service worker の中身を変えたら `sw.js` の `CACHE` 名を上げる（古いキャッシュが消える）。
