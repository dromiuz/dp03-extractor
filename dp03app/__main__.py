try:
    from .app import main
except ImportError:
    # PyInstaller can execute this file as a top-level script during
    # one-folder builds, where relative imports have no package parent.
    from dp03app.app import main

raise SystemExit(main())
