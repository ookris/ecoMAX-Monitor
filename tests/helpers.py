"""Wspolne narzedzia testow API."""

import contextlib
import os

from flask import Flask

from database import Database
from web import routes

_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')
_app = None


def _get_app():
    """Aplikacja Flask z blueprintem tras, bez warstwy SocketIO/eventlet.

    eventlet nie wspiera najnowszych wersji Pythona, a trasy REST nie wymagaja SocketIO.
    """
    global _app
    if _app is None:
        _app = Flask('ecomax-test', template_folder=os.path.join(_WEB_DIR, 'templates'),
                     static_folder=os.path.join(_WEB_DIR, 'static'))
        _app.register_blueprint(routes.bp)
        _app.testing = True
    return _app


@contextlib.contextmanager
def make_test_app(tmp_path, connection, monkeypatch):
    monkeypatch.setattr(routes, '_eventlet_tpool', None)
    db = Database(str(tmp_path / 'test.db'))
    routes.init_routes(connection, db)
    try:
        with _get_app().test_client() as test_client:
            yield test_client, db
    finally:
        db.close()
