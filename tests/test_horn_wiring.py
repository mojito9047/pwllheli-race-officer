"""Manual horn input sensing and the horn output polarity are one wiring, not two.

Reported from the hut while testing a race: clearing **Assert output line during
horn blast** produced a continuous stream of "Manual horn switch detected" with
nobody near the button. Every one of those scheduled an evidence clip, and the 84
that piled up then held up the nightly off-site backup. Ticking it again stopped
them.

The mechanism, on the ProLog wiring the app documents:

* DTR drives the horn relay; RTS is held asserted as the relay-feedback common;
  the relay's auxiliary contact ties RTS to DCD when idle and to CTS when active.
* ``horn_active`` false means active-low, so ``set_serial_output_inactive`` holds
  DTR **asserted** at idle.
* That energises the horn relay continuously — which is the horn — and its
  feedback contact then ties RTS to CTS, which is exactly the signal the app reads
  as "the manual button is pressed".

So the two settings are not independent. The app already forced the horn line, the
input line and the input polarity when sensing is enabled; it did not force the
one that mattered most.
"""
from __future__ import annotations

import app as ro
from core import horn


class TestSensingForcesAssertToFire:
    def test_enabling_sensing_forces_assert_to_fire(self, client):
        ro.save_hardware_config({
            "serial_port": "COM5",
            "horn_active": "0",              # what the race officer cleared
            "horn_input_enabled": "1",
        })
        assert horn.hardware_config()["horn_active"] is True

    def test_it_is_stored_not_only_applied_when_read(self, client):
        """Otherwise the tickbox shows one thing and the app does another."""
        ro.save_hardware_config({
            "serial_port": "COM5",
            "horn_active": "0",
            "horn_input_enabled": "1",
        })
        with ro.get_db() as db:
            stored = db.execute("SELECT value FROM hardware_settings WHERE key = 'horn_active'").fetchone()
        assert stored["value"] == "1"

    def test_active_low_is_still_allowed_without_sensing(self, client):
        """It is a legitimate option for other relay wiring — it is only sensing
        that pins the polarity down."""
        ro.save_hardware_config({
            "serial_port": "COM5",
            "horn_active": "0",
            "horn_input_enabled": "0",
        })
        assert horn.hardware_config()["horn_active"] is False

    def test_the_other_wiring_settings_are_forced_as_before(self, client):
        """Guarding against a regression in the guard this was added to."""
        ro.save_hardware_config({
            "serial_port": "COM5",
            "horn_line": "RTS",
            "horn_input_enabled": "1",
            "horn_input_line": "DSR",
            "horn_input_active": "0",
        })
        cfg = horn.hardware_config()
        assert cfg["horn_line"] == horn.PROLOG_HORN_OUTPUT_LINE
        assert cfg["horn_input_line"] == horn.PROLOG_ACTIVE_INPUT_LINE
        assert cfg["horn_input_active"] is True


class TestTheIdleLinesAreSafe:
    """What set_serial_output_inactive actually puts on the wire."""

    class _Port:
        def __init__(self):
            self.dtr = None
            self.rts = None

        def setDTR(self, value):
            self.dtr = bool(value)

        def setRTS(self, value):
            self.rts = bool(value)

    def test_with_sensing_on_the_horn_relay_is_left_de_energised(self, client):
        """The bug: DTR asserted at idle holds the relay in, which is the horn on
        and the feedback contact reporting a pressed button."""
        ro.save_hardware_config({"serial_port": "COM5", "horn_active": "0", "horn_input_enabled": "1"})
        port = self._Port()
        horn.set_serial_output_inactive(port, horn.hardware_config())
        assert port.dtr is False, "DTR asserted at idle energises the horn relay"

    def test_and_the_sense_common_is_asserted_so_dcd_and_cts_can_be_read(self, client):
        ro.save_hardware_config({"serial_port": "COM5", "horn_input_enabled": "1"})
        port = self._Port()
        horn.set_serial_output_inactive(port, horn.hardware_config())
        assert port.rts is True

    def test_without_sensing_active_low_still_idles_asserted(self, client):
        """Unchanged for a non-ProLog setup that genuinely wants active-low."""
        ro.save_hardware_config({"serial_port": "COM5", "horn_active": "0", "horn_input_enabled": "0"})
        port = self._Port()
        horn.set_serial_output_inactive(port, horn.hardware_config())
        assert port.dtr is True


class TestTheSettingsPageExplainsIt:
    def test_the_tickbox_is_locked_and_says_why_when_sensing_is_on(self, logged_in_client):
        ro.save_hardware_config({"serial_port": "COM5", "horn_input_enabled": "1"})
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        block = html.split("Assert output line during horn blast", 1)[0][-600:]
        assert "disabled" in block, "the tickbox should not offer a setting that is overridden"
        assert "Locked on because" in html

    def test_it_is_a_normal_tickbox_when_sensing_is_off(self, logged_in_client):
        ro.save_hardware_config({"serial_port": "COM5", "horn_input_enabled": "0"})
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert "Locked on because" not in html
