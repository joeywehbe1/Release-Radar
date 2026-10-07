"""CLI: `python -m radar run [--dry-run]` and `python -m radar test-webhook`."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from radar import notify, pipeline
from radar import state as state_mod
from radar.config import load_config, webhook_url
from radar.http import make_session

log = logging.getLogger("radar")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # emoji on Windows consoles
        except Exception:
            pass
    parser = argparse.ArgumentParser(prog="python -m radar", description="New-music and viral alerts for Discord.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="check every source once and post new alerts")
    run_p.add_argument("--dry-run", action="store_true", help="print what would be posted; don't post or save state")
    run_p.add_argument("--state", default="state/state.json", help="state file (default: state/state.json)")
    run_p.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("test-webhook", help="post a test message to the webhook")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    cfg = load_config()
    session = make_session()

    if args.cmd == "test-webhook":
        ok = notify.WebhookNotifier(webhook_url(), session).send(notify.test_payload(cfg))
        print("Test message sent. Check your Discord channel." if ok else "Test message failed (see error above).")
        return 0 if ok else 1
    return run(args, cfg, session)


def run(args, cfg: dict, session) -> int:
    path = Path(args.state)
    state = state_mod.load(path)
    webhook = None if args.dry_run else webhook_url()  # fail fast, before calling any source
    now = int(time.time())
    started = time.monotonic()

    result = pipeline.run_cycle(cfg, state, pipeline.LiveFetchers(cfg, session), now)
    _summary(result, cfg)
    if not any(result.stats.get(k) for k in ("charts", "watchlist", "deezer", "youtube")):
        log.error("Every source failed; nothing to do this run.")
        return 1

    if args.dry_run:
        _preview(result, now, cfg)
        log.info("Dry run finished in %.0fs (nothing posted, state not saved)", time.monotonic() - started)
        return 0

    try:
        ok = pipeline.deliver(result, state, notify.WebhookNotifier(webhook, session), now, cfg)
    finally:
        state["last_run"] = now
        state_mod.prune(state, now, cfg)
        state_mod.save(path, state)
    posted = 1 if result.bootstrap else len(result.alerts)
    log.info("Run finished in %.0fs: %s", time.monotonic() - started,
             "bootstrap message posted" if result.bootstrap else f"{posted} alert(s) posted")
    return 0 if ok else 1


def _summary(result: pipeline.CycleResult, cfg: dict) -> None:
    kinds = {"new": 0, "viral": 0}
    for a in result.eligible:
        kinds[a.kind] += 1
    plan = ("bootstrap: 1 summary message, the rest marked as seen" if result.bootstrap
            else f"posting {len(result.alerts)} this run")
    log.info("Scored %d recent songs; eligible: %d new drops, %d viral (threshold %s); %s",
             len(result.ranked), kinds["new"], kinds["viral"], cfg["alerts"]["viral_threshold"], plan)
    for score, t in result.ranked[:10]:
        p = t["parts"]
        log.info("  %5.1f  %-45.45s heat %3.0f  mom %.2f  yt %.2f  x %.2f  rd %.0f",
                 score, f"{t['artist']} — {t['title']}", t["heat"], p["chart_momentum"], p["youtube"],
                 p["cross_platform"], p["reddit"])


def _preview(result: pipeline.CycleResult, now: int, cfg: dict) -> None:
    printer = notify.PrintNotifier()
    if result.bootstrap:
        print(f"\nNo saved state yet, so the first real run is a BOOTSTRAP: it posts this one message and quietly "
              f"marks the {len(result.eligible)} songs that qualify right now as already seen.")
        printer.send(notify.online_payload(result, now, cfg))
        print(f"\nFor tuning: if this weren't the first run, today's data would trigger "
              f"{len(result.alerts)} alert(s) this run (after caps):")
    else:
        print(f"\nThis run would post {len(result.alerts)} alert(s):")
    for a in result.alerts:
        printer.send(notify.alert_payload(a, now, cfg))
    print("─" * 70)


if __name__ == "__main__":
    sys.exit(main())
