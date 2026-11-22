"""Aplikacja Flask z WebSocket (Flask-SocketIO)."""

import os
import time
import threading
import logging
from flask import Flask
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

socketio = SocketIO()


def create_app():
    """Utworz aplikacje Flask."""
    app = Flask(
        __name__,
        template_folder='templates',
        static_folder='static',
    )

    app.config['ASSET_VERSION'] = os.environ.get('ASSET_VERSION', '20261122e')

    @app.context_processor
    def inject_asset_version():
        return {'asset_version': app.config.get('ASSET_VERSION', 'dev')}

    # Wczytaj dozwolone origins z env. Domyślnie '*' (sieć lokalna).
    # W produkcji ustaw: CORS_ALLOWED_ORIGINS=http://192.168.1.10:5050
    _cors_env = os.environ.get('CORS_ALLOWED_ORIGINS', '*')
    _cors_env = (_cors_env or '*').strip()

    if _cors_env in ('*', ''):
        cors_origins = '*'
        logger.warning(
            "CORS ustawiony na '*' – ogranicz przez zmienną środowiskową "
            "CORS_ALLOWED_ORIGINS w środowisku produkcyjnym"
        )
    else:
        cors_origins = [o.strip() for o in _cors_env.split(',') if o.strip()]
        if not cors_origins:
            cors_origins = '*'
            logger.warning(
                "CORS_ALLOWED_ORIGINS jest puste po parsowaniu; wracam do '*'"
            )

    socketio.init_app(
        app,
        cors_allowed_origins=cors_origins,
        cors_credentials=False,
        async_mode='eventlet',
        allow_upgrades=False,
    )

    from web.routes import bp
    app.register_blueprint(bp)

    return app


def emit_data(boiler_data):
    """Wyślij dane przez WebSocket do wszystkich klientów."""
    try:
        socketio.emit('new_data', boiler_data.to_dict())
    except Exception as e:
        logger.error("Błąd wysyłania danych WebSocket: %s", e, exc_info=True)


def emit_alert(alert):
    """Wyślij alert przez WebSocket do wszystkich klientów."""
    try:
        socketio.emit('new_alert', alert)
    except Exception as e:
        logger.error("Błąd wysyłania alertu WebSocket: %s", e, exc_info=True)
