from __future__ import annotations

import sys

from app.main import main as app_main


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--camera-preview":
        # Delegate to the camera preview subprocess when frozen.
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from app.camera_preview import main as preview_main
        raise SystemExit(preview_main())
    app_main()


if __name__ == "__main__":
    main()
