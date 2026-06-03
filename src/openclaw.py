"""Compatibility wrapper for the old OpenClaw-named lunch feed task."""

from lunch_feed import *  # noqa: F403
from lunch_feed import main


if __name__ == "__main__":
    main()
