# Catalyst Evaluation Framework — Pre-Market Prep

> Guidelines for grading watchlist tickers during pre-market prep.
> This is a **reasoning aid for Claude Code**, not yet a coded grading system.
> When invoked, Claude should walk through these criteria for each ticker
> on the watchlist and produce a written evaluation per ticker.

---

## 1. Purpose

The scanner and the watchlist already surface *candidates*. This document
adds the missing step: **why is this ticker tradeable today, and at what
size?** The framework is adapted from SMB Capital's "Catalyst Value
Equation" (Jeff Holden, *Our Prop Trading Formula*) and is intended to
push every pre-market call past gut feel into a defensible grade.

When the trader asks Claude to run a pre-market evaluation, Claude:

1. Reads this file for the rules.
2. Pulls the current watchlist (and any attached news / scanner context).
3. Produces a per-ticker reasoning trail in plain text — no DB writes,
   no UI changes, no automated scoring yet.

The output is meant to be read on screen and used as input to the
trader's own grade. The trader still assigns the final letter.

---

## 2. Core principle — every trade starts at D

> Default assumption: **don't trade it.**

A ticker only earns its way up the grade ladder by passing two specific
tests. If either test returns "No", the grade stays D and the ticker is
not traded today regardless of how interesting the chart looks.

This bias matters because the entire framework collapses if Claude (or
the trader) starts from "this looks good" and looks for reasons to
confirm. Start from D. Make the catalyst prove itself.

---

## 3. The Catalyst Value Equation (CVE)

```
CV grade = f( Magnitude , Speed )
```

Two independent variables, each scored on a 4-level ordinal scale:

| Score      | Meaning                                                        |
| ---------- | -------------------------------------------------------------- |
| `Absolute` | Unambiguous, market-moving, hard to argue with                 |
| `Yes`      | Clearly present, but with some debatable element               |
| `Maybe`    | Possibly there — requires interpretation or assumption         |
| `No`       | Not present / cannot be evidenced                              |

**Magnitude** — *how big is the implied move?*
Is the catalyst large enough to drive a multi-percent intraday move on
its own, independent of tape conditions? A guided-up earnings number on
a $10B company is bigger than a 13D filing on a small float, etc.
Magnitude is about the **size of the implied repricing**, not the size
of the company.

**Speed** — *how quickly does the market need to reprice?*
Is the catalyst time-compressed — must be priced in *today*, at open,
or in the first hour — or is it a slow-burn thesis that could grind
for a week? Earnings, halts, surprise FDA, S&P inclusion ahead of
rebalance, analyst initiations day of — these are fast. Vague macro
narratives are slow.

Both variables must be evaluated **independently**. A ticker can have
Absolute magnitude with No speed (slow re-rating) and still grade D.

---

## 4. CV grade → daily risk allocation

The pair (Magnitude, Speed) maps to a letter grade, and the grade caps
the size of the day's risk on that name as a percentage of the day's
total risk budget.

| Magnitude × Speed       | Grade | Daily risk allocation |
| ----------------------- | ----- | --------------------- |
| `Absolute` × `Absolute` | **A+** | up to 80%            |
| `Yes` × `Yes`           | **A**  | up to 30%            |
| `Yes` × `Maybe`         | **B**  | up to 15%            |
| `Maybe` × `Maybe`       | **C**  | up to 5%             |
| `No` on either variable | **D**  | 0% — don't trade     |

Notes:

- Order doesn't matter — `Maybe × Yes` = `Yes × Maybe` = B.
- These are **caps**, not targets. A grade lets you risk *up to* the
  cap; setup quality, location, and current portfolio heat still
  decide whether you use the full allowance.
- The 80% A+ slot is a real allocation — A+ days are rare and are the
  point of the whole system. Don't dilute the term by giving A+ to
  Yes×Yes setups because they look pretty.

---

## 5. Catalyst types — what counts

