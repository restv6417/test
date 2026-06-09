#!/usr/bin/env python3
"""
Join a Teams meeting from the command line.
Opens the Teams desktop app, optionally mutes the mic, then auto-clicks Join.

Usage:
  python join_meeting.py "https://teams.microsoft.com/l/meetup-join/..."
"""

import os
import sys
import time
import argparse

# Teams web URL → app protocol URL
def _to_app_url(url):
    if url.startswith("https://teams.microsoft.com"):
        return url.replace("https://teams.microsoft.com",
                           "msteams://teams.microsoft.com", 1)
    return url


def open_teams(url):
    app_url = _to_app_url(url)
    try:
        os.startfile(app_url)
    except Exception as e:
        print(f"Error opening Teams: {e}")
        sys.exit(1)


def _find_teams_windows(desktop):
    return desktop.windows(title_re=".*(Teams|チーム).*", visible_only=True)


def mute_mic_if_on(win):
    """
    Mute the microphone on the pre-join screen if it is currently ON.
    Teams button title == the ACTION (what clicking does):
      "Mute microphone"        → mic is ON  → click to mute
      "Unmute microphone"      → mic is OFF → nothing to do
    """
    # Titles that indicate mic is currently ON (clicking will mute it)
    MIC_ON_TITLES = (
        "Mute microphone",
        "マイクをミュートにする",
        "Mute",
    )
    # Titles that confirm mic is already OFF
    MIC_OFF_TITLES = (
        "Unmute microphone",
        "マイクのミュートを解除する",
        "Unmute",
    )

    for title in MIC_ON_TITLES:
        try:
            btn = win.child_window(title=title, control_type="Button")
            if btn.exists(timeout=0.5):
                btn.click_input()
                print("  Mic: ON → muted")
                return
        except Exception:
            continue

    for title in MIC_OFF_TITLES:
        try:
            btn = win.child_window(title=title, control_type="Button")
            if btn.exists(timeout=0.5):
                print("  Mic: already muted")
                return
        except Exception:
            continue

    print("  Mic: state unknown — check manually")


def auto_join(timeout=40, mic_off=True):
    """Wait for Teams pre-join screen, configure mic, then click Join."""
    try:
        from pywinauto import Desktop
    except ImportError:
        print("pywinauto not installed — click 'Join now' manually.")
        print("To enable auto-join:  pip install pywinauto")
        return False

    JOIN_TITLES = ("Join now", "今すぐ参加")

    print(f"Waiting for join screen (up to {timeout}s) ", end="", flush=True)
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            desktop = Desktop(backend="uia")
            for win in _find_teams_windows(desktop):
                for title in JOIN_TITLES:
                    try:
                        btn = win.child_window(title=title, control_type="Button")
                        if not btn.exists(timeout=0.5):
                            continue

                        print()  # newline after progress dots

                        if mic_off:
                            mute_mic_if_on(win)

                        btn.click_input()
                        print(f"  Joined! (clicked '{title}')")
                        return True
                    except Exception:
                        continue
        except Exception:
            pass

        print(".", end="", flush=True)
        time.sleep(1)

    print("\nJoin button not found — please click manually.")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Join a Teams meeting from the command line",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python join_meeting.py "https://teams.microsoft.com/l/meetup-join/..."
  python join_meeting.py "https://teams.microsoft.com/..." --mic-on
  python join_meeting.py "https://teams.microsoft.com/..." --no-auto-join
  python join_meeting.py "https://teams.microsoft.com/..." --timeout 60
        """,
    )
    parser.add_argument("url", help="Teams meeting URL (https:// or msteams://)")
    parser.add_argument("--mic-on", action="store_true",
                        help="Join with microphone ON (default: mic is muted)")
    parser.add_argument("--no-auto-join", action="store_true",
                        help="Open Teams but skip auto-clicking Join")
    parser.add_argument("--timeout", type=int, default=40, metavar="SEC",
                        help="Seconds to wait for join screen (default: 40)")

    args = parser.parse_args()

    print("Opening Teams...")
    open_teams(args.url)

    if args.no_auto_join:
        print("Teams opened. Click 'Join now' to join.")
        return

    time.sleep(4)
    auto_join(timeout=args.timeout, mic_off=not args.mic_on)


if __name__ == "__main__":
    main()
