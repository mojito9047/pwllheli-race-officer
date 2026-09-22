# The trackers: what they are and how they behave

Everything the club has measured about the GPS trackers on the boats — what each model
costs in battery, how accurate it is, how quickly its fixes arrive, and the several ways
each one will mislead you. Written from measurements against the club's own units in
August and September 2026, not from datasheets.

The vendor protocol manuals are in `docs/Trackers/`, by manufacturer, and the settings the
club's own units are running are in `docs/Trackers/Config Files/` — the GL521MG's as plain
text, the ATC700's as a configurator export. They are tracked so a change to a tracker is a
change somebody can see: the roaming trap below was invisible for exactly as long as the
configuration lived only on a laptop.

**The ATC700's parameter list is not in this repo and cannot be.** Teltonika ship a datasheet
and a quick manual, both here; the parameter IDs and the SMS/GPRS command list live only on
their wiki, at <https://wiki.teltonika-gps.com/view/ATC700> — follow *Parameter List* and
*SMS/GPRS Command List*. The configurator export beside it is a binary file, so it cannot be
read or diffed here either. Looking up what a parameter means, or what the club's units are
actually set to, therefore needs the wiki plus a `getparam` round trip. The table under
*Teltonika ATC700* below is that round trip written down, so the next person does not have to
repeat it.

---

## The fleet at a glance

| | Teltonika ATC700 | Queclink GL521MG | Jimi LL301 | Teltonika RUTX50 |
|---|---|---|---|---|
| Traccar protocol | `teltonika` | `gl200` | `gt06` | `teltonika` |
| Position CEP50 | **1.9–4.5 m** clear sky\*\* | 8–13 m | 41 m | 0.7 m\* |
| Position CEP95 | **5.4–20 m** clear sky\*\* | 24–46 m | 138 m | 1.7 m\* |
| Worst excursion seen | 79 m | **1060 m** | 860 m | 2 m |
| Reporting, moving | 10 s | 60 s (hard floor) | ~34 s | 60 s |
| Delivery lag | **1 s** | 45 s | 28 s | **0 s** |
| Drain, active | **~10 %/h** | ~1 %/h | — | mains |
| Quality fields sent | hdop, pdop, sat, rssi | hdop only | sat | sat |

\* The RUTX50 reports an identical position between fixes, so it is almost certainly
averaging or only re-reporting on movement. Its figure is flattered and not comparable.

\*\* Across the club's five units, measured together. Indoors the same five gave 3.9–7.2 m and
9.9–22 m. That spread is **not** unit-to-unit variation — their configurations are identical to
the parameter — it is how much sky each one could see. See *Sky view swamps the tracker* below.

**There is no best tracker.** The ATC700 is roughly twice as accurate as the GL — three
times, given a clear sky — and answers in a second instead of forty-five, and it goes flat in
nine hours. The GL is the one that finishes a night race. Choose per job, not overall.

---

## The traps

Each of these cost real time to find, and none is visible without measuring.

### The SIM roams, so half of every setting is dead

The club's SIM is multi-network and registers as **roaming in the UK**. Teltonika
parameters come in home/roaming pairs, and only the roaming half is live:

| Home | Roaming | Setting |
|---|---|---|
| 10000 | 10100 | On-stop record interval |
| 10005 | 10105 | On-stop send period |
| 10004 | 10104 | On-stop min saved records |
| 10050 | 10150 | Moving record interval |
| 10055 | 10155 | Moving send period |
| 10054 | 10154 | Moving min saved records |

The device **accepts a write to the dead half, reads it back correctly, and ignores it**.
`setparam 10000:10` was confirmed by the device, read back as 10, and changed nothing for
25 minutes; `setparam 10100:10` took effect on the next cycle.

**Always write both halves.** Not only because a tracker's SIM type may be unknown, but
because a multi-network SIM can change registration mid-race — a device with one half set
would silently change reporting behaviour partway through, with nothing at the app end to
explain it. Both halves fit easily inside the 160-character command limit.

### hdop does not detect a bad fix

