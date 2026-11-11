"""Separate local worker. Claim once; never replay interrupted provider work."""

import argparse
import signal
from threading import Event

from .demo import demo_enabled
from .web_api import live_enabled, tasks


def main():
    parser = argparse.ArgumentParser(description="Process private queued travel tasks")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    stopped = Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            tasks.store.maintenance()
            if not (live_enabled() or demo_enabled()):
                if args.once:
                    break
                stopped.wait(0.5)
                continue
            did_work = tasks.execute()
            if args.once:
                break
            if not did_work:
                stopped.wait(0.5)
    finally:
        tasks.pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
