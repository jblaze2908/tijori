"""ASGI entrypoint: `uvicorn tijori.app.main:app`."""

import logging

from tijori.app import create_app


class _DropQueryString(logging.Filter):
    """uvicorn's access line carries the full URL; query strings hold OAuth codes and search terms."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_DropQueryString())

app = create_app()