During two GPS failures on 16 August, `hdop` sat at 1–3 throughout — including while a
receiver was placing itself a kilometre below the sea. It reports confidence in the fix
just made, not whether the solution has drifted. It *does* rise after a cold start
(5.0 following a reboot), so it measures acquisition, not correctness.

### Altitude is the signal that works

A boat is at sea level, so altitude is the one field whose true value is known in advance
— which turns every fix into a direct error measurement. Both August failures are obvious
in it and invisible everywhere else. The app records altitude, hdop, pdop, satellites,
rssi and Traccar's `valid` flag with every fix for exactly this reason.

Healthy bands are per-model and per-location, so judge against a rolling median rather
than a constant: the ATC700 holds a 9 m spread, a GL unit 119 m at the same place and
time.

### A type code does not identify a manufacturer

The first eight digits of an IMEI (the Type Allocation Code) identify the model, and two
units of a model share it exactly — the club's two GL521MGs both read `86486407`. It is
the only thing that separates devices Traccar calls identical: the ATC700 and the RUTX50
are both `teltonika`.

But the code belongs to whoever certified the radio, and a tracker built on an
off-the-shelf cellular module often ships with the module vendor's range. The ATC700's
datasheet names its module as a Quectel EG915U, so another maker's tracker on the same
part could carry the same code. The app therefore matches on **type code plus protocol**,
and a device whose protocol contradicts the catalogue entry is not matched.

### Sky view swamps the tracker

On the night of 20 August the club's five ATC700s sat within three metres of each other, so
they shared a sky, a night and a temperature. Their accuracy still varied by nearly a factor
of two — CEP50 3.9 m to 7.2 m, CEP95 9.9 m to 22 m, worst excursion 13 m to 79 m.

**It was not the configuration.** Twenty-one parameters were read back off each unit and all
five are identical, GNSS source (`121`) included, at 31 — every constellation enabled. Nor was
it a stale almanac on the newer units: time-to-first-fix is 2–4 s on all five.

It was satellite count, and the relationship is monotonic and holds *within* every unit as
well as across them:

| Satellites | Fixes | CEP50 | CEP95 |
|---|---|---|---|
| under 10 | 147 | 10.0 m | 23.0 m |
| 10–14 | 351 | 6.7 m | 17.9 m |
| 15–19 | 140 | 4.5 m | 13.9 m |
| 20 or more | 119 | **3.4 m** | **8.7 m** |

The best unit held a median of 21 satellites against 10–13 for the other four. The reason is
mundane: it was the one on charge, so it was wherever the charger is, and three metres indoors
is the difference between a windowsill and the middle of a table.

**Given the same sky they are the same tracker.** Parked together under a clear view the next
morning, all five held 24–29 satellites at hdop 0.5–0.6, and the gap closed completely:

| | …196001 | …273925 | …306832 | …435854 | …608278 |
|---|---|---|---|---|---|
| CEP50 indoors | 7.2 m | 7.0 m | **3.9 m** | 7.0 m | 6.3 m |
| CEP50 clear sky | 2.5 m | 2.9 m | 2.2 m | 4.5 m | **1.9 m** |

The unit that looked twice as good indoors is now mid-table, beaten by two of the four it had
apparently outclassed. Altitude spread fell from 5–13 m to about 2 m on every unit, and the five
place the same spot within 0.4–4.3 m of their common median. (Ninety-one minutes at the parked
300 s cadence is only 22 fixes each, so read the medians and not the tails.)

Two working rules follow. **Check the satellite count before blaming a tracker** — it is
recorded with every fix and it explains most of what looks like a faulty unit. And **a bench
comparison of two trackers in different spots in one room measures the room**, not the
trackers; the only honest comparison puts them side by side.

### Movement mode goes back to sleep after every record

Measured across the four races of 19–20 September 2026. This is the largest data loss the
club has measured, and it is a configuration fault rather than a hardware one.

An ATC700 in `12150 = 1` (Movement) is **not** awake for as long as the asset is moving.
Teltonika's own description of the mode: an acceleration over the `19000` threshold sets an
instant movement status, a second one must follow **within five seconds** to confirm it, and
*after generating a record in On Move the device returns to On Stop unless another movement
event occurs first*. Every record is therefore a fresh test. Pass it and the next record comes
in 10 s; fail it and the next comes in 300 s.

