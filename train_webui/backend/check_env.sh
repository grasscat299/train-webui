#!/bin/bash
# 学習用PCの singularity instance 内の環境確認（design.md §1.6）。check_remote.py が ssh の標準入力で流して実行する。
# 引数: [WebUI server の URL] [python（server.toml の remote.python）]
URL="$1"
PY="${2:-python3}"
# ssh ログイン時の PATH には /usr/sbin が無いことが多く、sshd / tailscaled を見落とすため足す
PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"
ok() { echo "OK  $1"; }
ng() { echo "NG  $1"; }

echo "== コマンド =="
for c in sshd rsync setsid ps pgrep tailscale tailscaled ts; do
  p=$(command -v "$c" 2>/dev/null) && ok "$c ($p)" || ng "$c が見つからない"
done

echo; echo "== Python パッケージと GPU =="
p=$(command -v "$PY" 2>/dev/null) && ok "学習に使う python: $PY ($p)" || ng "学習に使う python（$PY）が見つからない"
"$PY" - <<'EOF'
import importlib, subprocess, sys

def pip_show(name):
    r = subprocess.run([sys.executable, "-m", "pip", "show", name], capture_output=True, text=True)
    if "No module named pip" in r.stderr:
        return None, "pip が使えない"
    info = dict(line.split(": ", 1) for line in r.stdout.splitlines() if ": " in line)
    if r.returncode != 0 or "Version" not in info:
        return False, "pip: 見つからない"
    return True, f"pip: {info['Version']} @ {info.get('Location', '?')}"

mismatch = False
for m in ["torch", "numpy", "matplotlib", "tomli"]:
    found, pip_part = pip_show(m)
    try:
        importlib.import_module(m)
        print(f"OK  {m}（{pip_part}）")
    except Exception as e:
        print(f"NG  {m} を import できない: {e}（{pip_part}）")
        if found:
            mismatch = True
if mismatch:
    print("    → pip では入っているのに import できないものがある。上の Location がこの python の sys.path に無い:")
    for p in sys.path:
        print(f"      {p}")
try:
    import torch
    print(f"--  torch の CUDA 版: {torch.version.cuda} / 対応アーキテクチャ: {torch.cuda.get_arch_list()}")
    if not torch.cuda.is_available():
        print("NG  torch.cuda.is_available() が False")
    else:
        print(f"OK  GPU: {torch.cuda.get_device_name(0)} capability={torch.cuda.get_device_capability(0)}")
        x = torch.ones(1024, 1024, device="cuda")
        print(f"OK  GPU で実際に計算できる（{(x @ x).sum().item():.0f}）")
except Exception as e:
    print(f"NG  GPU の確認で失敗: {e}")
EOF

echo; echo "== sshd と tailscale の設定ファイル =="
for f in /opt/sshd/sshd_config /opt/sshd/host_ed25519 /opt/sshd/authorized_keys /usr/local/bin/ts; do
  [ -r "$f" ] && ok "$f" || ng "$f が無い（または読めない）"
done
grep -q tailscaled /.singularity.d/startscript 2>/dev/null && ok "startscript に tailscaled の起動がある" || ng "startscript が書き換わっていない"

echo; echo "== 動いているプロセス =="
pgrep -x tailscaled >/dev/null && ok "tailscaled が動いている" || ng "tailscaled が動いていない（instance で起動したか確認）"
pgrep -x sshd >/dev/null && ok "sshd が動いている" || ng "sshd が動いていない"
ts status >/dev/null 2>&1 && ok "tailnet に参加している（$(ts ip -4 2>/dev/null)）" || ng "tailnet に参加していない（ts up が必要）"

echo; echo "== 環境変数（sshd の SetEnv で渡しているもの） =="
case ":$LD_LIBRARY_PATH:" in *:/.singularity.d/libs:*) ok "LD_LIBRARY_PATH に GPU ライブラリがある";; *) ng "LD_LIBRARY_PATH に /.singularity.d/libs が無い";; esac
[ -n "$http_proxy" ] && ok "http_proxy=$http_proxy" || ng "http_proxy が無い（client の送信が tailnet に届かない）"
[ -n "$TMPDIR" ] && [ -w "$TMPDIR" ] && ok "TMPDIR=$TMPDIR（書き込み可）" || ng "TMPDIR が無いか書き込めない"

if [ -n "$URL" ]; then
  echo; echo "== WebUI server に届くか =="
  "$PY" - "$URL" <<'EOF' && ok "$URL に届いた" || ng "$URL に届かない（server の起動と http_proxy を確認）"
import sys, urllib.request
try:
    print("    " + urllib.request.urlopen(sys.argv[1] + "/api/state", timeout=10).read().decode())
except Exception as e:
    print(f"    {type(e).__name__}: {e}")
    sys.exit(1)
EOF
fi
