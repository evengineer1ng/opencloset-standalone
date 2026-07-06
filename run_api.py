from __future__ import annotations

import os

from api.api.app import create_app


def main() -> None:
    db_path = os.environ.get("OPENCLOSET_DB_PATH")
    host = os.environ.get("OPENCLOSET_API_HOST", "127.0.0.1")
    port = int(os.environ.get("OPENCLOSET_API_PORT", "5000"))

    app = create_app(db_path=db_path, start_background_workers=True)
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()