A sailing boat does not reliably pass that test. Percentage of race time inside a gap longer
than 30 s:

| | race 87 | race 88 | race 89 | race 90 |
|---|---|---|---|---|
| MOJITO, RUTX50 | 0% | 0% | 0% | 0% |
| CRACKAJACK, ATC700 on deck | **0%** | **0%** | 43% | 50% |
| FINALLY, ATC700 below deck Sat | 67% | 76% | 46% | 66% |
| SGRECH BACH, ATC700 | — | — | 61% | 75% |
| ATC700-5, below deck on MOJITO | 47% | 65% | — | — |

Two controlled comparisons fell out of the weekend by accident, and between them they pin it:

**Same unit, same mounting, two days.** CRACKAJACK's tracker did not move between Saturday and
Sunday. Saturday was windy with waves and it produced 1,009 fixes and **not one dropout**.
Sunday was flat calm and the same unit dropped out 37 times. Boat motion, measured from its own
fixes, differed by 13% — enough to change the answer completely, because the test is a 50 mG
knock and not a speed.

**Two trackers on one boat.** MOJITO carried the mains-powered RUTX50 on Saturday *and*
ATC700-5 below deck — 3 m apart (median over 887 matched fixes, 95th percentile 5 m), so the
same boat, the same hour, the same SIM. The router missed nothing. The ATC700 beside it was
silent for half of each race.

**Below deck is worse than a flat calm.** The two below-deck units failed on the windy day as
badly as the on-deck unit failed in the calm, although the *boats* were equally lively (motion
index 1.09 below deck on MOJITO against 1.19 on deck on CRACKAJACK). Soft stowage — a bag, a
bunk, a padded locker — damps exactly what the accelerometer is listening for.

Across all five unit-days, the boat was measurably calmer in the minute before a unit went
quiet than when it kept reporting: 13–27% lower on the same index, five out of five in the same
direction.

**What it is not**, each checked rather than assumed:

* *Not the radio.* Below deck and on deck read alike — 39–40 satellites, hdop 0.40, zero failed
  fixes, signal bars in the same proportions. A deck that was attenuating enough to break the
  link would show in the fixes that got through.
* *Not the link.* Nothing ever arrives late. The first fix after a gap is 4.9 s old, the same as
  any other, and the back-fill — which asks Traccar directly for the missing window — recovered
  0–3 fixes per race. Those minutes were never recorded by anything.
* *Not the battery.* FINALLY's unit was on external power and *gained* five points across
  Saturday, and had the largest holes of the day.
* *Not the hut and not Traccar.* The three units' silences do not coincide: all quiet together
  for 9% of race 89 and 11% of race 90, against 7% and 14% expected by chance.

**The signature to recognise:** ragged gaps of 90–330 s while the boat is sailing at 5–6 kn,
with perfect GPS quality either side, and nothing delivered late afterwards. Not a clean 300 s
cadence — the unit escapes and re-enters On Stop at random moments, so only 8–31% of the gaps
run the full parked interval.

**What it cost.** Six to twelve kilometres of each boat's track missing per race, with single
unrecorded hops up to 1.5 km. GPS finish detection needs every mark rounded in order, and a
five-minute hole at 6 kn hides a rounding completely.

#### Basic mode is the fix, but not on its own

`12150 = 0` is **Basic**, which disables the accelerometer and records on a fixed interval
regardless of movement. That is the right behaviour for a boat that is out racing: there is no
state to get wrong.

The trap is the interval. **Basic records on the On Stop block** (`10000`/`10100`) — measured,
not inferred: with On Stop at 10 s and On Move moved to 60 s, a unit in Basic kept reporting
every 10 s. The club had On Stop at **300 s**, so switching the mode alone would have pinned
every tracker at a five-minute cadence and made the weekend's worst case the permanent case.
Basic is only an improvement with `10000`/`10100` brought down at the same time. A `getparam`
cannot settle this, whatever an earlier draft of this file said: it reads the values back, not
which block the firmware honours. Only changing one block and watching the cadence answers it.

