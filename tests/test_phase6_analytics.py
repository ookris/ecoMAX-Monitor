"""Testy fazy 6: analityka pracy kotla z kazdej ramki."""

import datetime
import time

import pytest

from analytics import (
    METRIC_ENERGY, METRIC_FEEDER, METRIC_FUEL, METRIC_KINDLINGS, METRIC_LIGHTER_STARTS,
    METRIC_OBSERVED, METRIC_WORK_CYCLES, OperationTracker, local_day, mode_metric,
)
from database import Database
from econet.decoder import BoilerData
from tests.helpers import make_test_app


def _data(mode, *, feeder=False, fan=False, lighter=False, kw=0.0, fuel=0.0):
    data = BoilerData()
    data.mode = mode
    data.feeder_on = feeder
    data.fan_on = fan
    data.lighter_on = lighter
    data.boiler_power_kw = kw
    data.fuel_stream = fuel
    return data


@pytest.fixture()
def db(tmp_path):
    database = Database(str(tmp_path / 'an.db'))
    yield database
    database.close()


def test_tracker_integrates_times_and_counts_events(db):
    tracker = OperationTracker(db)
    t0 = datetime.datetime(2026, 11, 22, 10, 0).timestamp()
    day = local_day(t0)
    frames = [
        (0, _data(0)),                                            # wylaczony
        (2, _data(2, lighter=True, fan=True)),                    # rozpalanie
        (4, _data(2, lighter=True, fan=True, feeder=True)),
        (6, _data(3, fan=True, kw=10.0, fuel=2.0)),               # praca
        (8, _data(3, fan=True, feeder=True, kw=10.0, fuel=2.0)),
        (10, _data(3, fan=True, kw=10.0, fuel=2.0)),
        (200, _data(5)),                                          # przerwa > 60 s - nie calkowana
        (202, _data(2, lighter=True)),                            # kolejne rozpalenie
    ]
    for offset, data in frames:
        tracker.on_frame(data, now=t0 + offset)
    tracker.flush()

    stats = db.get_daily_stats(day)[day]
    assert stats[METRIC_OBSERVED] == pytest.approx(12.0)          # 5 odcinkow po 2 s + 2 s po przerwie
    assert stats[mode_metric(0)] == pytest.approx(2.0)
    assert stats[mode_metric(2)] == pytest.approx(4.0)
    assert stats[mode_metric(3)] == pytest.approx(4.0)
    assert stats[mode_metric(5)] == pytest.approx(2.0)
    assert stats[METRIC_FEEDER] == pytest.approx(4.0)             # stan z poprzedniej ramki x dt
    assert stats[METRIC_KINDLINGS] == 2
    assert stats[METRIC_WORK_CYCLES] == 1
    assert stats[METRIC_LIGHTER_STARTS] == 2                      # wlaczenia zapalarki: t=2 s i t=202 s
    assert stats[METRIC_ENERGY] == pytest.approx(10.0 * 4 / 3600)
    assert stats[METRIC_FUEL] == pytest.approx(2.0 * 4 / 3600)

    events = db.get_mode_events(since=t0 - 1)
    assert [e['mode'] for e in events] == [0, 2, 3, 5, 2]


def test_tracker_writes_only_on_flush_and_retries_on_db_error(db, monkeypatch):
    tracker = OperationTracker(db)
    t0 = time.time()
    tracker.on_frame(_data(3), now=t0)
    tracker.on_frame(_data(3), now=t0 + 11)
    day = local_day(t0 + 11)
    assert db.get_daily_stats(day) == {}                          # on_frame nie pisze do bazy
    assert db.get_mode_events(since=0) == []

    monkeypatch.setattr(db, 'add_daily_stats', lambda inc: False)
    monkeypatch.setattr(db, 'add_mode_event', lambda **kw: False)
    tracker.flush()                                               # zapis nieudany - dane zostaja w pamieci
    monkeypatch.undo()
    tracker.flush()
    assert db.get_daily_stats(day)[day][METRIC_OBSERVED] == pytest.approx(11.0)
    assert [e['mode'] for e in db.get_mode_events(since=0)] == [3]


