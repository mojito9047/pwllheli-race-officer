# The Virtual Race Officer

**VRO** — the Virtual Race Officer — is a page for running a race from a boat: one text box, one thread of
conversation, and a **Yes** button. It is meant for a race officer on the water
with a phone in one hand and a tiller in the other, and it does exactly what the
race sheet does — through the same code — rather than being a second way to run a
race.

```text
https://pro.pwllhelisailingclub.org/vro
```

It is behind login, like every race-office page, **and** behind a per-user
permission. Nothing about it is public.

## Who may use it

Being an administrator is not enough. **Settings → Users** has a *VRO*
tick box on each account, and without it the page answers 403 and says which
permission is missing. The endpoints check it too, not only the page: a POST is
a POST. An account that has it gets a **Virtual Race Officer** link in the side menu. The page also answers at its old address, `/onwater`, so a phone with it bookmarked keeps working.

That separation is deliberate. The club has settled that a race may start with
nobody watching the line — an alternative penalty is applied to a boat later
judged over by video — but **who** may drive the racing from a phone is a
different decision, and it is made one account at a time.

## The page

The top of the page is two lines and a rolled-up chart, because every line there
is a line of conversation pushed off a phone screen:

- The race the conversation is about, and what it is doing (*Counting down*,
  *Racing*, *Race finished*, *No start time*).
- **The countdown to the first gun**, turning red inside the last five minutes.
- The course: its number, its length, and about how long it should take round in
  the wind that is blowing. Then the wind itself.
- **The course board** — the same marks and hands the race sheet and the
  clubhouse display show, and the thing you read out over the VHF, sized for a
  phone: `Fp 2p Os Fp 1p Os`.
- **Chart** — closed until you open it. It draws the marks and the legs from the
  page itself: no map tiles, no requests, nothing downloaded over the same 4G the
  hut is using.

All of it follows the race: change the course and the board, the chart and the
expected time change with it, without a reload.

Everything below is the conversation, the box, and the two buttons.

## How a command works

1. You type a sentence.
2. The app says back what it would do, in full.
3. Nothing happens until you press **Yes** or type one.

The read-back is the whole safety model. It is the only check that catches the
mistake where the app understood something perfectly and it was not what you
meant — a race at 11 tomorrow rather than today, the wrong fleet, the right
command aimed at yesterday's race.

Questions are answered straight away, because there is nothing to undo and
asking somebody to confirm a question they just asked is noise.

```text
create a race called Sunday Points at 11am, in the summer series
  → Create 'Sunday Points' as a standard race, first warning signal 10:55,
    first gun 11:00, in series Summer Series 2026, with GPS finishes armed and
    recorded automatically. Course 29 (4.0 nm), chosen for the wind now
    (131°T, 12.0 kn). Say no if you would rather pick your own.
  → Yes
  → Race #614 'Sunday Points' created. First gun 11:00, course 29.
```

Agreeing in words works as well as the button: *yes*, *ok*, *go ahead*, *do it*.
A sentence that begins with yes but carries a change — *"yes, but make it half
past"* — is an instruction, not agreement, and is read as one.

## Finishes record themselves

A race created from this page has **Arm GPS auto-finish** and **Auto-confirm
(unmanned)** both switched on, and the reply says so. The premise of the page is
that nobody is in the hut to press **Finish**, so the app watches the fleet
across the line and records each finish as it happens. They are the ordinary tick
boxes on the race sheet's *Entries & finish times* tab and can be turned off
there.

GPS gives the approximate time and order; the finish video remains the arbiter,
and every recorded finish stays editable afterwards. Set a frequent tracker
reporting interval on race days or the order between close boats will be too
coarse — see [`TRACKING.md`](TRACKING.md).

## What it can do

