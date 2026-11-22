"""Test dymny: wszystkie strony renderuja sie (szablony Jinja) z nowymi sekcjami."""

import pytest

from tests.helpers import make_test_app


class _Conn:
    connected = False
    last_data = None

    def get_status(self):
        return {'connected': False}


@pytest.mark.parametrize('path, marker', [
    ('/', 'mixers-section'),
    ('/analysis', 'daily-cycles-table'),
    ('/parameters', 'regdata-section'),
    ('/boiler-control', 'device-info'),
    ('/alerts', 'controller-alarms-section'),
    ('/settings', 'fuel_level_not_set'),
    ('/logs', '<html'),
])
def test_page_renders(tmp_path, monkeypatch, path, marker):
    with make_test_app(tmp_path, _Conn(), monkeypatch) as (client, _):
        resp = client.get(path)
        assert resp.status_code == 200
        assert marker in resp.get_data(as_text=True)
