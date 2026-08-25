# Amazon Marketing Stream — setup and what it is for

> Written 2026-08-21, after checking live that the account already has access.

## What this buys

Today the engine ASKS Amazon to build a report, then waits. `phase0_pull.py`
polls for up to 25 minutes (`MAX_WAIT = 1500`) and gives up on anything slower.
Reports that outrun that window are picked up the next night. A missed night
needs `catchup.py` and several rounds.

Marketing Stream turns that around. Amazon PUSHES rows to a queue we own, about
an hour behind the hour they describe. Nothing to ask for. Nothing to poll.

## What it does NOT buy

**Stream has no history.** A subscription starts the clock. It will never send a
row about yesterday, let alone last month.

So Stream does not replace the report pipeline. It sits beside it:

| Job | Owner |
|---|---|
| Fresh hours, today and yesterday | Stream |
| History, backfill, the Monday 30-day true-up | `phase0_pull.py`, `backfill_daily.py` |

It also does not fix the attribution lag. The freshest day or two is
under-attributed in Amazon's own numbers, whichever way we fetch them.

## Access — already granted

Checked live on 2026-08-21 with a read-only probe:

```
GET /streams/subscriptions
US → 200 {"subscriptions":[]}    UK → 200    DE → 200    USKDP → 200
```

`200` means the existing Ads API credentials already carry Stream permission.
Amazon's rule is that an account already integrated with the Ads API needs no
separate Stream application. There is no second approval wait.

Being allowed to LIST is not proof that a Merch/POD profile may CREATE. That is
settled by the first `stream-subscribe`, not by this page.

## What is still needed: one AWS account

Stream will not hand us data. It writes into a queue we own. That means AWS.

### Step 1 — an AWS account

Sign up at aws.amazon.com. A card is required. The queues here cost cents per
month; the free tier covers a million SQS requests.

> **The account has its own clock, and it fails quietly.**
> A new AWS account starts on the **free plan, which auto-closes six months in**
> unless it is upgraded to a paid one. The bill for two SQS queues is about
> nothing either way, so this is paperwork — and it is the dangerous kind of
> deadline because of *how* it ends. If the account lapses the queues go, Stream
> stops arriving, and **Amazon carries on reporting the subscription `ACTIVE`**.
> Every screen keeps working. The day simply reads quieter, which is
> indistinguishable from a slow sales week.
>
> The date lives in `engine/stream_config.py` as `AWS_PLAN_EXPIRY` (currently
> **2027-02-21**, for an account opened 2026-08-21). The `aws_plan_expiry` alert
> starts 60 days out and keeps speaking after the date rather than going quiet at
> the worst possible moment. Upgrade the account, then update the constant. Set it
> to `None` to switch the warning off deliberately.

### Step 2 — one queue per dataset, in the right region

Two datasets matter for Sponsored Products:

- `sp-traffic` — impressions, clicks, spend
- `sp-conversion` — orders, sales

**The region is not a free choice.** A NA subscription can only deliver to
`us-east-1`. An EU subscription can only deliver to `eu-west-1`.

| Market | Realm | Queue region |
|---|---|---|
| US, KDP US | NA | `us-east-1` |
| UK, DE, FR, ES, IT | EU | `eu-west-1` |

One queue serves a whole realm. All five EU markets can point at the same EU
queue; their messages arrive mixed together and the drain sorts them out.

Start with NA only. In the AWS console, SQS → Create queue → Standard:

- `merchads-sp-traffic-na` in `us-east-1`
- `merchads-sp-conversion-na` in `us-east-1`

### Step 3 — the access policy on each queue

**Each dataset publishes from a DIFFERENT Amazon AWS account.** Reusing one
policy for both queues drops every message of the second dataset, with no error
anywhere. The subscription still reports ACTIVE.

So do not hand-write the policy. Ask for it:

```bash
ADS_MARKET=US python3 engine/appctl.py stream-setup --queue-url <the queue URL>
```

It prints the exact JSON for that queue, with the right publisher account, plus
the `ReviewerRole` grant Amazon needs to validate the queue before it will
activate the subscription. Paste it into the queue's Access policy tab.

### Step 4 — a user that may read the queues

IAM → Users → create a user with no console access. Attach an inline policy
allowing `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on
the two queue ARNs, and `sns:ConfirmSubscription` on `*`.

Create an access key. Put both halves in `.env`.

### Step 5 — four lines in `.env`

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
STREAM_QUEUE_NA_SP_TRAFFIC=https://sqs.us-east-1.amazonaws.com/<account>/merchads-sp-traffic-na
STREAM_QUEUE_NA_SP_CONVERSION=https://sqs.us-east-1.amazonaws.com/<account>/merchads-sp-conversion-na
```

The queue URL is the only thing to paste. The ARN, the region and the account
are all derived from it, so a URL and an ARN can never disagree.

### Step 6 — subscribe (a live account write, operator-run)

```bash
ADS_MARKET=US python3 engine/appctl.py stream-subscribe --dataset sp-traffic
ADS_MARKET=US python3 engine/appctl.py stream-subscribe --dataset sp-conversion
```

### Step 7 — answer the handshake

This is the step everyone misses.

Creating a subscription parks a `SubscriptionConfirmation` message in the queue.
Until something answers it with its Token, the topic is not really subscribed
and **no data ever arrives**. The subscription sits at `PENDING` and every screen
looks healthy.

Draining answers it:

```bash
python3 engine/stream_drain.py --seconds 30
```

It prints `SNS handshake confirmed` when it does. Amazon's own reference
implementation spends a Lambda, a second queue and a CDK stack on this; here it
is part of the drain we already run.

### Step 8 — wait an hour, then look

```bash
python3 engine/stream_drain.py
ADS_MARKET=US python3 engine/appctl.py stream-fields
```