The framework recognises a small number of catalyst archetypes. Each
ticker on the watchlist should be tagged with at least one. Untagged
tickers default to D (no identifiable catalyst → don't trade).

**Confirmed material events.** Hard, dated, public information that the
market is required to react to:

- Earnings release, guide change
- M&A announcement (confirmed, not rumoured)
- FDA decision / clinical readout
- Index inclusion (e.g. S&P 500) — known rebalance date
- Regulatory ruling, large contract award
- IPO post-quiet-period analyst initiations

**Coverage / positioning shifts.** Re-rating of the existing thesis by
parties who actually move flow:

- Multiple analyst initiations or upgrades on the same day
- Notable activist filing (13D)
- Large block / unusual options activity tied to a specific date

**Narrative / sector flow.** Soft catalysts that need confirmation from
the tape. These tend to score `Maybe` or `No` on Magnitude unless price
is already moving:

- Sector sympathy from a peer's earnings or news
- Macro / commodity-driven theme of the day
- Social-media or retail-driven attention (treat with skepticism)

A pure technical setup (breakout from base, MA cross, gap-and-go on a
clean chart) is **not** a catalyst on its own. It's a setup. Without a
catalyst behind it the grade is D.

---

## 6. Reference examples (from the source material)

These are calibration anchors. When Claude is uncertain about a grade,
it should explicitly compare the current ticker to one of these.

- **CBRS — post-IPO coverage burst.** Hot recent IPO, post-quiet-period
  initiations from multiple banks hit the tape on the same morning.
  Magnitude = Absolute (coordinated coverage on a high-attention name,
  large implied repricing). Speed = Absolute (all initiations released
  pre-market, must be priced at open). Grade: **A+**.

- **MRVL — S&P 500 inclusion.** Confirmed index addition with a known
  rebalance date forcing mechanical buying. Magnitude is high but the
  catalyst is dated — speed depends on how far the rebalance is. Grade
  typically lands **A** when close to the date.

- **INTC — big move, imperfect catalyst.** Price moved hard but the
  underlying catalyst was ambiguous. This is the canonical "trap" —
  big tape move without clean Magnitude scores **Maybe** at best, and
  size has to be cut accordingly. The chart will lie to you here.

- **GLW — confirmed deal, lower grade.** Real catalyst, but either the
  magnitude or the time-pressure didn't justify A-tier sizing. Confirmed
  + slow-burn lands around **B**.

- **AAPL — disappointment is not a catalyst.** "It missed and faded"
  is a reaction, not a catalyst. If there's no concrete piece of news
  driving forced repricing, the answer is D regardless of how broken
  the chart looks intraday.

---

## 7. Pre-market evaluation checklist

For each ticker Claude should run through this sequence and produce a
short paragraph of reasoning, ending in a proposed grade and a one-line
sizing note.

```
1. Identify the catalyst.
   - What specifically happened? Source it. (PR, 8-K, news headline, etc.)
   - If you cannot name a concrete catalyst in one sentence, grade = D, stop.

2. Tag the catalyst type.
   - Confirmed material event / coverage shift / narrative.

3. Score Magnitude (Absolute / Yes / Maybe / No).
   - Implied size of the move this catalyst alone should drive.
   - Compare to the reference examples in §6.

4. Score Speed (Absolute / Yes / Maybe / No).
   - Does this need to be priced today, at open, in the first hour?
   - Or could it drift for days?

5. Look up the grade in the §4 table.

6. Add caveats.
   - Is the move already in the price? (gap, after-hours run)
   - Is the float / liquidity sufficient for the implied size?
   - Is there a confounding catalyst on a peer that complicates flow?

7. Output: <grade> — <one-line reasoning> — <max risk %>.
```

---

## 8. What Claude should NOT do (yet)

- **No writes.** Don't push grades into the database, the watchlist
  strategy field, or any new table. This document is reasoning-only
  until the schema for storing CV grades is designed.
- **No automated scoring loops.** Don't build a function that
  programmatically maps news text → magnitude score. The whole point
  of this phase is for the trader to see Claude's reasoning and adjust
  the rubric before any of it becomes code.
- **No grade inflation.** If a ticker doesn't have a clean catalyst,
  say D. The bias must be toward not trading.
- **No grade without evidence.** Every grade above D must cite the
  specific piece of news/filing/event that justifies it. "Looks
  strong" is not a citation.

---

## 9. How the trader invokes this

Typical pre-market session:

```
"Run catalyst evaluation on today's watchlist."
```

Claude reads this file, fetches the watchlist (and any news already
attached), and produces a per-ticker block:

```
AAPL
  Catalyst:   <one sentence>
  Type:       <confirmed event | coverage | narrative>
  Magnitude:  <Absolute|Yes|Maybe|No> — <why>
  Speed:      <Absolute|Yes|Maybe|No> — <why>
  Grade:      <letter>
  Sizing:     <≤X% of daily risk>
  Notes:      <caveats, peer flow, float concerns>
```

The trader reads, edits, and decides. Nothing else happens until the
grading rubric has been used live for enough sessions that the trader
trusts it.

---

## 10. Roadmap (not yet — for context only)

Future phases, in rough order, once the rubric is stable:

1. Persist grades alongside watchlist entries (new column or side table).
2. Surface grades in the scanner / watchlist UI as a column.
3. Auto-tag catalyst type from attached news headlines (low-confidence
   suggestion only; trader confirms).
4. Track grade vs. realised P&L per ticker to validate the rubric.
5. Enforce daily risk caps in the order/position manager based on grade.

None of this gets built until §1–§9 has been used by hand and the
definitions in §3–§6 are settled.

---

## Source

Adapted from SMB Capital, *Our Prop Trading Formula (Secret to Grading
Your Trades)* — Jeff Holden's Catalyst Value Equation. The grade-to-risk
table in §4 is taken directly from the video; the rest is restated for
this project.
