#!/usr/bin/env python3
"""
transcriber.py
指定フォルダに WAV ファイルが追加されたら自動で Whisper 文字起こしを実行する。
結果は同じフォルダに <元のファイル名>.txt として保存する。
"""

import sys
import time
import argparse
import threading
from pathlib import Path
from queue import Queue, Empty

try:
    import whisper
except ImportError:
    print("openai-whisper not found. Install with:\n  pip install openai-whisper")
    sys.exit(1)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("watchdog not found. Install with:\n  pip install watchdog")
    sys.exit(1)

DEFAULT_WATCH_DIR = str(Path.home() / "Desktop" / "recordings")
DEFAULT_MODEL = "medium"
DEFAULT_LANGUAGE = "ja"


# ---------------------------------------------------------------------------
# ファイル完了待ち（録音中のファイルを拾わないため）
# ---------------------------------------------------------------------------

def wait_until_stable(path: Path, interval=1.0, retries=10) -> bool:
    """ファイルサイズが変化しなくなるまで待つ。タイムアウトしたら False を返す。"""
    prev_size = -1
    stable_count = 0
    for _ in range(retries * 5):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == prev_size and size > 0:
            stable_count += 1
            if stable_count >= 2:
                return True
        else:
            stable_count = 0
        prev_size = size
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# 文字起こし
# ---------------------------------------------------------------------------

def transcribe(wav_path: Path, model, language: str, output_dir: Path | None):
    out_dir = output_dir or wav_path.parent
    out_file = out_dir / (wav_path.stem + ".txt")

    if out_file.exists():
        print(f"[skip] 既に文字起こし済み: {out_file.name}")
        return

    print(f"[start] {wav_path.name}")
    try:
        result = model.transcribe(str(wav_path), language=language, verbose=False)
    except Exception as e:
        print(f"[error] 文字起こし失敗: {e}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        # タイムスタンプ付きのセグメントを書き出し
        for seg in result["segments"]:
            start = _fmt_time(seg["start"])
            end   = _fmt_time(seg["end"])
            f.write(f"[{start} --> {end}]\n{seg['text'].strip()}\n\n")

    print(f"[done]  {out_file}")


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# フォルダ監視
# ---------------------------------------------------------------------------

class WavHandler(FileSystemEventHandler):
    def __init__(self, queue: Queue):
        self._queue = queue

    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".wav"):
            self._queue.put(Path(event.src_path))

    def on_moved(self, event):
        # ファイルが別フォルダからコピー完了後に移動してきた場合
        if not event.is_directory and event.dest_path.lower().endswith(".wav"):
            self._queue.put(Path(event.dest_path))


def watch_and_transcribe(watch_dir: Path, model, language: str, output_dir: Path | None):
    queue: Queue = Queue()
    handler = WavHandler(queue)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()

    print(f"監視中: {watch_dir}")
    print(f"モデル : {model.dims.n_mels and 'loaded'}")
    print(f"言語  : {language}")
    if output_dir:
        print(f"出力先: {output_dir}")
    print("Ctrl+C で終了\n")

    try:
        while True:
            try:
                wav_path = queue.get(timeout=1)
            except Empty:
                continue

            # ファイルが書き込み完了するまで待機
            print(f"[wait]  {wav_path.name} の書き込み完了を待機中...")
            if not wait_until_stable(wav_path):
                print(f"[skip]  タイムアウト: {wav_path.name}")
                continue

            transcribe(wav_path, model, language, output_dir)

    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        print("\n終了しました。")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="WAV ファイルを監視して自動文字起こし (Whisper)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  python transcriber.py
  python transcriber.py --watch C:\\meetings\\recordings
  python transcriber.py --model large-v3
  python transcriber.py --output C:\\meetings\\transcripts
  python transcriber.py --language en

デフォルト監視フォルダ: {DEFAULT_WATCH_DIR}
        """,
    )
    parser.add_argument("--watch", "-w", default=DEFAULT_WATCH_DIR, metavar="DIR",
                        help=f"監視するフォルダ (default: {DEFAULT_WATCH_DIR})")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL,
                        choices=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
                        help=f"Whisper モデル (default: {DEFAULT_MODEL})")
    parser.add_argument("--language", "-l", default=DEFAULT_LANGUAGE, metavar="LANG",
                        help=f"言語コード (default: {DEFAULT_LANGUAGE})")
    parser.add_argument("--output", "-o", default=None, metavar="DIR",
                        help="テキスト出力先フォルダ (default: WAV と同じフォルダ)")

    args = parser.parse_args()

    watch_dir = Path(args.watch)
    if not watch_dir.exists():
        watch_dir.mkdir(parents=True, exist_ok=True)
        print(f"フォルダを作成しました: {watch_dir}")

    output_dir = Path(args.output) if args.output else None

    print(f"Whisper モデル ({args.model}) を読み込み中...")
    model = whisper.load_model(args.model)

    watch_and_transcribe(watch_dir, model, args.language, output_dir)


if __name__ == "__main__":
    main()
