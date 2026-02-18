"""Watermeter Inference — CLI entry point."""
import argparse
import sys


def cli():
    parser = argparse.ArgumentParser(description="Watermeter Inference")
    parser.add_argument(
        "--one-shot",
        action="store_true",
        help="Run a single meter reading and exit (exit 0 = success)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    args = parser.parse_args()

    if args.one_shot:
        from watermeter.oneshot import main as oneshot_main

        sys.exit(oneshot_main(args.config))
    else:
        from watermeter.app import main

        main()


if __name__ == "__main__":
    cli()
