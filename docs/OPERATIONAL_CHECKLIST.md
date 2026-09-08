# Operational checklist

Use this checklist before, during and after racing.

## Before race day

- Install/test the app on the race-office PC.
- On the hut PC deployment, run `deploy\windows\status_startup_task.cmd` and confirm the scheduled task is running.
- Confirm PC clock is synchronised by NTP/GPS.
- Check horn interface wiring and isolation.
- Test manual horn input sensing if the ProLog-style DTR/RTS/DCD/CTS interface is fitted.
- Test central audio output to the VHF/audio path.
- Test weather station polling and wind-direction alignment.
- Test video recording and live finish-camera preview.
- If using GPS tracking: charge the trackers, confirm each is assigned to its boat on the **Trackers** page, set a frequent race-day reporting interval, and check each shows a green last-reported dot. If the automatic horn matters, check **Settings → GPS tracking** says *Push: working* — a mismatched push token still polls, so nothing looks wrong except a late horn.
- Use **Backup / restore** from the side menu, or copy the `data/` folder manually. At minimum back up `data/race_officer.db` and, if trackers are in use, `data/track_positions.db`.

- For a race **not finishing on the club line** &mdash; an ISORA passage race, say &mdash; set the **Finish line** on the Course & start tab before the start. GPS finishes are detected on the line the race is set to, and on the wrong one no boat finishes at all.

## Before the start

- Open **Settings** and confirm horn, audio, weather and video status.
- Open **Boats** and check active boats and IRC/YTC ratings.
- Open **Series** if the race is in a series; check IRC/YTC class bands, numeral class flags and the default start plan.
- Open **New race** or select an existing race from **Races**.
- On **Course & start**, set race name, series and first warning-signal time.
- Confirm the race start plan. Use the series default or set a race-specific override.
- Select, recommend or build the course.
- Add entries. For a series race, confirm the entries are synchronised across all races in the series.
- Check the scheduled signal plan and graphical flags.
- Open the public competitor page if required.

## During the start sequence

- Keep the race-office PC clock visible and do not let the PC sleep.
- Supervise automatic horn/audio signals.
- Check that displayed flags match the physical flags hoisted.
- Use manual controls for recall, postpone or abandon if required.
- Watch/log any manual horn input events.

## During the race

- Keep weather and video health indicators under observation.
- If the **clubhouse display** (`/bar`) is up on the bar TV, it needs nothing from you: it follows the current race, zooms to the boats still racing, cycles the leaderboards and cuts to the camera at the start, at each rounding of the ODM and at each finish. See `docs/BAR_DISPLAY.md`.
- If using GPS tracking, watch the fleet on the course chart and the **Position on the water** list on the **Course & start** tab (order on the water, not handicap-corrected); if armed for GPS auto-finish, confirm each proposed finish against the finish video.
- Competitors watching the public race page see the same fleet on its **Chart** tab, and can put a **corrected-time** order beside it — IRC or YTC, projected from each boat's pace so far. It is labelled an estimate on the page and is not a result; expect to be asked about it.
- Use **Entries & finish times** for finish recording.
- Use the blue **Finish** button where possible so horn, log, finish time and video clip are linked.
- If using a manual horn event for a finish, assign the event from the horn/race log.
- Edit race-entry IRC/YTC ratings in the entries table only when a race-specific value is required.

## After the race

- Review provisional IRC/YTC results by class.
- Check finish video links where needed.
- Export race results CSV.
- Review series results if the race belongs to a series.
- Export series results CSV if required.
- Use **Backup / restore** after racing, selecting **Videos** as well if saved video evidence is required. The video option can create a large ZIP, so allow time for it.
- **Public videos upload in the background, one at a time.** On a slow hut connection a start clip of tens of megabytes can take twenty minutes, and finish clips queue behind it — so do not wait on the last one before closing up. The evidence copy is on the hut PC either way; only the public web copy goes to Cloudflare. If one fails, `docs/TROUBLESHOOTING.md` has the causes.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