### How long the drain needs (learned the hard way, 2026-08-21)

**Stream sends roughly one message per impression.** Not one per hour, not one
per campaign — the payload carries a single ad, keyword, placement and hour,
and `impressions` is usually 1 or 2. A US day of ~25,000 impressions is
therefore on the order of ten thousand messages, and SQS hands them over about
ten at a time at roughly seven messages a second.

The first hourly job used a 60 second budget. That reads about 480 messages,
which is **less than one hour delivers**, so the queue grew all day and the
Dashboard's live totals were an undercount that got worse by the hour. Nothing
looked wrong: every run logged a healthy count of messages banked.

Two things now stop that happening quietly:

- The budget is **300 seconds per queue** (`--seconds`, and the same in
  `run_stream_drain.sh`). The loop still exits the moment the queue is empty, so
  a quiet hour still costs about 40 seconds.
- The drain **says so out loud** when the budget runs out with the queue still
  full, `stream_store.health()` reports it as `drain_backlog`, and System Health
  draws an amber line. A recent drain with a big message count is not proof the
  queue is keeping up.

### Why the drain runs hourly, and not every three hours

Asked and measured on 2026-08-21, on the live US queue.

**The messages are safe either way.** The queue keeps a message for 14 days
(`MessageRetentionPeriod` 1209600), so a slower schedule loses nothing to
retention. That is not what settles it.

**The time budget is what settles it.** One SQS round trip takes 0.44 seconds
from this Mac, measured over five calls. A receive brings back at most ten
messages, and each batch of ten needs a delete call as well. So a backlog of N
messages costs about `N/10 × 2 × 0.44` seconds, plus the ~40 seconds of empty
long-polls that prove the queue is empty.

| Schedule | Backlog per run | Time needed | Budget | Headroom |
|---|---|---|---|---|
| hourly | ~850 | ~115 s | 300 s | 2.6× |
| 3-hourly | ~2,550 | ~264 s | 300 s | 1.1× |

The busiest hour of 2026-08-21 delivered 844 messages, and that was a quiet
August day on six markets' worth of a young subscription. At three hours the
run sits on the edge of the budget, and the failure past that edge is the one
this whole section is about: the drain stops before the queue is empty, the
backlog carries into the next run, and every run still logs a healthy count of
messages banked.

**It costs nothing to run hourly.** About 4,250 SQS calls a day, or ~128,000 a
month against a permanently free tier of 1,000,000. A message is ~516 bytes and
ten of them are ~5 KB, well under the 64 KB that SQS bills as one request.
Moving to three hours would save only the empty polls — 32 calls a day out of
4,250, under 1%.

**And it costs the panel its point.** Amazon publishes about an hour behind the
hour it describes. Hourly means "Today so far" is 1–2 hours behind; 3-hourly
means up to 4. That is a long way from *so far* on the Dashboard's only live
section.

If the account grows enough that an hourly run stops finishing, the answer is a
bigger `--seconds` budget or a shorter interval, not a longer one.

### Corrections arrive as negative numbers

A message with `impressions: -1` is Amazon backing out an impression it
reported earlier, usually invalid traffic. Summing everything is the right
handling and needs no special case.

The one time it looks wrong is the **first day**: corrections arrive for hours
whose originals were sent before the subscription existed, so an early hour can
show a negative impression count. It is real, it is small, and it stops
happening once a full day is covered.

## Where the data lands

`stream_data.sqlite`, beside the market databases. Its own file on purpose.

One queue carries several markets, so deciding which market a message belongs to
means reading a field of the payload. This engine does not guess at fields it
has never seen. So arrival is kept separate from interpretation: every message
is banked whole, and mapping into per-market daily rows is written afterwards
against real messages.

That is what `stream-fields` is for. It counts the keys the stored payloads
actually carry. The first hour of real data is the only chance to learn what
Amazon sends — Stream does not replay.

## Commands

| Command | What it does | Safe? |
|---|---|---|
| `appctl stream-status` | subscriptions, queue depth, what is banked | read only |
| `appctl stream-setup [--queue-url U]` | queue names and the policy to paste | read only |
| `appctl stream-subscribe --dataset D` | start the push | **live write** |
| `appctl stream-unsubscribe --subscription ID` | archive it | **live write** |
| `appctl stream-drain [--seconds N]` | pull the queue, confirm handshakes (default 300s per queue; exits early when empty) | reads AWS, writes local |
| `appctl stream-fields` | which fields real payloads carry | read only |
| `appctl stream-today [--day D]` | today so far: spend, placements, hours, campaigns | read only |
| `appctl stream-advertisers [--refresh]` | which advertiser id maps to which market | read only |
| `appctl stream-verify [--day D]` | **the only check that can prove Stream is not dropping data.** Measures one settled day twice — from Stream and from the banked report — and compares per campaign | read only |
| `python3 engine/stream_drain.py --status` | same as stream-status, no Amazon call | read only |

## Why there is no boto3

The app ships its own CPython at `Contents/Resources/python` carrying exactly
one third-party package: `requests`. The whole point is that a bare Mac with no
Homebrew and no pip can run the engine. botocore would add tens of megabytes.

Stream needs three AWS calls, so `engine/aws_sigv4.py` signs them by hand. It is
checked against AWS's own published `get-vanilla` signature vector in
`tests/stream_tests.py` — canonical request, string to sign and Authorization
header, all three pinned.

## Still open

- Whether Amazon accepts a subscription for a Merch/POD profile. Unknown until
  the first `stream-subscribe`.
- The mapping from banked messages into `target_daily` / `campaign_daily`. It
  waits for real payloads on purpose.
- EU queues. Do NA first, prove the numbers match the reports, then repeat.