**Applied to all five units on 22 September 2026** — `setparam 10000:10;10100:10;12150:0` over
the air (Codec 12 through Traccar, see *Sending commands*), read back on every unit, then
measured. Sitting still on a table, which is the condition that used to produce total silence:

| | stretch | fixes | yield | median gap | longest gap |
|---|---:|---:|---:|---:|---:|
| ATC700-1 | 69 min | 411 | 100% | 10 s | 22 s |
| ATC700-2 | 41 min | 247 | 100% | 10 s | 15 s |
| ATC700-3 | 41 min | 247 | 100% | 10 s | 14 s |
| ATC700-4 | 41 min | 244 | 100% | 10 s | 19 s |
| ATC700-5 | 40 min | 243 | 100% | 10 s | 16 s |

**1,392 fixes against 1,397 expected — 99.6%**, no gap over 30 s on any unit. Against 24–57% of
expected fixes during the races of 19–20 September on the same hardware.

One unit demonstrated the old fault on the bench first, which is worth recording because it
needs no boat to reproduce: three fixes ten seconds apart, the motion flag went `False`, and it
then said nothing for **nine minutes** until it was disturbed.

Continuous 10 s recording costs battery: the five dropped 5–15 percentage points in the first
hour, which is consistent with the ~10 %/h measured before but too short a window, and too
coarsely quantised (`io113` moves in 5% steps), to refine that figure. Flat in about nine hours
either way: right for a race day from a full charge, wrong for a tracker left aboard between
races. Both parameters can be set over the air, so the shape if endurance ever matters is Basic
plus 10 s before racing and Movement plus 300 s afterwards. Remember only the roaming half of
each pair is live — both halves are written above for that reason.

---

## Battery

Measured on the ATC700 (1000 mAh), which is the only unit where it is close.

| Recording interval | Drain |
|---|---|
| 10 s | **~10 %/h** |
| 60 s | ~9 %/h |
| 300 s (parked) | 1.1–1.7 %/h |

**The curve is brutally non-linear.** Going from 10 s to 60 s is six times fewer fixes
and saves barely a tenth of the power, so there is no useful middle setting: the saving
only appears somewhere below 300 s.

**Batching the uploads saves nothing measurable.** An ABBA counterbalanced run — sending
each record immediately against queueing them for a minute, both recording every 10 s —
found the two indistinguishable. GNSS and record generation dominate; how records leave
the device is second order.

On 8 August the ATC700 went from 95% at 08:20 to flat by 17:26, half an hour before a
night race started. The GL units sailed that same nine-hour race for **11 points** of
their charge.

**Power bank support (`116`) is disabled by default** and must be switched on explicitly;
a plugged-in bank does nothing until it is. It needs a USB-C to USB-C cable, and when
enabled the internal cell is deliberately held between 40% and 75%, so do not enable it
on a unit expected to run standalone.

---

## Sending commands

The app can talk to a tracker from **Trackers → Commands** (administrators only). It
sends through Traccar's `custom` command, which carries raw protocol text: Codec 12 for
Teltonika, an @Track AT string for Queclink.

Traccar queues a command while the device sleeps and delivers it on the next connection.
Measured round trips: about **6 s** on a GL holding a long-lived TCP connection, **95 s to
5 minutes** on an ATC700 that wakes on its own schedule.

### What comes back depends entirely on the protocol

**Teltonika replies are the data.** `getparam 10100` returns the values, and they appear
in the console.

**Queclink replies do not arrive.** @Track answers a query with `+ACK:GTRTO` and then a
separate `+RESP` message; Traccar's decoder keeps the acknowledgement and discards the
payload. Verified for a config read, device info and signal strength — every one produced
an ACK alone. Enabling `database.saveOriginal` in Traccar does not help: it attaches raw
text to a *position*, and these replies never become one. Reading them needs
`logger.level=all` and the relay's `tracker-server.log`, or the vendor configurator over
USB.

