#!/usr/bin/env python3
"""
Join a Teams meeting from the command line.
Opens the Teams desktop app and auto-clicks the Join button.

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


def auto_join(timeout=40):
    """Wait for Teams pre-join screen and click the Join button."""
    try:
        from pywinauto import Desktop
    except ImportError:
        print("pywinauto not installed — click 'Join now' manually.")
        print("To enable auto-join:  pip install pywinauto")
        return False

    # Button titles for English and Japanese Teams
    JOIN_TITLES = ("Join now", "今すぐ参加")

    print(f"Waiting for join screen (up to {timeout}s) ", end="", flush=True)
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            desktop = Desktop(backend="uia")
            for win in desktop.windows(title_re=".*(Teams|チーム).*", visible_only=True):
                for title in JOIN_TITLES:
                    try:
                        btn = win.child_window(title=title, control_type="Button")
                        if btn.exists(timeout=0.5):
                            btn.click_input()
                            print(f"\nJoined! (clicked '{title}')")
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
  python join_meeting.py "https://teams.microsoft.com/..." --no-auto-join
  python join_meeting.py "https://teams.microsoft.com/..." --timeout 60
        """,
    )
    parser.add_argument("url", help="Teams meeting URL (https:// or msteams://)")
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

    # Give Teams a moment to launch before scanning for the button
    time.sleep(4)
    auto_join(timeout=args.timeout)


if __name__ == "__main__":
    main()
