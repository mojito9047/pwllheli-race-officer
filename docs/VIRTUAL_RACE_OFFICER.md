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

It is laid out as a chat, filling the screen: the race strip pinned to the top,
the conversation between, scrolling on its own, and the box to type in pinned to
the bottom where a thumb is. The race officer's messages are on the right in
blue and the app's on the left, each with the time it was said. A reply that
reads something back, or asks a question, has a blue rule down its left edge,
one that did something a green one, and a problem a red one. While a read-back
waits for an answer, **Yes** and **No** are docked just above the box, and the
shortcuts (*Status*, *Shorten…*) are put away until it is
answered.

The strip is near-black, like the race sheet's countdown, and is two lines and a
rolled-up chart, because every line there is a line of conversation pushed off
a phone screen:

- The race the conversation is about, and what it is doing (*Counting down*,
  *Racing*, *Race finished*, *No start time*).
- **The countdown to the first gun**, turning amber inside the last five minutes.
- The course: its number, its length, and about how long it should take round in
  the wind that is blowing. Then the wind itself.
- **The course board** — the same marks and hands the race sheet and the
  clubhouse display show, and the thing you read out over the VHF, sized for a
  phone: `Fp 2p Os Fp 1p Os`.
- **Chart** — closed until you open it. It draws the marks and the legs from the
  page itself: no map tiles, no requests, nothing downloaded over the same 4G the
  hut is using.

All of it follows the race: change the course and the board, the chart and the
expected time change with it, without a reload — and that includes a change made
anywhere else in the app. A course changed, a start moved or a course shortened
on the race sheet reaches this page within five seconds while the phone's screen
is on (`/api/assistant/header`), applied in place so nothing half typed is lost.

Everything below is the conversation, the box, and the two buttons.

**While it works, it says so.** A sentence can take two or three calls to the
interpreter and fifteen or twenty seconds. The moment it is sent, a dashed bubble with three moving dots
appears saying what the app is doing — *Reading that*, *Searching the marks for
a course*, *Getting the results*, *Working out the answer* — and counting the
seconds; after thirty it says it is slower than usual. The page asks the hut
what is happening every second and a half (`/api/assistant/progress`), which
passes through Cloudflare and the relay like any small request.

**Replies are laid out.** Lines stay lines, numbered options are a list, and
**bold** is bold; nothing else in a reply is read as formatting, and nothing in
one can become part of the page. A course the app suggested, timed, or is about
to set is drawn as a **card** under the words — its board as chips, its length
and time, and a row a leg with the TWA, the tack (P or S), the sail, the
distance and the minutes — rather than written out as a line of figures.

**Cards are numbered, and can be chosen.** When a reply offers more than one
course, the cards sit side by side under it, to be swiped through, with the edge
of the next one showing. They carry the numbers the words give the options — 1, 2, 3 — and
each has a **Use this** button. The button types what a race officer would
(*"Use course 61"*, *"Set the made-up course Fp 8p Fp 8p Op"*) and sends it, so
choosing goes through the same read-back and **Yes** as typing it. *"The second
one"* also works: the next sentence is told which course was which number. A
card on a read-back is the course already being proposed, so it has no button.

## How a command works

1. You type a sentence.
2. The app says back what it would do, in full.
3. Nothing happens until you press **Yes** or type one.

The read-back is the whole safety model. It is the only check that catches the
mistake where the app understood something perfectly and it was not what you
meant — a race at 11 tomorrow rather than today, the wrong fleet, the right
command aimed at yesterday's race.

