"""ASGI entrypoint: `uvicorn tijori.app.main:app`."""

from tijori.app import create_app

app = create_app()
