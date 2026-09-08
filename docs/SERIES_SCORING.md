# Series scoring

The app can group races into a series and calculate IRC and YTC series scores separately, split by configured rating-band class when class bands are configured.

## Creating a series

Open **Series** and create a series with a name and optional notes. Configure:

- up to three IRC rating-band classes;
- up to three YTC rating-band classes;
- the default start plan;
- the series discard profile;
- the minimum number of scored races needed to constitute the series.

A race can be assigned to a series when the race is created or later from the race **Course & start** tab.

## Deleting a series (v0.276)

A series can be deleted from the **Series** list, or from the bottom of **Edit
series details** on the series itself — but **only once no races are in it**. A
series that still holds racing shows *In use* instead of a Delete button, and the
series page says how many races are in the way.

To remove one that is in use, first empty it: open each race's **Course & start**
tab and set its series to another one or to *(none)*, or delete the races. Then
the button appears.

The restriction is deliberate rather than a limitation of the delete. A series is
not just a label on a group of races — it carries the rating bands, the start plan
and the discard profile those races were **scored under**. Deleting it with races
still attached would drop them out of the standings while they went on claiming to
belong to it, with no record of what they had been scored against; deleting the
races along with it would remove a season's racing from a button on a list page.

## Race results used for series scoring

The app uses each race’s corrected result tables:

- IRC series uses IRC corrected results only.
- YTC series uses YTC corrected results only.
- If class bands are configured, standings are shown per rating class.

A boat without the relevant race-entry rating is not included in that scoring table for that race. Ratings are taken from the race-entry snapshots, not from the current boat database.

A race is not included in the series scoring while any entry in that race is still marked `RACING`. Once every entry has a non-racing status such as `FINISHED`, `DNC`, `DNS`, `DNF`, `RET`, `OCS`, `DSQ`, `DNE` or `DGM`, that race can contribute to the series tables.

**Pursuit races** score differently: there is no time correction, so a pursuit race in a series scores by the **finishing order recorded on its Finish & positions tab** — first across the line takes first place, and so on — exactly like the places in a normal race. See "Pursuit races" in the Race Officer's workflow guide for how those positions are recorded.

## Series constitution threshold

Each series has a configurable minimum number of scored races needed to constitute the series. The default is 3 races. The setting is shown in the same **Series discards** setup box as the discard profile.

The app still calculates provisional series standings before the threshold is met, but marks the table as **Not yet constituted**. A class table is marked constituted once the number of series races with entries in that IRC/YTC class table is at least the configured minimum. Races where nobody in the class finishes are still included, and the affected boats receive their DNC/DNF/RET-style score rather than the race disappearing from the table.

## Discard profiles

Each series has its own comma-separated discard profile. The first value applies when one race has been completed, the second value applies when two races have been completed, and so on. If more races have been completed than values in the list, the last value is reused.

Example:

```text
0,0,1,1,1,1,2,2,2
```

This means:

- 1 completed race: 0 discards;
- 2 completed races: 0 discards;
- 3 to 6 completed races: 1 discard;
- 7 or more completed races: 2 discards.

If a boat has two or more equal worst scores, the app discards the earliest equal worst score first, matching RRS A2.1.

Scores marked **DNE** or **DGM** are treated as non-excludable, following RRS 90.3(b). They remain in the total even if they are among the worst scores.

## Appendix A scoring behaviour

The series scoring is intended to follow the RRS Appendix A Low Point System for the common club-racing cases handled by the app:

- Races are scored by corrected-time finishing place for IRC/YTC.
- First place scores 1 point, second scores 2 points, and so on.
- Race ties on equal corrected time are scored using equal points for the tied places, following RRS A7. Corrected-time ties are tested at the same whole-second precision displayed in the results table, so two boats showing the same corrected time receive tied places and equal points.
- Boats that are entered in the series but do not have a scored finish in a race included in that series/class table are scored one more than the number of boats entered in that series/class table.
- Series totals are the sum of non-discarded race scores.
- Series ties are broken using RRS A8.1, by comparing each tied boat’s non-excluded race scores from best to worst.
- If a tie remains, it is broken using RRS A8.2 by comparing the tied boats’ scores in the last race, then the next-to-last race, and so on, including excluded scores.
- If the tie still remains after Appendix A8.2, the boats remain tied in the displayed rank.

## Overall tables across classes

When two or more configured classes use the same rating rule and share the same scheduled start time, the app also produces an **Overall** table for that rating rule. For example, if IRC Class 1 and IRC Class 2 both start together, the app shows **IRC Overall series** and **IRC Overall results** as well as the separate class tables.

Overall tables are not produced when the classes are on different scheduled starts, because their elapsed and corrected times are not directly comparable as one start.

## Current limitations

The app does not yet provide full protest/redress scoring workflows. Manual status codes can be entered for common race-committee outcomes, including DNC, DNS, OCS, DNF, RET, DSQ, DNE and DGM. Redress and scoring penalties should currently be handled by editing results outside the app or by a future scoring-adjustment feature.

## Export

Series results can be exported as CSV from the series detail page. Discarded scores are shown in square brackets. Non-excludable scores are marked `NE`.

## Series CSV export

The series CSV export contains the series standings first, followed by the race-by-race result tables for the races in that series. This gives one file that can be used for checking both the series calculation and the underlying race results.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
