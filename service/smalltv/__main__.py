"""Command line: python -m smalltv --config config.toml {run,preview,push-once,find-location}."""
import argparse
import logging
import sys

from . import __version__, config as config_module, web
from .app import Dashboard
from .bom import BomClient, state_of


def main(argv=None):
    parser = argparse.ArgumentParser(prog="smalltv", description=__doc__)
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Rotate screens on the device forever")
    preview = sub.add_parser("preview", help="Render every screen to PNG files, without the device")
    preview.add_argument("out", help="Output folder")
    sub.add_parser("push-once", help="Push one screen to the device and exit")
    find = sub.add_parser("find-location", help="List BOM places matching a name")
    find.add_argument("name")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)

    if args.command == "find-location":
        client = BomClient(config_module.Weather(location=args.name), "UTC")
        for place in client.search(args.name, limit=10):
            print(f"{place.get('name', '')}, {state_of(place)}  type={place.get('type', '')}  id={place.get('id', '')}")
        return 0

    try:
        settings = config_module.Settings(args.config)
        try:
            cfg = settings.current()
        except ValueError as error:
            print(f"ignoring dashboard settings in {settings.path}: {error}", file=sys.stderr)
            settings.overrides = {}
            cfg = settings.current()
    except (OSError, ValueError) as error:
        print(f"config error: {error}", file=sys.stderr)
        return 2
    dashboard = Dashboard(cfg)
    if args.command == "preview":
        for path in dashboard.preview(args.out):
            print(path)
    elif args.command == "push-once":
        dashboard.refresh_weather(force=True)
        dashboard.cycle()
    else:
        if cfg.web.enabled:
            web.start(dashboard, settings, cfg.web)
        dashboard.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