| Say | What happens |
| --- | --- |
| *create a race at 11*, *new pursuit at 7, 90 minutes* | A race sheet, with its first gun and its series. A course is suggested for the wind and set if you agree. |
| *use course 4*, *put the start back ten minutes*, *bring it forward 5* | The race's course and first warning signal. |
| *add all the boats*, *add the fleet*, *add Mojito and Sgrech Bach*, *same boats as last time* | Entries: every active boat, boats by name, or the same boats as another race. |
| *make me a windward-leeward twice round, O to 4* | A made-up course, read back mark by mark. Say *change the second mark to 2, starboard* and it re-reads the whole course back. |
| *put it in the summer series* | The series an existing race is scored in. |
| *what is Mojito rated?*, *what's GBR4822R?* | A boat's IRC and YTC ratings — from the club's boat database, and from the RORC IRC listing and the YTC sheet when it is not in there or has no rating. |
| *add Halcyon to the boat database* | A boat the club does not have, from those listings. **An existing record is never overwritten.** |
| *how many courses are there*, *where is mark 4*, *what sail at 12 knots* | A look-up in the club's own data — marks and their positions, the fixed courses, one course's legs and distances, the boat database, the series, recent races, where the fleet has got to, the polar and its sail chart. |
| *postpone*, *hold the start, the wind's gone*, *further signals ashore* | AP up, two horn blasts, and the start sequence held. **Say when the wind is back rather than what time to start** — see below. |
| *lower AP*, *we're ready, start it again* | AP comes down at the next whole minute — one horn blast then, the warning a minute later, the gun five after that. Say *lower AP at twenty past* to choose the minute. |
| *shorten at mark 4*, *finish them at the windward mark* | A shortened course — two horn blasts and the spoken announcement. |
| *status*, *how are they getting on* | The race, its gun, its course, how long it should take, and the entry counts. |
| *results of the night race*, *who won on Wednesday* | The finishing order of a race that has been sailed. |

**Postponing is the one worth knowing about.** The club used to delay a start
by moving the start time, which signals nothing to the fleet — and if the
sequence is running, drops the rest of it. Ask it to postpone instead and it
flies AP; ask it to lower AP when the wind is back and **the warning signal
follows one minute later, by rule**. So it never asks what time to restart at:
not having to know that is the whole point of the flag. On a boat, in a dying
breeze, that is the difference between a correct postponement and a guess.

It reads back all three times — AP down, warning, first gun — and they are
always whole minutes, because a gun at 14:21:41 is not something a fleet can
count down to.

It will also answer questions about the racing in words, from what the app
itself knows: the wind now and how it has shifted over the last hour, which
boats are entered, how long a course should take, which course would give more
reaching, and whether the trackers are reporting.

## What it will not do

- **Sound a horn on command.** There is no *arm* step and no *fire now*: with
  start automation switched on in Settings, the app fires the warning,
  preparatory and start signals itself, off the race's stored warning signal.
  **So setting or moving a start time from the water moves the horn with it**,
  and the read-back says so. With start automation off, nothing sounds by
  itself whatever time is set.
- **Signal a recall.** The club starts races with nobody watching the line and
  reviews the video afterwards, so there is nothing for it to signal.
- **Abandon a race**, or record a finish on command. Finishes are recorded
  by GPS, not by asking — see above.

Asked for any of these it says so plainly and offers the nearest thing it can
actually do. It never picks a nearby command to be helpful.

## Ratings

Three places hold a boat's rating and they are not the same thing. The **boat
database** is what a race actually scores on, because entering a boat snapshots
its rating at that moment. The **RORC IRC listing** and the **YTC sheet** are
where that figure came from, and either may have moved since. Asked what a boat
is rated, the app says all of it, by name or by sail number:

```text
what is Mojito rated?
  → MOJITO (GBR4822R) is in the boat database: IRC 1.084 and no YTC rating.
    The IRC listing has MOJITO (GBR4822R) at TCC 1.084, certificate IRC/1234.
```

A boat the club does not have is looked up in the same two listings the race
office reads when adding one, and the app offers to add it:

```text
what is Halcyon rated?
  → 'Halcyon' is not in the club's boat database. The IRC listing has Halcyon
    (GBR7777) at TCC 1.021… Say 'add Halcyon to the boat database' to add it.
```

**An existing record is never overwritten.** The import screens on the Boats
page do update one, and that is right there — somebody is looking at the listing
row beside the record and has decided. From a boat, without the two side by
side, the existing record wins and the reply says what it already holds. Taking
a listing figure over the club's own record is how a season gets scored on the
wrong number.

## Asking the app rather than being told everything

The club has 24 marks, 67 courses, a boat database and a polar. None of that
is sent with every command — it would be a page of text on every sentence
typed on 4G, and most of it is not wanted most of the time. What is always
relevant goes in the facts; the rest is a **look-up**, which the app answers
itself and puts in the thread, so the next question can be answered from it.

```text
how many courses are there?
  → The club has 67 fixed courses: 1 (4.6 nm), 2 (7.4 nm), 3 (10.3 nm)…
where is mark 4?
  → 4 (Mark 4) at 52° 51.500'N 04° 24.250'W.
what sail would we carry at 12 knots?
  → at 12 kn: J2 from 36° to 60°, J1 from 65° to 80°, A0 from 85° to 100°…
```

## The interpreter

A model reads what you type — one configured in **Settings → Virtual Race
Officer**. There is nothing behind it.

**Without one the page is unavailable, and says so.** The app has a small
built-in grammar that understands about six sentence shapes, and it used to
answer when no model was configured or one could not be reached. On a page whose
whole premise is *type what you want to do*, that reads as an app that simply
cannot understand anything — and worse, somebody on the water cannot tell a
sentence it will not understand from one it has misunderstood. So the club's
decision is that this feature does not exist until an interpreter is configured
and answering. With none, the page says that plainly and offers no box to type
in; the endpoints refuse in the same words.

What that costs: the hut's outbound internet can fail while the page itself is
perfectly reachable, and the racing then has to be run from the race sheet. That
is the trade, made deliberately — half a feature that works for six sentences is
worse on the water than a clear "not available".

If the model was configured and its last request failed, the page and Settings
say so and give the provider's own reason, rather than letting a billing problem
look like a stupid app.

## The conversation

- It remembers the last half hour with the same person, so *"make it a pursuit"*
  or *"add the fleet to that one"* has something to refer to.
- A question the app asks can be answered in a word: asked *"standard race or a
  pursuit?"*, answering *standard* re-issues the whole command with the time,
  the name and the length it already had.
- While a proposal is waiting, it is what the conversation is about. Asking for
  a longer course changes **that** proposal rather than something else.
- Only one proposal is live at a time, and it lapses after five minutes.
- **A refresh does not lose it.** On a boat that is a dropped signal or a locked
  phone, not a decision to start again: the page reloads the conversation and any
  proposal still waiting comes back with its Yes button live.

## What is recorded

Every command, read-back and result is written to the activity log with the
account that gave it, and the conversation itself is kept in the race database
for a fortnight before being cleared. Sending the same command twice does it
once — the phone's retry after a dropped reply is the same command, not a second
race.

## When a course has not been chosen

A new race stores a fixed course number as a fallback so the chart and the leg
analysis have geometry to work with. That is not a decision, and the app no
longer treats it as one: until somebody chooses a course, the race sheet and the
competitor page both say **Course not set**, and **the spoken VHF course
announcement stays silent**. A fleet sent round a course the race officer never
picked is a general recall at best.

Creating a race from the water offers a course for the wind at that moment and
sets it if you agree, so the ordinary path does not leave a race in that state.

## See also

- `docs/RACE_OFFICER_WORKFLOW.md` — the same jobs done on the race sheet.
- `docs/SETTINGS_AND_ADMIN.md` — the permission, and the interpreter settings.
- `docs/FLAGS_AND_START_SEQUENCE.md` — what the horn and the flags do.
