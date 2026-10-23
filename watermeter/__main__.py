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
    sub = parser.add_subparsers(dest="command")
    set_password = sub.add_parser("set-password", help="Set or reset the admin password (needs host access)")
    set_password.add_argument(
        "--auth-file",
        default=None,
        help="Path to auth.json (default: $WATERMETER_AUTH_FILE or /data/auth.json)",
    )
    args = parser.parse_args()

    if args.command == "set-password":
        from watermeter.auth import cli_set_password

        sys.exit(cli_set_password(args.auth_file))
    elif args.one_shot:
        from watermeter.oneshot import main as oneshot_main

        sys.exit(oneshot_main(args.config))
    else:
        from watermeter.app import main

        main()


if __name__ == "__main__":
    cli()