Questions are answered straight away, because there is nothing to undo and
asking somebody to confirm a question they just asked is noise. If the answer
needs something the app has to look up first — a distance, a past race, a
course's length — it looks it up and answers from it; see
[Asking the app rather than being told everything](#asking-the-app-rather-than-being-told-everything).

**Several changes in one sentence are one read-back.** *"Use course 4 and add
Mojito"* is read back as both, numbered, and done in that order on one **Yes**:

```text
use course 4 and add Mojito
  → In this order: (1) Set Evening Race: course 4. (2) Add MOJITO to Evening Race.
```

Every step is checked against the race before anything is read back, so a
second step that would fail — a boat the club does not have — is a question
before the first has been done. If a step fails when it is carried out, the rest
are not done and the reply says which were.

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
there. A finish can also be given by hand — *Mojito's finished* — exactly as the
race sheet's **Finish** button gives it; see [Finishing a boat](#finishing-a-boat).

GPS gives the approximate time and order; the finish video remains the arbiter,
and every recorded finish stays editable afterwards. Set a frequent tracker
reporting interval on race days or the order between close boats will be too
coarse — see [`TRACKING.md`](TRACKING.md).

## What it can do

| Say | What happens |
| --- | --- |
| *create a race at 11*, *new pursuit at 7, 90 minutes* | A race sheet, with its first gun. It asks which series it is in and what it is called — see [A new race](#a-new-race) — and a course is suggested for the wind and set if you agree. |
| *create a race at 11 on course 4*, *a race at 7 with Mojito and Sgrech Bach* | The same, with the course you named — read back with its length, and no suggestion beside it — and the boats entered as it is created. |
| *another ISORA race*, *same course as last time* | It looks up the last race of that kind and carries over its series and its course. |
| *use course 4*, *put the start back ten minutes*, *bring it forward 5* | The race's course and first warning signal. |
| *time the race on the J70 polar*, *use Mojito's polar* | The polar the race's predicted times are worked out on — the race sheet's own setting. Every time the Virtual Race Officer gives for that race then uses it, and says so; results are never affected. |
| *add Mojito and Sgrech Bach*, *same boats as last time*, *add all the boats* | Entries: boats by name, the same boats as another race, or — only when you say so — every active boat in the database. |
| *Mojito's finished*, *finish Jackdaw*, *1234's over the line* | The race sheet's **Finish** button: the finish recorded at the moment you press Yes, and the horn sounded — see [Finishing a boat](#finishing-a-boat). |
| *make me a windward-leeward twice round, O to 4* | A made-up course, read back mark by mark. Say *change the second mark to 2, starboard* and it re-reads the whole course back. |
| *put it in the summer series* | The series an existing race is scored in. |
| *what is Mojito rated?*, *what's GBR4822R?* | A boat's IRC and YTC ratings — from the club's boat database, and from the RORC IRC listing and the YTC sheet when it is not in there or has no rating. |
| *add Halcyon to the boat database* | A boat the club does not have, from those listings. **An existing record is never overwritten.** |
| *how many courses are there*, *where is mark 4*, *what sail at 12 knots* | A look-up in the club's own data — marks and their positions, the fixed courses, one course's legs and distances, the boat database, the series, past races with their series and course, where the fleet has got to, the polar and its sail chart. |
| *how far is it from O to the Causeway?*, *what bearing is O to 4?*, *O to 2 and back* | The distance and bearing between any two marks, measured by the app. |
| *which is the longest course?*, *which is longer, 3 or 4?*, *what course was the last ISORA race?* | Worked out from what it looked up, and said as an answer — not a list to read through. |
| *use course 4 and add Mojito* | Both, read back together and done on one Yes. |
| *postpone*, *hold the start, the wind's gone*, *further signals ashore* | AP up, two horn blasts, and the start sequence held. **Say when the wind is back rather than what time to start** — see below. |
| *lower AP*, *we're ready, start it again* | AP comes down at the first whole minute that can still be announced — the app says "AP coming down in one minute" beforehand — with half a minute to read it and press Yes. If agreeing takes longer, it takes the next such minute and says which. Say *lower AP at twenty past* to choose the minute yourself; a minute too soon to announce is refused, with the earliest there is. |
| *shorten at mark 4*, *finish them at the windward mark* | A shortened course — two horn blasts and the spoken announcement. |
| *status*, *how are they getting on* | The race, its gun, its course, how long it should take, and the entry counts. |
| *results of the night race*, *who won on Wednesday*, *what was Mojito's corrected time in race 90?*, *by how much did she win?* | The results of a race that has been sailed, class by class: finish, elapsed and corrected times and the rating each boat was scored on, from the app's own result tables. |
| *who's leading the autumn series?*, *how many points has Crackajack got?* | The series standings, scored by the app's own series rules: rank, points and every race's score with the discards marked. "The autumn series" is the one raced most recently, and the answer says which. |
| *when was the start signal in race 90?*, *when did AP go up?* | The race's own log: every horn, flag, postponement, shortening and finish, with its time. The spoken countdown is left out unless asked for. |
| *how many races has Mojito sailed this series?*, *what was her best result?* | A boat's season: every race it was entered in, with its series and where it finished on IRC and YTC. |
| *who is entered in race 88?*, *what is Finally racing on?* | The entries, with the ratings the race is scored on — taken when each boat was entered, so they can differ from the boat database. |
| *any GPS finishes waiting to be confirmed?* | The finishes the trackers detected, which are waiting for a Yes, and whether the race records them itself. |
| *is Mojito's tracker reporting?* | Each tracker: its boat, when it last reported, its battery. |
| *where's Mojito?*, *how fast are they going?*, *who's nearest mark 4?* | Where the trackers say each boat is — in the terms a race officer uses, *0.3 nm SW of mark 4*, with the position beside it — its speed and heading over the ground, how old the fix is, marks rounded, the next mark's distance and bearing, and the distance still to sail. |
| *where was Jackdaw at 19:20?*, *how fast did Mojito sail between 19:05 and 19:30?* | The fleet as it stood at that moment — the race replay's own working — or one boat's track over a period: a dozen points through it, the distance sailed, and its average and top speed. |
| *what was the wind like in race 90?*, *what has the wind done this last hour?* | The wind the club's instrument recorded — average, range, strongest gust and how it moved. |
| *which course for 90 minutes?* | The club's fixed courses the Recommend page would choose for that length in the wind blowing now, or in a wind you name. |
| *suggest a windward leeward for an hour*, *make me a course with some reaching* | Made-up courses from any of the club's marks — see below. |
| *how long would 4p 7p Op take?*, *and on a J109?* | Any course timed leg by leg on a polar. |
| *how fast is a J109 on a beam reach in 8 knots?* | A boat's polar, found from the boat's design or by its own name. |
| *will the wind build during the race?*, *what's the forecast for 11 tomorrow?* | The weather forecast from the internet, hour by hour — see [The forecast](#the-forecast). |

## A new race

A new race is asked two things the sentence did not say:

```text
create a race at 18:55
  → Which series is the new race in? The last race, 'Evening Race' on Wed 23 Sep,
    was in Wednesday Evening Points. A race in a series is entered with the boats
    already racing in it.            [Wednesday Evening Points] [no series]
Wednesday Evening Points
  → What is the new race called? The last race in Wednesday Evening Points was
    'Evening Race'.                  [Evening Race]
Evening Race 12
  → Create 'Evening Race 12' as a standard race, first warning signal 18:50, first
    gun 18:55, in series Wednesday Evening Points, entering its 12 boat(s):
    CRACKAJACK, FINALLY, MOJITO, … and 4 more, with GPS finishes armed …
```

**The series is the fleet as well as the scoring.** A race in a series is
entered with every boat already racing in that series — the same rule the race
sheet applies when a race is created in one — and the read-back names them,
because agreeing to the series is agreeing to them. There is no *Add all boats*
button any more: most of the boat database is not racing on any given day, and
a race in the right series already has the right boats. Tap the suggestion, type
another series, or say *no series*; a race in no series with nobody named is
read back as starting empty.

**The name is asked for, not assumed.** An unnamed race used to be called
*Club Race*, which is not what anybody looks for in the results. The suggestion
is what the last race in that series was called; any name typed is taken whole,
so *Evening Race 12* is not read as the *Evening Race* it contains.

A sentence that says either — *create a race called Sunday Points at 11 in the
summer series* — is not asked about it.

## Finishing a boat

*Mojito's finished* is the race sheet's **Finish** button, from the water. It is
the same call (`core.raceadmin.finish_entry_now`), so the same things happen: the
finish is recorded, the **horn sounds**, the race log has it, and the finish
camera keeps a clip.

```text
Mojito's finished
  → Finish MOJITO (GBR 1234) in Evening Race now, and sound the horn. The finish
    time is the moment you say yes.
  → Yes
  → MOJITO (GBR 1234) finished at 19:42:07. Horn sounded.
```

- **The time is the Yes**, not the sentence: that is when it is taken, as the
  button takes it when it is pressed.
- A boat is named by its name or its sail number, and matched against **the
  race's own entries**. A name that is not entered is a question listing the
  boats that are, and a name that could be two boats asks which — never the
  nearest boat, because a finish given to the wrong one is two results wrong.
- **Not before the start.** A race that has not started, or has AP up, refuses:
  the horn would sound for a finish that cannot have happened.
- A boat that already has a finish is read back as *this replaces it*; one marked
  DNF or similar as *this records a finish instead*.
- Two boats together — *Mojito and Finally are over the line* — are one
  read-back and one Yes, finished in that order, a horn each.
- If the horn fails, the finish is still recorded and the reply says the horn did
  **not** sound. On a machine with no horn connected it says the horn is only
  simulated.

"Over the line" in a race that has been going for a while is a finish; at the
start it is a boat over early, which it still cannot signal (see below).

## The forecast

*Will it build during the race?* is answered from a weather forecast on the
internet — the only thing in the app that says what the wind will do, as against
what the club's instrument says it is doing now.

- **With nothing set up** it reads Open-Meteo's forecast at the start line, from
  the UK Met Office model: the wind hour by hour — direction, speed, gusts — in
  knots.
- Asked about **the race**, it reads the race's hours: the first gun and the two
  and a half hours after it. Asked about another time — *11 tomorrow* — it reads
  those hours; otherwise the next six.
- It says it is a forecast, and keeps it apart from the wind now.

**Settings → Virtual Race Officer → Weather forecast URL** chooses another
source. An `api.open-meteo.com` URL of your own — another model, another
position — is read the same way. **Any other page is read for its words only.**
A windy.app spot page such as
`https://windy.app/forecast2/spot/313701/Pwllheli+Sailing+Club` gives a
day-by-day summary written for kitesurfers, in metres per second and with no
wind direction, because its hour-by-hour forecast is drawn in the browser by
script and never reaches a program that fetches the page; the `#alerts=sail`
part of such an address is read by the browser too, and never sent. The words
are passed on as the page's own, never as the app's.

A forecast is fetched at most once every half hour, whoever asks: the hut's link
is 4G shared with everything else.

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

- **Sound a horn on its own.** It sounds for a finish, a shortened course and a
  postponement, as the race sheet does — never on command. There is no *arm*
  step and no *fire now*: with
  start automation switched on in Settings, the app fires the warning,
  preparatory and start signals itself, off the race's stored warning signal.
  **So setting or moving a start time from the water moves the horn with it**,
  and the read-back says so. With start automation off, nothing sounds by
  itself whatever time is set.
- **Signal a recall.** The club starts races with nobody watching the line and
  reviews the video afterwards, so there is nothing for it to signal.
- **Abandon a race.**

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
relevant goes in the facts; the rest is a **look-up**.

A look-up goes back to the interpreter, not straight to the phone. It asks the
app, the app answers from its own data, and the interpreter then answers the
question that was asked:

```text
how far is it from O to the Causeway?
  → It's 11.57 nm from O to the Causeway, on a bearing of 184° true.
which is longer, course 3 or course 4?
  → Course 3 is longer — 10.3 nm versus course 4's 4.2 nm.
what course did we sail in the last ISORA race?
  → The last ISORA race was #61 'ISORA CW5 Night Race' (gun 2026-08-08 20:05),
    sailed on course 1.
```

Until September 2026 a look-up was shown as it came — a list of every course, or the
Causeway's latitude — and the interpreter was told not to work anything out
from it. Asked how far it was from O to the Causeway it said, four times running,
that it could not calculate that, while holding both positions.

Three things keep this honest:

- **The figures are the app's.** A distance or bearing between marks is
  measured by the app (`mark_distance`, the same arithmetic the leg analysis
  uses), results come from the scoring code, and a look-up goes through exactly
  the path the page would take for it. The interpreter adds legs up and
  compares lengths; it does not do trigonometry on positions or score a race.
- **Anything that changes something still ends the turn as a read-back.** The
  interpreter never sees the result of an action, because there is none until
  somebody says yes.
- **It is bounded.** At most three rounds of looking things up, and 45 seconds
  for the whole sentence. If either runs out, the page is shown what was looked
  up, as it is. The status report is shown as the app writes it, without a
  second round trip: it is the sentence people type most.

## Made-up courses, and polars

The club's fixed courses are drawn for sixteen wind bands, and a race officer on
the water often wants something the sheet does not have. Every mark is in the
app with its position, and the app times any leg on the polar, so it can search
for a course itself:

```text
suggest a windward leeward for about an hour
  → 1. Fp 8p Fp 8p Op – 5.99 nm, about 54 min, 2 upwind / 2 downwind.
         TWA: O-F 44° P, F-8 157° S, 8-F 23° P, 8-O 7° P
    2. Fp 4p Fp 4p Op – 7.07 nm, about 67 min …
use the first one
  → Make up a course for Evening Race and set it: Fp 8p Fp 8p Op (5 marks).
    It replaces the numbered course.
```

The search (`core.courses.suggest_made_up_courses`) uses every mark that has a
position of its own within six miles of the line — not only the ones the fixed
courses round — and leaves out waypoints and compound marks. Courses start and
finish at O and round everything to port, as the club's do. Each leg between
every pair of marks is timed once, with the same function the race sheet's leg
analysis uses, and each candidate is the sum of its legs, so thousands are
tried in a few hundredths of a second. They are ranked on how near they come to
the time asked for, then on what a race officer would object to: a first leg
that is not a beat, a leg too short to race, sail changes, and more than three
laps. A windward-leeward can be between any two marks, not only back to the line:
in a northerly nothing within reach lies upwind of O, because the land does.

Every suggested or timed course comes with the wind angle of each leg and the tack (P or S) — the thing that tells a race officer whether it is the course they want. Replies are shown as lines, numbered options and **bold**, and nothing else is read as formatting.

Nothing is set until it is agreed. The suggestion is words; *use the first one*
is a made-up course read back mark by mark, like any other.

Going back the other way works too: *"use course 64"* on a race with a made-up
course reads back *"It replaces a made-up course, 5p Op"*, and on **Yes** the
made-up course is cleared and the number set, as the race sheet's own course
change does. The reply is read back from the race afterwards, so it can only say
what the race is now on; if the course could not be changed it says so.

**The wind is only "now" when it is.** Suggestions and timings use the club's
wind instrument. A reading more than fifteen minutes old is said to be old —
*"the last wind reading, 3 hours old — nothing newer has come in"* — and a wind
typed into Settings is said to be typed in, because a course time worked out on
either is not a time for the wind on the water.

**Polars.** The club has 53. A boat's is found from its **design** in the boat
database (Mojito's is the J122), or asked for by name (*"on a J109"*); a boat with
no polar of its own is timed on the club's default, and the answer says so,
because a time on another design's polar is a different answer. The interpreter
can read a polar to reason with — which boat gains on a reach, what angle a boat
beats at — but a time round a course comes from the app, which does those sums on
the same polar leg by leg, so what it says matches the race sheet.

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

Nor is anything guessed from a sentence when the interpreter fails. The same
grammar stayed behind the model for a while, to catch a command answered in
words and to answer whenever the model did not. It never caught a command; what
it did, every time it acted, was see "course N" and propose setting it —
*"which is longer, course 3 or course 4?"*, *"time course 4 on the J70 polar"*,
*"that's a different course, not course 6"*. Measured over 67 sentences with and
without it, it changed nothing else, and it was taken out. A timeout now says the
interpreter did not answer in time, and to say it again.

What that costs: the hut's outbound internet can fail while the page itself is
perfectly reachable, and the racing then has to be run from the race sheet. That
is the trade, made deliberately — half a feature that works for six sentences is
worse on the water than a clear "not available".

If the model was configured and its last request failed, the page and Settings
say so and give the provider's own reason, rather than letting a billing problem
look like a stupid app.

The tools and the system prompt are the same for every sentence, so each request
marks where that part ends and the provider serves it from its cache. Through
Cloudflare a cached call answered in 1.9 s where the same call uncached took
3.6 s. Cloudflare's Messages route takes the system prompt only as a plain
string, so the marker sits on a short fixed opening message instead.

### Measuring it

`scripts/eval_vro.py` sends the sentences people actually type — the ones in
the hut's own log among them — to the real endpoint with the real model,
against a copy of the newest hut backup in `HutData/`, and checks each reply
against the app's own answer: the distance the app measures, the course the
last ISORA race used, the gun a read-back names. Nothing is carried out; only
the interpret endpoint is called, on a throwaway copy with every credential
blanked and everything outbound switched off. It uses the interpreter configured
on the dev box and costs well under a pound a run.

```bash
python scripts/eval_vro.py
```

`--repeat 3` runs it three times (models are not deterministic), `--only
distance` runs the cases whose id or tag matches, and `--model` tries another
model through the same gateway. On 24 September 2026, with Sonnet 5 through
Cloudflare, the v1.010 page scored 25 of 42 and this one 83 of 84 over two runs, with a median of 4.4 seconds a sentence. The questions added with standings, times, the log, made-up courses and polars are tagged `more`; with them the page scored 116 of 118 over two runs, every one of the new questions among the passes, and then 59 of 59.

## The conversation

- It remembers the last half hour with the same person — the last eight
  exchanges — so *"make it a pursuit"* or *"add the fleet to that one"* has
  something to refer to. Each exchange is remembered with **what it looked up**
  as well as what it said: asked for courses for 20 minutes, then which polar,
  then to use the J70's, it used to time them for a hundred minutes, because the
  twenty had only ever been an argument to a look-up.
- It knows when a postponement flag is up. Under AP a race keeps its stored gun
  time and its boats stay marked as racing, so both look like a race under way;
  the facts it is given say, first, that AP is up and the race has not started.
- It is not given a race length nobody asked for. It used to be shown the
  courses recommended for the current course's own time, and took that time as
  the race officer's target.
- A question the app asks can be answered in a word: asked *"standard race or a
  pursuit?"*, answering *standard* re-issues the whole command with the time,
  the name and the length it already had.
- While a proposal is waiting, it is what the conversation is about. Asking for
  a longer course changes **that** proposal rather than something else. **The Yes
  and No buttons stay with it**, with a line above them saying what they are for,
  whatever else is said in between: a question asked while AP-down was waiting
  used to take the Yes button away, and the interpreter then spent a minute and
  a half telling the race officer to press it.
- A Yes that cannot be carried out closes the proposal and says why, in the
  thread, so the interpreter knows it happened rather than offering it again.
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
