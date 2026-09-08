# Horn, manual switch and audio countdown hardware

The app supports a ProLog-style race-office hardware workflow:

- Automatic serial-port horn output.
- Manual horn button in the app.
- Optional sensing of a physical horn button.
- Race log of automatic and manual horn events.
- Central audio countdown/course announcements generated on the race-office PC for speakers or a VHF audio input.

## Serial horn output

The app uses `pyserial` to assert DTR on a serial adapter for the configured blast duration. This matches the ProLog-style wiring found in the race-office interface.

Typical settings in **Settings → Horn settings & Test → Horn output**:

- Port: `COM3` on Windows or `/dev/ttyUSB0` on Linux.
- Output line: `DTR` for the ProLog-style interface.
- Polarity: active high or active low depending on the interface.
- Default blast duration: the horn blast time used by race signals and finish-button horn blasts.

When no serial port is configured, the app runs in simulation mode and logs horn events without firing hardware.

## Wiring safety

Do not drive a horn directly from a serial adapter. Use a relay, opto-isolated relay board, transistor driver or a properly designed interface.

Do not connect horn voltage or a horn switch directly to CTS/DSR/DCD/RI. Use isolation and voltage limiting.

A robust arrangement is:

- The existing horn button and horn circuit continue to work without the PC.
- The PC horn output drives an isolated relay contact in parallel with the horn button.
- A separate isolated auxiliary contact or opto output feeds the manual horn input line.

## Manual horn input sensing

Manual horn input sensing records the time when the physical horn button is pressed. The ProLog-style interface uses the auxiliary relay contact as feedback:

- `DTR` pin 4 drives the horn relay.
- `RTS` pin 7 is held asserted and used as the feedback-contact common.
- Relay relaxed / horn off: `RTS` is connected to `DCD` pin 1.
- Relay active / horn on: `RTS` is connected to `CTS` pin 8.

Configure in **Settings → Horn settings & Test → Manual horn input sensing**:

- Enable manual horn input sensing.
- Set polling interval.
- Check the live input status changes from idle to active when the physical horn button is pressed.

The app treats `CTS` asserted as the active/manual-horn state and uses `DCD` as the idle-state diagnostic internally. The status poll uses the authenticated JSON API under `/admin/api/hardware/input_status`; if the browser cannot reach that API it reports **Input status unavailable** rather than showing a raw JSON parser error. The race **Start console & log** tab deliberately shows only short active/idle feedback text so the page stays uncluttered during a start sequence.

Only manual horn button/input events can be assigned as a boat finish from the race log. Automatic sequence horns are not finish-assignable.

## Manual controls

The **Start console & log** tab has manual controls for:

- Manual horn.
- Individual recall.
- General Recall.
- Postpone.
- Abandon.

These actions are logged. Manual horn events can later be assigned as finish times if required.

## Central audio countdown and VHF feed

Automatic speech is generated on the race-office PC running the app, not in the browser. This keeps the VHF feed working if the race officer is using a tablet, adding boats, viewing another page or using the split-screen view.

Configure and test central audio in **Settings → Horn settings & Test → Start automation and central audio**. On Windows the app can use `pyttsx3`/SAPI; other systems fall back to available local speech tools where possible.

To feed this to VHF:

1. Route the race-office PC audio output into a small mixer, isolation transformer or approved radio audio interface.
2. Feed that into the VHF/PA/VOX input as appropriate.
3. Set levels conservatively and test before racing.
4. Keep the official horn circuit independent of the audio feed.

### VOX wake-up tone

A VOX-keyed radio takes a moment to start transmitting, which clips the first word or two off every announcement. **Play a VOX wake-up tone before announcements** (Settings → Horn settings & Test → *Start automation and central audio*) sounds a short tone first so the radio is already transmitting when the speech starts. **VOX tone lead (seconds)** sets how far ahead of the announcement it plays — long enough for your radio to key, and no longer. Leave the tone off if the audio path is not VOX-keyed.

### Speech rates

Two rates are configurable, because the useful pace differs:

- **Normal speech rate** — course and general announcements. Slow this down as far as you like: it is the one to change if announcements are hard to follow, and it no longer affects the timing of anything.
- **Countdown speech rate** — the final “Ten. Nine. … One.”, and **the one that has to keep time**.

Both are words-per-minute values passed to the speech engine, so the sensible range depends on the voice installed on the PC. Test with **Save and test central audio** rather than guessing.

**The countdown rate governs when the count starts, not just how fast it is said.** The ten numbers are one utterance, so the app works out how far ahead of the gun to begin it: about eleven seconds at a rate of 185, proportionally earlier at a slower rate and later at a faster one. That is why the count keeps time whatever you set. Until v0.266 it was spoken at the *normal* rate with a fixed eleven-second lead, so a club that slowed the normal rate down to make the course announcements followable stretched the count with it and “One” landed after the gun.

If you have slowed the normal rate, **set the countdown rate to what the normal rate used to be** — 185 unless you had changed it. A club upgrading keeps whatever it had stored, since a default is not a migration; only a fresh install picks up 185 on its own.

If the count ever drifts against the clock, time the phrase at a known rate and set `COUNTDOWN_REFERENCE_RATE` and `COUNTDOWN_PHRASE_SECONDS_AT_REFERENCE` in `core/startsequence.py` to what you measured. Voices differ.

The automatic start-sequence scheduler also runs centrally. Browser pages display the countdown and signal plan, but they no longer fire automatic horns or generate automatic speech. The horn signal remains the authoritative race signal.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
