"""Source/portable entry point (relative imports stay inside the package)."""
from infalsus_studio.__main__ import main

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        import os,traceback
        from pathlib import Path
        target=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'InFalsusStudio'/'last-error.txt'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(traceback.format_exc(),encoding='utf8')
        raise SystemExit(1)
