"""Explicit candidate entrypoint; does not alter existing fast_cli gates."""
def main():
    import sys
    if len(sys.argv) < 2 or sys.argv[1] not in (
        'simulate-exact-events', 'simulate-mapped-exact-events'):
        raise SystemExit('fast_adapter_cli supports exact-event timing commands only')
    from .fast_adapters import activate
    activate()
    from .cli import main as cli_main
    cli_main()


if __name__ == '__main__':
    main()
