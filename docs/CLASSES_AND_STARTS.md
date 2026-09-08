# Rating classes, multiple starts and series entries

Series setup defines the race classes used for IRC and YTC results, and the default start plan used by races in that series.

## Graphical rating-band setup

The series page uses graphical tables rather than free text.

Each series can have up to:

- three IRC classes,
- three YTC classes.

For each class, tick **Use** and enter/select:

- class name, for example `IRC1` or `YTC2`,
- class flag from the dropdown, limited to **Numeral 0** through **Numeral 9**,
- optional lower rating limit,
- optional upper rating limit,
- whether each limit is inclusive.

Leave a lower or upper rating blank for an open-ended band. For example:

- `IRC0`: lower rating `1.100`, no upper limit,
- `IRC1`: lower rating `1.000`, upper rating `1.099`,
- `IRC2`: no lower limit, upper rating `0.999`,
- `YTC0`: no lower limit, upper rating `800`,
- `YTC1`: lower rating `801`, upper rating `900`,
- `YTC2`: lower rating `901`, no upper limit.

The app evaluates the bands separately for IRC and YTC. A boat can therefore be in one IRC class and a different YTC class.

## Graphical default start plan

Each series can have up to six starts. For each start, tick **Use**, enter the start name and offset in minutes from the first actual start, then tick the classes that start on that signal.

Example:

- Start 1, offset `0`, classes `IRC1`, `IRC2`,
- Start 2, offset `5`, classes `YTC1`, `YTC2`.

Race setup stores the time of the **first warning signal**, not the first start. The first actual start is five minutes after the first warning signal. A start offset of `5` therefore starts five minutes after Start 1 and its warning signal occurs at the same time that Start 1 starts.

Each race normally uses the series default start plan. On the race sheet, untick **Use the series default start plan** to edit a race-specific start plan.

## Results

Race results are split by rating system and class. Elapsed time for a result row uses that class's configured actual start time, unless an individual entry start override has been entered.

IRC and YTC ratings used for results are stored on the race entry. They are copied from the boat database when the boat is added to the race, then remain fixed for that race unless edited in **Entries & finish times**.

This means:

- updating a boat's rating in the boat database does not silently change existing race results,
- a race can use a race-specific rating when needed,
- a newly created race in a series snapshots the ratings current at the time the entry is inserted.

## Series entry synchronisation

For races that belong to a series:

- adding a boat to one race in the series adds it to all other races in that series,
- creating a new race in a series automatically adds the competitors that already appear in the other races in that series,
- moving/saving a race into a series triggers the same series-entry synchronisation.

Boat-database entries snapshot the current boat database IRC/YTC ratings when they are inserted into each race.

## Signals and flag display

The central start-sequence scheduler creates horn and audio events for each configured start. In a multi-start race the event labels include the start name so logs show which signal belongs to which start. Rolling starts are combined where the same horn sound serves two signals.

Class flags are selected from Numeral 0 through Numeral 9. The race-office and competitor-page graphical flag boxes use the configured numerals from the active start plan. Flags that are not currently up are not shown. The preparatory flag is P.

## Backwards compatibility

Older stored text-style class/start settings are still parsed internally so existing databases can load. Old free-text flag names such as `YTC 1 flag` are normalised to `Numeral 1` where possible.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