So on a GL the console can usefully **request a position** (the answer arrives as a fix)
and **reboot**; its queries run on the device but cannot be read back here.

### Factory resets are refused

Queclink `AT+GTRTO=...,4,...` is RESET and **`...,3,...` is REBOOT** — one digit apart. A
factory reset wipes the server address and the tracker stops being findable at all,
recoverable only over USB with the unit off the boat. The app refuses anything matching a
factory reset, and logs the attempt.

### Rebooting a diverging tracker

Measured on a GL: **5 min 45 s with no fixes**, then several minutes at hdop 5 before
quality settles. Configuration survives. Against a divergence that resolved itself in 28
minutes, that is worth about 17 minutes — but 5¾ minutes is roughly a kilometre of
untracked course at 6 kn, so it is viable early in a race and **never on approach to the
finish**, where being blind is worse than being displaced.

---

## Per-model notes

### Teltonika ATC700

Best accuracy, best latency, worst endurance. Reports the richest telemetry of the fleet
— satellites, hdop, pdop, rssi, battery percent in `io113` (not `battery`, which is
volts) — and signals a failed fix honestly with `hdop=100, sat=0`.

Movement detection **is not reliable on a sailing boat, and the August figure here was
misleading.** Above 1 kn the *moving* flag is set 97.9% of the time — but the flag is not what
keeps the unit reporting, and on 19–20 September the same units were silent for 43–76% of every
race. See *Movement mode goes back to sleep after every record* above; that is the trap, and it
is the reason to consider `12150 = 0`. The accelerometer threshold (`19000`) is already at its
most sensitive 50 mG, so there is no sensitivity left to give it. On a mooring the same
sensitivity may keep a unit awake in swell — untested, and a plausible contributor to the
8 August flat battery.

These are the settings the club's five units are running, read back off the devices on
21 August 2026 and identical on all five. Parameter names are the wiki's; the roaming half of
each pair is the one that is live (see the roaming trap above).

| ID | Name | Club value |
|---|---|---|
| 102 | Power saving mode | 5 |
| 116 | Power Bank Support | 0 — disabled |
| 121 | GNSS source | 31 — all constellations |
| 128 | GNSS location source | 0 |
| 1000 | Open link timeout | 30 s |
| 1010 | GNSS position fix search timeout | 120 s |
| 1011 | Periodic record priority | 2 |
| 1012 | Power from USB | 1 |
| 10000 / 10100 | On-stop record interval | **10 s** (300 s before 22 Sep 2026) — the interval Basic records on |
| 10004 / 10104 | On-stop min saved records | 1 |
| 10005 / 10105 | On-stop send period | 0 |
| 10050 / 10150 | Moving record interval | 10 s |
| 10054 / 10154 | Moving min saved records | 1 |
| 10055 / 10155 | Moving send period | 120 s |
| 12150 | Asset movement mode | **0 — *Basic*** (1, *Movement*, before 22 Sep 2026). Basic ignores the accelerometer and records on the On Stop interval |
| 19000 | Accelerometer sensitivity threshold | 0 — which is 50 mG, the most sensitive |

The 120 s moving send period is a ceiling, not the cadence: min saved records is 1, so a
record goes as soon as it exists, and with `1000` at 30 s the socket is still open when it
does. That is why the measured delivery lag is a second and why batching saved nothing.

**`1000` open-link timeout matters more than it looks, and it is why sleep depends on what
the boat is doing.** Moving, records come every 10 s and the 30 s timeout never expires, so
the socket never closes, the modem never sleeps and `102` cannot engage however it is set.
Stopped, records come every 300 s, the link does drop, and the unit sleeps properly between
them.

**So while it is parked, every wake is a restart.** Uptime reads about 20 s when it answers a
command, and the reset counter climbs with ordinary use — 989 on the unit the club has had
longest against 139–147 on the four newer ones. A high count is age, not a fault.

### Queclink GL521MG

The workhorse. One minute is a **hard protocol floor** — `<Continuous Send Interval>` is
1–1440 *minutes* — so 60 s is not a conservative choice, it is as fast as scheduled
reporting goes.

