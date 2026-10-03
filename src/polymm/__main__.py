"""Allow ``python -m polymm ...``."""

from polymm.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
