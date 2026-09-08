"""Regression: the 'Save and test weather source' button must not 500.

The route updated core.weather_store.WEATHER_POLLER_STATE via bare names that
app.py never imported, so it raised NameError -> 500 (a latent bug, uncaught by
any test). Guards the fix (import from core.weather_store in routes.settings_actions).
"""
import app as ro


def test_weather_test_route_does_not_500(logged_in_client, monkeypatch):
    monkeypatch.setattr(ro.video, "public_live_frame_path", lambda: __file__)  # unrelated safety
    monkeypatch.setattr(
        ro, "fetch_weather_station_sample",
        lambda force=False: {"ok": True, "message": "Weather station test complete."},
    )
    token = "test-csrf-token"
    with logged_in_client.session_transaction() as sess:
        sess["_csrf_token"] = token
    resp = logged_in_client.post(
        "/admin/settings/weather/test",
        data={"_csrf_token": token, "weather_source": "station"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