But on-demand polling with `AT+GTRTO=gl521m,1,,,,,,FFFF$` returns a fresh fix in about a
second, and can be sustained at **20 s intervals** with 100% yield. Faster saturates it:
at 5 s it returns duplicates of a cached fix with lag climbing to 57 s. Twenty seconds is
a floor set by the tracker, not the transport.

Polling at 20 s costs **+1.1 %/h**, roughly doubling consumption — so a whole nine-hour
race polled continuously costs about 19% of the battery. That is the cheapest way the
club has to get 20-second, 4-second-old positions.

The scheduled 60 s report arrives 45 s late because the device takes its fix and then
holds it until the top of the minute. It is not GPS acquisition: an on-demand request
answers in a second.

AGPS (`AT+GTCFG` field 18) is disabled. It would speed acquisition after a restart; the
manual warns some operators cannot serve the URL fetch it needs, and the club's SIM roams.

### Jimi LL301

CEP50 41 m, CEP95 138 m, excursions to 860 m, six satellites, and it reports a median
speed of 0.5 kn while stationary. **Not suitable for GPS finish detection.** Reports no
hdop.

### Teltonika RUTX50

The router on Mojito. Mains powered, pushes in real time at zero delay, and reports an
identical position between fixes — so its accuracy figure is not comparable with the
battery trackers. Immune to the degraded spells that affect them, which is what showed
those were a cellular problem rather than a relay one.

---

## Degraded reporting spells

Sporadically, a quarter to half of all fixes arrive more than a minute late, with p90
jumping from ~48 s to ~105 s. Observed on the GL units and the LL301 across several days,
day and night, sometimes on two units at once.

It is **not the relay**. Through the worst of it the mains-powered RUTX50 stayed at 0.0%
late and Traccar was never silent for two minutes together. The late fixes are scattered
rather than bursty — 177 late fixes in 175 separate runs — so it is individual uploads
missing their slot rather than a dropped connection. The ~105 s figure is about 45 s plus
one 60 s cycle: a failed upload retried on the next attempt.

The likeliest explanation is carrier re-selection on the multi-network SIM, which would
explain why it spares the router on its own connection. Unconfirmed.

---

## Measuring it yourself

`scripts/tracker_latency.py` reads all of this from Traccar, which keeps about ten days
of history including `fixTime` and `serverTime` for every position:

```
python scripts/tracker_latency.py                        # last 12h, every device
python scripts/tracker_latency.py --daily --days 10      # a row per device per day
python scripts/tracker_latency.py --compare <imei> --control <imei> --flashed 2026-08-18
```

Two lessons are built into `--compare`, both learned the hard way. **One night proves
nothing** — the same tracker has run between 0% and 49% late across ten consecutive
nights with nothing altered on it — so it prints the whole baseline rather than a
before/after pair. And **do not start the "after" window at a reboot**: a unit that has
just restarted spends its first half hour catching up, which once read as a sixfold
regression that did not exist.

Always keep a control device. When two independent trackers degrade on the same night the
cause is upstream of both, and without a control that reads as a fault in whichever one
you happened to be looking at.

---

## What is still unknown

* Whether a reboot actually **clears** a diverging receiver. The cost is measured; the
  benefit is inferred, and cannot be tested without a live fault.
* Whether the 50 mG accelerometer threshold keeps an ATC700 awake on a mooring in swell.
* Whether Basic mode holds the link open the way a moving unit does, or re-registers on the
  roaming SIM for every record — which would put the delivery lag up from a second. The
  stationary test says the *records* arrive on time; their lag has not been measured.
* What Basic costs in battery over a full day. The first hour says 5–15 points, which the
  quantisation of `io113` will not resolve any finer.
* Whether 99.6% on a table holds up at six knots in a seaway. The fault it replaces was
  invisible on a bench, and so might its successor be.
* Whether enabling AGPS on the GL units shortens recovery, and whether the roaming SIM
  can serve the URL fetch it needs.
* What the degraded reporting spells actually are. `getops` was sent twice and never
  replied.

---

Copyright © 2026 CapeNet Ltd. All Rights Reserved.
