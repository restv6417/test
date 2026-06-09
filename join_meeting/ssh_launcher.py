#!/usr/bin/env python3
"""
ssh_launcher.py
SSH セッションから Teams 会議に参加するためのランチャー。

SSH は非インタラクティブセッションで動くため、join_meeting.py を直接実行しても
Teams GUI が表示されない。本スクリプトはアクティブなデスクトップセッションを検出し、
psexec または schtasks 経由でそのセッション内に join_meeting.py を投入する。

前提:
  - Windows 機にユーザーがログイン済み（ローカル or RDP）
  - psexec (Sysinternals) または schtasks (Windows 標準) が利用可能

Usage:
  python ssh_launcher.py "https://teams.microsoft.com/l/meetup-join/..."
"""

import sys
import os
import subprocess
import argparse
import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
JOIN_SCRIPT = SCRIPT_DIR / "join_meeting.py"


# ---------------------------------------------------------------------------
# セッション検出
# ---------------------------------------------------------------------------

def get_active_session_id():
    """
    qwinsta でアクティブなデスクトップセッション ID を返す。
    例:
      SESSIONNAME    USERNAME    ID  STATE
      >console       alice        1  Active   ← これを取る
       rdp-tcp#0     bob          2  Active
    """
    result = subprocess.run(
        ["qwinsta"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    for line in result.stdout.splitlines():
        if "Active" not in line:
            continue
        if "Services" in line or "session 0" in line.lower():
            continue
        for token in line.split():
            if token.isdigit():
                return int(token)
    return None


# ---------------------------------------------------------------------------
# 起動方法 ① psexec
# ---------------------------------------------------------------------------

def launch_psexec(session_id, url, passthrough_args):
    """
    psexec -i <session> -d python join_meeting.py <url> [args]

    -i  : 指定セッションのデスクトップで実行
    -d  : 起動後すぐに制御を返す（待機しない）
    """
    cmd = [
        "psexec",
        "-i", str(session_id),
        "-d",
        sys.executable,
        str(JOIN_SCRIPT),
        url,
        *passthrough_args,
    ]
    print(f"[psexec] session={session_id}  cmd={' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    # psexec は正常起動でも returncode=1 を返すことがある
    if result.returncode not in (0, 1):
        print(f"psexec error (code {result.returncode}): {result.stderr.strip()}")
        return False
    return True


# ---------------------------------------------------------------------------
# 起動方法 ② schtasks（psexec 不要・Windows 標準）
# ---------------------------------------------------------------------------

def launch_schtasks(url, passthrough_args, delay_sec=5):
    """
    タスクスケジューラに /it (interactive) フラグ付きで登録してすぐ実行。
    インタラクティブフラグにより、ログイン中ユーザーのデスクトップで起動する。
    """
    task_name = "JoinMeeting_OneShot"
    run_at = (datetime.datetime.now() + datetime.timedelta(seconds=delay_sec)
              ).strftime("%H:%M")

    # コマンド文字列を組み立て（スペース含むパスをクォート）
    args_str = " ".join(f'"{a}"' if " " in a else a for a in passthrough_args)
    tr = f'"{sys.executable}" "{JOIN_SCRIPT}" "{url}" {args_str}'.strip()

    create = [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", tr,
        "/sc", "once",
        "/st", run_at,
        "/it",          # interactive: ログインユーザーのデスクトップで実行
        "/rl", "HIGHEST",
        "/f",           # 既存タスクを上書き
    ]
    print(f"[schtasks] {run_at} に実行予約 (約 {delay_sec}s 後)")
    r = subprocess.run(create, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"schtasks /create error: {r.stderr.strip()}")
        return False

    run = subprocess.run(
        ["schtasks", "/run", "/tn", task_name],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        print(f"schtasks /run error: {run.stderr.strip()}")
        return False

    return True


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SSH 経由で Teams 会議参加スクリプトをデスクトップセッションに投入する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ssh_launcher.py "https://teams.microsoft.com/l/meetup-join/..."
  python ssh_launcher.py "URL" --method psexec
  python ssh_launcher.py "URL" --method schtasks
  python ssh_launcher.py "URL" --mic-on
  python ssh_launcher.py "URL" --timeout 60

セッション確認:
  qwinsta          # アクティブなセッション一覧
  query session    # 同上
        """,
    )
    parser.add_argument("url", help="Teams 会議 URL")
    parser.add_argument(
        "--method", choices=["auto", "psexec", "schtasks"], default="auto",
        help="起動方法 (auto: psexec → schtasks の順で試みる)",
    )
    # join_meeting.py へのパススルー引数
    parser.add_argument("--mic-on", action="store_true",
                        help="マイク ON で参加（join_meeting.py に転送）")
    parser.add_argument("--no-auto-join", action="store_true",
                        help="自動クリックなし（join_meeting.py に転送）")
    parser.add_argument("--timeout", type=int, default=40, metavar="SEC",
                        help="join_meeting.py の待機タイムアウト秒数")

    args = parser.parse_args()

    # join_meeting.py に渡す引数を構築
    passthrough = []
    if args.mic_on:
        passthrough.append("--mic-on")
    if args.no_auto_join:
        passthrough.append("--no-auto-join")
    if args.timeout != 40:
        passthrough += ["--timeout", str(args.timeout)]

    # アクティブセッション確認
    session_id = get_active_session_id()
    if session_id is None:
        print("ERROR: アクティブなデスクトップセッションが見つかりません。")
        print("       対象 Windows にユーザーがログイン中か確認してください。")
        sys.exit(1)
    print(f"Active session: {session_id}")

    # 起動
    method = args.method
    launched = False

    if method in ("auto", "psexec"):
        try:
            launched = launch_psexec(session_id, args.url, passthrough)
        except FileNotFoundError:
            if method == "psexec":
                print("psexec が見つかりません。Sysinternals PsExec をインストールしてください。")
                sys.exit(1)
            print("psexec が見つかりません。schtasks にフォールバックします...")
            method = "schtasks"

    if not launched and method in ("auto", "schtasks"):
        launched = launch_schtasks(args.url, passthrough)

    if launched:
        print("Teams がデスクトップ上で起動します。")
    else:
        print("起動に失敗しました。対象マシンで join_meeting.py を直接実行してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
