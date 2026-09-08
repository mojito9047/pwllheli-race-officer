# Start-hut weather station

The app can use either live wind from the start-hut weather station or manual wind entered by the race officer.

## Source selection

In **Settings → Weather Station**, choose one of two sources:

- **Weather station polling** — the app polls the configured station URL/IP and stores live samples.
- **Manual input** — the app uses the entered TWD and TWS values and does not poll the weather station.

Manual input is useful when the station is offline or the RO wants to use an official wind value from another instrument.

## API endpoint

Configure the station IP or full URL in **Settings → Weather Station** when using weather-station polling.

Examples:

```text
10.10.10.106
http://10.10.10.106/get_livedata_info?
```

The app calls `get_livedata_info` and reads `common_list` values:

- `0x0A` — wind direction.
- `0x0B` — wind speed.
- `0x0C` — gust speed.

Wind speed is converted to knots from common returned units such as mph, m/s, km/h or kt.

## Background polling

When **Weather station polling** is selected, polling runs continuously in the background while the app is running. The course recommendation page does not need to be open for wind history to be collected.

Stored samples are used for:

- Course recommendation.
- Race-page leg analysis.
- Public competitor page course analysis.
- Wind history chart, with seven labelled scale positions for both TWD and TWS. TWS is shown as horizontal bars from 0 kt to the measured speed.
- The **record of conditions** on a finished race: once every boat has stopped racing, the public race page's Chart and Course analysis tabs switch from live wind to the **average TWD and TWS between the warning signal and the last boat finishing**. Direction is averaged circularly, so a wind oscillating either side of north averages to north rather than to south.
- The **wind gauge on the replay chart**, which shows the wind as it was at whatever moment the playback is showing rather than the wind now.

## How long samples are kept

Ambient samples are pruned after 24 hours, but any sample falling within **an hour either side of a race** is kept indefinitely. The wind a race was sailed in is part of that race's record: the two uses above both read it back long after the day it blew, one of them potentially years later. Trimming everything to the last day would leave every race older than yesterday with a wind gauge and no wind to put in it.

## Wind dial

The current wind is also shown as an analog TWD/TWS dial — on the race-office dashboard, and on the public competitor home page's header. Both use the same instrument and refresh every few seconds.

The public dial is deliberately **not** wired to the weather-station status line, so station errors are never shown to competitors; with no wind available it simply reads `—°` / `— kt`. The Wind tab carries the numbers and the history plot.

## Wind direction offset

Use the wind-direction offset if the weather station is not physically aligned with true north. The app applies the offset before storing/displaying the wind direction.

## Important wording

Public and race pages label the wind as **wind at the hut**. This avoids implying that it is representative of the whole race course.

## Troubleshooting

- Use **Settings → Weather Station → Save and test selected weather source** to check the URL.
- Confirm the race-office PC can ping/browse to the station.
- Check whether the station returns wind speed in a unit the parser recognises.
- If no samples appear, restart the app after saving settings to restart background polling.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
