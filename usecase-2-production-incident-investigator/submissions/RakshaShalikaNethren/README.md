# Submission — Production Incident Investigator

## Design

The whole thing lives in `investigate()` in `solution.py`, split into two
passes, because that's basically what the brief is asking for even if it
doesn't spell it out that way.

First pass is retrieval. I chunk the corpus instead of scoring whole
files — `known_issues.csv` gets split one row at a time, and every
markdown file gets split into paragraphs plus each individual line
inside a multi-line paragraph. That second part mattered more than I
expected going in. `logs.md` has the real log lines mixed in with a
bunch of noise from other systems (checkout latency, search lag, auth
timeouts, refund webhooks), and if you score the whole file as one blob
the noise just drowns out the two or three lines that actually matter.
Scoring itself is plain TF-IDF cosine similarity, nothing fancier, over
a query vector built the same way.

Second pass is where the actual work is. I pull an "anchor" entity out
of the top-ranked chunks — a component name, an exception name, a
KI-/INC-/RB- id, whatever shows up consistently across multiple files
near the top of the ranking. Then, separately, I go back over the whole
corpus (not just the top hits) and check every section that mentions
the anchor: does it just mention it, or does it also hedge/deny that
it's actually related ("unconfirmed," "no correlated deployment," that
kind of phrasing)? Confidence comes directly from how many independent
files back the anchor cleanly vs. how many walk it back.

`root_cause`, `remediation`, `mttr_minutes`, and `impacted_systems` all
get pulled from whichever sections end up "clean" — looking for
structural markers like `**Remediation**:`, a `Typical MTTR: N minutes`
line, a deployment table row. None of this is keyed to the specific
wording of incident A or B, it's keyed to the document *shapes* the
brief itself describes.

## What I think the actual problem is

Retrieval isn't the hard part. You can get a plausible top-ranked
document in an afternoon. The hard part is that pretty much any
pipeline — LLM or hand-rolled heuristics, doesn't matter — will happily
hand you a confident-sounding answer even when the evidence doesn't
really back it up, because "sounds sure" and "is right" are two
different things and nothing forces them to line up unless you build
something that checks.

Incident B feels like it's built specifically to expose this. There
really is a present, un-contradicted-looking symptom
(`notification-service` queue depth) sitting right next to a runbook
that name-checks it. If you only look at the top search hit, incident B
looks about as solid as incident A. The only way to tell them apart is
to read the rest of the corpus and notice the runbook is quietly
hedging itself ("incomplete pending better instrumentation," an MTTR
labeled as being "from a different, unconfirmed prior occurrence"). So
the real problem isn't finding relevant text, it's telling the
difference between "this document mentions X" and "this document
actually backs X up" — which is why most of the time went into the
hedge-detection side, not the retrieval side.

## Why I built it this way, and what didn't work

First version picked the anchor by trying a few candidate entities and
seeing which one had the best corroboration score after the fact. Bad
idea — on incident B it picked `web-frontend`, a component with nothing
to do with the actual symptom, purely because nothing in the corpus
contradicted it. I only caught that by printing the candidate list and
checking it by hand against what incident B is supposed to say. Fixed
it by locking the anchor in from retrieval alone, before any
corroboration checking happens — otherwise the "analysis" step just
rewards whatever's easiest to not-disprove, which isn't the same as
what's true.

Second thing I got wrong: checking hedge phrases against whole files.
`known_issues.csv` breaks that immediately — one row (`KI-101`) is the
real, clean evidence, but a completely different row in the same file
(`KI-121`, about a refund webhook) happens to say "separate from,"
which would hedge the entire file, including the row that shouldn't be
touched. Moved hedge checking down to section/row level and that went
away.

Entity extraction is just regex — hyphenated names, `Exception`/`Error`/
`Timeout` suffixes, `ALL_CAPS` constants, `KI-`/`INC-`/`RB-` ids. Not a
real NER model, and it shows: I had to explicitly exclude `INFO`/`WARN`/
`ERROR` from the ALL_CAPS pattern after they started showing up as
"candidate entities," which is the kind of patch a proper entity
extractor wouldn't need.

`impacted_systems` is also restricted to components that show up on the
*same line* as the anchor, not the whole matching section — otherwise a
single architecture-overview paragraph that lists every component in
the system ends up marking all of them "impacted" regardless of what
the incident actually is.

I didn't wire in an LLM anywhere. Everything's deterministic, which made
debugging against these two incidents a lot less painful than it would
have been chasing down whether a weird answer was a real bug or just
sampling noise.

One more thing worth being honest about: I got nervous that I'd
basically tuned this to incident A and B's exact wording, so I wrote a
third, fake incident using the same document types but different
phrasing for the "this is thin evidence" parts — things like "has not
been established" and "never pinned down" instead of "unconfirmed."
It broke immediately, reporting 85% confidence on evidence I'd
deliberately written to be coincidental at best. That pushed me to
rewrite hedge detection from a fixed phrase list into actual negation
patterns ("not"/"never"/"no" near "confirm"/"establish"/"correlate,"
plus a short list of standalone words like "isolated" and
"coincidental"). That fix then immediately over-corrected and broke
incident A instead — a throwaway "unrelated" trigger matched harmless
decoy text in `logs.md` and `deployment_history.md` — so I pulled that
one pattern back out. There was a smaller third gap after that too: the
fake incident's hedges were attached to a component the anchor calls
into, not the anchor itself, so I added a small check for tightly
co-occurring "neighbor" entities so a hedge on a closely linked
component still counts against confidence. None of this changed what
incident A or B actually output, it just made me trust the number more.
I'd still call the hedge-phrase list the weakest part of the whole
thing — it's pattern-matching on how doubt tends to get phrased, not
real language understanding, so a sufficiently different way of hedging
could still slip past it on a genuinely new incident.

## Contact

- Raksha — 88785456
- Shalika — 91057921
- Nethren — 94894654
- Email: emailtosubbu@gmail.com
