"""Entry point: no arguments -> window; any argument -> command line (see --help)."""
import sys


def run() -> int:
    if len(sys.argv) > 1:
        from dashgps.cli import main
        return main()
    from dashgps.gui import main
    return main()


if __name__ == "__main__":
    sys.exit(run())
