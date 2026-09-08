# Flags and start sequence

The app now builds the displayed flag state from the active signal plan. A race can use one start for all classes or up to six starts with different classes assigned to each start.

## Timing basis

The race sheet stores the **first warning-signal time** in the `races.start_time` database column. The first actual start is calculated as five minutes after that warning. Start-plan offsets are measured from the first actual start.

Example:

```text
First warning signal: 10:55
Start 1 offset: 0 minutes   -> warning 10:55, start 11:00
Start 2 offset: 5 minutes   -> warning 11:00, start 11:05
```

This means Start 2's warning signal can occur at the same instant as Start 1's start signal. The scheduler treats this as one combined horn sound when the rules require the same one-sound signal.

## Class flags

- Series setup limits class flags to **Numeral 0** through **Numeral 9**.
- The graphical setup page shows a preview of each numeral pennant.
- The live race-office and competitor/public flag panels use the uploaded numeral pennant images.
- If several classes start together, their numeral pennants are displayed together, stacked when required by the layout.
- Flags that are not currently up are not displayed.

## Preparatory flag

- The app currently uses **P** as the preparatory flag.
- The live flag panel uses the uploaded International Code Flag P image.
- P is only displayed between the preparatory signal and the one-minute signal for the relevant start.

## Scheduled stages for each start

For each configured start, the RRS 26-style sequence is:

| Time before that start | Signal | Displayed flags after signal |
|---:|---|---|
| 5 minutes | Warning signal | That start's class numeral pennant(s) up |
| 4 minutes | Preparatory signal | That start's class numeral pennant(s) + P up |
| 1 minute | One-minute signal | That start's class numeral pennant(s) up; P down |
| 0 minutes | Start signal | That start's class numeral pennant(s) down |

The Start console scheduled signal-plan table greys out stages that have already passed. In a rolling-start sequence, the plan may show combined rows where one horn sound covers two scheduled actions at the same time.

### Pursuit races

A **pursuit race** reuses this same sequence for its first (slowest boat's) start, flying the normal flags and defaulting to class 1 (the numeral-1 pennant). After that first start there is a single start signal at each subsequent boat's computed start time, and one finish signal at the end of the fixed period — there are no per-class flags and no individual finish times. See "Pursuit races" in the Race Officer's workflow guide.

## Race-office and public flag boxes

The flag box is a white panel with a grey outline. The same dynamic flag data is used by:

- the race page,
- the Start console area,
- the public competitor page.

The flag box reflects the configured start plan. It does not show inactive class flags.

## Recalls, postponements and abandonments

The app has manual controls for:

- Individual Recall.
- General Recall.
- Postpone.
- Abandon.

These are logged as race events. The current visual flag panel models the normal start-sequence class/P flags plus Code flag S for a shortened course (below), but does not yet model every recall/postponement/abandonment flag combination. The race officer remains responsible for hoisting and lowering those flags correctly.

## Shortened course (Code flag S)

When the race officer shortens the course from the **Shorten course** tab, the app displays International Code flag **S** (a blue square on a white field) in the flag panel on both the race-office page and the public competitor page, and keeps it up until all boats are no longer racing. It also sounds two horn blasts followed by a spoken announcement. As always, this is a display aid — the race officer is responsible for physically flying flag S. See the shortened-course section in the Race Officer's workflow for the full behaviour.

## Future improvements

Potential next additions:

- Configurable preparatory flags other than P.
- Full displayed-state model for AP, X, First Substitute, N and other race-management flags.
- Audio prompts for physical flag handling as well as horn/audio signals.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
