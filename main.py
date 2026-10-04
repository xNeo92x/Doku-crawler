import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from docharbor.cli import main
    else:
        from docharbor.app import main
    raise SystemExit(main())