def test_tracker_reset_drops_pending_data(db):
    tracker = OperationTracker(db)
    t0 = time.time()
    tracker.on_frame(_data(3), now=t0)
    tracker.on_frame(_data(3), now=t0 + 5)
    tracker.reset()
    tracker.flush()
    assert db.get_daily_stats('2000-01-01') == {}
    assert db.get_mode_events(since=0) == []


def test_frame_without_mode_breaks_continuity(db):
    tracker = OperationTracker(db)
    t0 = time.time()
    tracker.on_frame(_data(3), now=t0)
    tracker.on_frame(_data(None), now=t0 + 5)                     # brak trybu
    tracker.on_frame(_data(3), now=t0 + 10)
    tracker.on_frame(_data(3), now=t0 + 12)
    tracker.flush()
    day = local_day(t0)
    assert db.get_daily_stats(day)[day][METRIC_OBSERVED] == pytest.approx(2.0)


def test_tracker_background_thread_flushes(db):
    tracker = OperationTracker(db, flush_interval=0.05)
    tracker.start()
    t0 = time.time()
    tracker.on_frame(_data(3), now=t0)
    tracker.on_frame(_data(3), now=t0 + 2)
    time.sleep(0.3)
    tracker.stop()
    day = local_day(t0)
    assert db.get_daily_stats(day)[day][METRIC_OBSERVED] == pytest.approx(2.0)


def test_daily_analytics_api(tmp_path, monkeypatch):
    class _Conn:
        connected = False
        last_data = None

        def get_status(self):
            return {'connected': False}

    with make_test_app(tmp_path, _Conn(), monkeypatch) as (client, db):
        today = time.strftime('%Y-%m-%d')
        db.add_daily_stats({
            (today, METRIC_OBSERVED): 43200.0,
            (today, mode_metric(3)): 21600.0,
            (today, mode_metric(5)): 21600.0,
            (today, METRIC_KINDLINGS): 4,
            (today, METRIC_WORK_CYCLES): 6,
            (today, METRIC_FEEDER): 1800.0,
            (today, METRIC_ENERGY): 80.0,
            (today, METRIC_FUEL): 16.0,
        })
        start = datetime.datetime.now().replace(hour=0, minute=30, second=0, microsecond=0)
        db.upsert_controller_alarm(code=7, start_text=start.isoformat(sep=' '), start_ts=start.timestamp(),
                                   end_text=None, end_ts=None)
        db.set_config('fuel_price_per_ton', '1200')

        data = client.get('/api/analytics/daily?days=3').get_json()
        row = [r for r in data['data'] if r['day'] == today][0]
        assert row['coverage_percent'] == 50.0
        assert row['modes']['3']['hours'] == 6.0
        assert row['modes']['3']['name'] == 'Praca'
        assert row['modes']['3']['percent'] == 50.0
        assert row['kindlings'] == 4
        assert row['kindling_failures'] == 1
        assert row['kindling_success_percent'] == 75.0
        assert row['feeder_minutes'] == 30.0
        assert row['cost_pln'] == pytest.approx(19.2)
        assert row['cost_per_kwh'] == pytest.approx(0.24)


def test_reset_runtime_clears_analytics(db):
    db.add_daily_stats({('2026-11-22', METRIC_OBSERVED): 10.0})
    db.add_mode_event(timestamp=1.0, mode=3, previous_mode=2)
    db.reset_runtime_data()
    assert db.get_daily_stats('2000-01-01') == {}
    assert db.get_mode_events(since=0) == []


def test_reset_endpoint_resets_tracker(tmp_path, monkeypatch):
    from web import routes

    class _Conn:
        connected = False
        last_data = None

        def get_status(self):
            return {'connected': False}

    with make_test_app(tmp_path, _Conn(), monkeypatch) as (client, db):
        tracker = OperationTracker(db)
        t0 = time.time()
        tracker.on_frame(_data(3), now=t0)
        tracker.on_frame(_data(3), now=t0 + 5)
        monkeypatch.setattr(routes, '_operation_tracker', tracker)
        resp = client.post('/api/db/reset-runtime', json={}, headers={'X-Requested-With': 'ecoMAX-Monitor'})
        assert resp.get_json()['status'] == 'ok'
        tracker.flush()
        assert db.get_daily_stats('2000-01-01') == {}
