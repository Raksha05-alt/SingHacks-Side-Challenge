"""investigate() for the Production Incident Investigator use case.

Pipeline, in two genuinely separate stages (see README "Why this is
harder than it looks"):

1. Retrieval (`_ingest_corpus` + `_retrieve_relevant_documents`) — plain
   TF-IDF cosine similarity over fine-grained chunks (paragraphs, plus
   individual log/table lines split out so one relevant line isn't
   diluted by an otherwise-irrelevant block; CSV rows split per-row).
   This finds *what the query is about* and nothing more - it has no
   notion of "root cause" or "confidence".

2. Correlation + calibration (`_correlate_evidence` +
   `_calibrate_confidence`) — takes the entities retrieval surfaced as
   query-relevant, then re-checks the FULL corpus (not just top-K) at
   section granularity (a markdown "## " section, a CSV row, or a whole
   file when there's no finer structure) for two things per entity:
   which independent files positively corroborate it, and which
   sections explicitly hedge/deny the correlation ("unconfirmed", "no
   correlated deployment", "first recorded report", ...). Confidence is
   a direct function of (independent corroborations - hedges), not of
   how strongly the top document matched the query - a single strong
   retrieval hit with zero corroboration and an explicit hedge must
   still land under 50.

Nothing here is keyed to a specific incident's filenames or content -
entity extraction is generic (hyphenated component identifiers, He
Exception/ALL_CAPS error codes, KI-/INC-/RB- ids, version strings) and
section/file-type detection is by structural shape (CSV vs "## "
headers) and by the filename *categories* the brief itself defines
(logs, deployment history, known issues, runbooks, previous incidents),
not by any one incident's specific values.
"""
from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "for", "and", "or", "this", "that", "these",
    "those", "it", "its", "as", "by", "at", "from", "with", "has", "have",
    "had", "not", "no", "than", "after", "before", "during", "over",
    "under", "up", "down", "if", "then", "so", "but", "into", "your",
    "you", "we", "what", "when", "which", "who", "will", "there", "their",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[_\-][a-z0-9]+)*")


def _tokenize(text: str) -> list[str]:
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


# ---------------------------------------------------------------------------
# Stage 1a: ingest -> fine-grained chunks for retrieval
# ---------------------------------------------------------------------------

class Chunk:
    __slots__ = ("chunk_id", "file", "text", "tokens")

    def __init__(self, chunk_id: str, file: str, text: str):
        self.chunk_id = chunk_id
        self.file = file
        self.text = text
        self.tokens = _tokenize(text)


def _split_csv_rows(filename: str, text: str) -> list[Chunk]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    chunks = []
    for row in body:
        if not row or not any(cell.strip() for cell in row):
            continue
        row_id = row[0].strip() or f"row{len(chunks)}"
        rendered = "; ".join(f"{h}: {v}" for h, v in zip(header, row))
        chunks.append(Chunk(f"{filename}#{row_id}", filename, rendered))
    return chunks


def _split_prose_fine(filename: str, text: str) -> list[Chunk]:
    """Paragraph chunks (blank-line separated), plus each individual
    non-trivial line within a multi-line paragraph as its own chunk -
    this is what lets a single relevant log/table line rank on its own
    instead of being averaged away inside a noisy block."""
    chunks: list[Chunk] = []
    para_lines: list[str] = []
    para_idx = 0

    def flush() -> None:
        nonlocal para_idx
        if not para_lines:
            return
        chunks.append(Chunk(f"{filename}#p{para_idx}", filename, "\n".join(para_lines)))
        if len(para_lines) > 1:
            for li, line in enumerate(para_lines):
                stripped = line.strip().strip("`|-").strip()
                if len(stripped) >= 8:
                    chunks.append(Chunk(f"{filename}#p{para_idx}l{li}", filename, line))
        para_idx += 1
        para_lines.clear()

    for line in text.splitlines():
        if line.strip() == "":
            flush()
        else:
            para_lines.append(line)
    flush()
    return chunks


def _ingest_corpus(corpus: dict) -> list[Chunk]:
    chunks: list[Chunk] = []
    for filename, text in corpus.items():
        if filename.lower().endswith(".csv"):
            chunks.extend(_split_csv_rows(filename, text))
        else:
            chunks.extend(_split_prose_fine(filename, text))
    return chunks


# ---------------------------------------------------------------------------
# Stage 1b: retrieve -> TF-IDF cosine similarity, ranked
# ---------------------------------------------------------------------------

def _build_idf(chunks: list[Chunk]) -> dict[str, float]:
    n = len(chunks)
    df: Counter[str] = Counter()
    for c in chunks:
        df.update(set(c.tokens))
    return {t: math.log((n + 1) / (d + 1)) + 1.0 for t, d in df.items()}


def _vectorize(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = Counter(tokens)
    vec = {t: (1.0 + math.log(f)) * idf.get(t, 0.0) for t, f in tf.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / norm for t, v in vec.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def _retrieve_relevant_documents(query: str, corpus: dict) -> list[tuple[Chunk, float]]:
    """Ranks every chunk against `query`. Returns [(chunk, score), ...]
    sorted most relevant first, zero-score chunks dropped."""
    chunks = _ingest_corpus(corpus)
    idf = _build_idf(chunks)
    qvec = _vectorize(_tokenize(query), idf)
    scored = []
    for c in chunks:
        score = _cosine(qvec, _vectorize(c.tokens, idf))
        if score > 0:
            scored.append((c, score))
    scored.sort(key=lambda cs: cs[1], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Stage 2a: entity extraction (generic - no incident-specific strings)
# ---------------------------------------------------------------------------

_COMPONENT_RE = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+){1,4}\b")
_EXCEPTION_RE = re.compile(r"\b[A-Z][a-zA-Z]*(?:Exception|Error|Timeout)\b")
_CONST_RE = re.compile(r"\b[A-Z][A-Z_]{3,}\b")
_ID_RE = re.compile(r"\b(?:KI|INC|RB)-\d+\b")
_VERSION_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")
_LOG_LEVELS = {"INFO", "WARN", "WARNING", "ERROR", "DEBUG", "TRACE", "FATAL"}


def _extract_entities(text: str) -> set[str]:
    ents: set[str] = set()
    ents.update(_COMPONENT_RE.findall(text.lower()))
    ents.update(_EXCEPTION_RE.findall(text))
    ents.update(c for c in _CONST_RE.findall(text) if c not in _LOG_LEVELS)
    ents.update(_ID_RE.findall(text))
    ents.update(_VERSION_RE.findall(text))
    return ents


# ---------------------------------------------------------------------------
# Stage 2b: sections for correlation (coarser than retrieval chunks - a
# whole markdown section / CSV row / whole file, so a hedge phrase
# anywhere near a mention is caught even if it's a few lines away)
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)


def _split_sections(filename: str, text: str) -> list[str]:
    if filename.lower().endswith(".csv"):
        return [c.text for c in _split_csv_rows(filename, text)]
    headers = list(_HEADER_RE.finditer(text))
    if len(headers) <= 1:
        return [text]
    bounds = [h.start() for h in headers] + [len(text)]
    return [text[bounds[i]:bounds[i + 1]] for i in range(len(headers))]


# Hedge/disclaimer detection: pattern-based rather than a fixed phrase
# list. A literal phrase list only catches wording it was written
# against ("unconfirmed", "no correlated deployment") and silently
# misses a paraphrase of the same claim ("has not been established",
# "not believed related", "never pinned down"). These patterns instead
# target the underlying *shape* of a hedge - a negation next to a
# causation/confirmation verb, or a standalone epistemic-uncertainty
# word - so a new incident's own wording is more likely to be caught
# even if it never appeared in the incidents this was developed against.
_HEDGE_PATTERNS = (
    re.compile(
        r"\b(?:not|never|cannot|can.t|couldn.t|isn.t|wasn.t|hasn.t|haven.t|doesn.t|no)\b"
        r"[^.]{0,60}?\b(?:confirm|establish|link|relat|correlat|verif|pin(?:ned)? ?down|reproduc|match|affect)"
    ),
    re.compile(r"\b(?:unconfirmed|unverified|inconclusive|coincidental|anecdotal|speculative)\b"),
    re.compile(r"\bisolated (?:report|reports|incident|incidents|case|cases|occurrence|occurrences)\b"),
    re.compile(r"\b(?:no|not any) (?:prior|previous|earlier) (?:incident|report|record)s?\b"),
    re.compile(r"\bfirst (?:recorded|reported)\b"),
    re.compile(r"\bnot (?:currently )?(?:exposed|instrumented)\b"),
    re.compile(r"\bmay not (?:apply|be)\b"),
    re.compile(r"\bcosmetic only\b|\bby design\b|\bseparate from\b"),
    # deliberately NOT a standalone "unrelated" trigger: both incident A's
    # logs.md and deployment_history.md legitimately use that word to
    # describe *other* decoy content in the same untitled/no-header
    # section as the real evidence (e.g. "a handful of entries from
    # unrelated systems") - it self-hedged genuinely clean files during
    # testing against a synthetic third incident. "unrelated to X" /
    # "no deployment correlated" style claims are still caught above.
)


def _is_hedged(text: str) -> bool:
    return any(p.search(text) for p in _HEDGE_PATTERNS)


def _file_type(filename: str) -> str:
    name = filename.lower()
    for key in ("log", "deployment", "known_issue", "runbook", "previous_incident", "architecture", "api_spec"):
        if key in name:
            return key
    return "other"


def _pick_anchor_and_neighbors(
    ranked: list[tuple[Chunk, float]], max_neighbors: int = 3
) -> tuple[str | None, list[str]]:
    """The anchor is chosen from retrieval alone - which entity is most
    central to what the query is actually about (appears across the most
    distinct files among the top-ranked chunks, tie-broken by relevance
    score). Deliberately NOT chosen by which entity's downstream
    correlation looks best - that would let an unrelated-but-uncorroborated
    entity win just because nothing contradicts it, which is the opposite
    of what retrieval is supposed to establish.

    Neighbors are entities tightly co-occurring with the anchor - found in
    the SAME single-line chunk as the anchor among the top-ranked chunks.
    Restricted to single-line chunks specifically (not whole paragraphs):
    a coarse "here are all our components" bullet-list paragraph would
    otherwise turn every component in the system into a "neighbor" just
    because they're all described together in one place. A shared single
    line - the same log line, the same table row, the same sentence - is
    a much stronger signal that two components are actually coupled in
    this incident (e.g. "checkout-service ... calls inventory-service").
    """
    top = ranked[:15]
    files_by_entity: dict[str, set[str]] = defaultdict(set)
    score_by_entity: dict[str, float] = defaultdict(float)
    for chunk, score in top:
        for ent in _extract_entities(chunk.text):
            files_by_entity[ent].add(chunk.file)
            score_by_entity[ent] += score

    if not files_by_entity:
        return None, []

    multi_file = [e for e in files_by_entity if len(files_by_entity[e]) >= 2]
    pool = multi_file or list(files_by_entity)
    anchor = max(pool, key=lambda e: (len(files_by_entity[e]), score_by_entity[e]))

    neighbor_counts: Counter[str] = Counter()
    for chunk, _score in top:
        if "\n" in chunk.text:
            continue
        ents = _extract_entities(chunk.text)
        if anchor not in ents:
            continue
        neighbor_counts.update(e for e in ents if e != anchor)
    neighbors = [e for e, _ in neighbor_counts.most_common(max_neighbors)]
    return anchor, neighbors


def _correlate_evidence(corpus: dict, ranked: list[tuple[Chunk, float]]) -> dict | None:
    """Measures the anchor entity's support across the FULL corpus (not
    just the top-ranked chunks) at section granularity: which files
    positively corroborate it (a matching section with no hedge language)
    vs. which files mention it but explicitly hedge/deny the correlation.

    A hedge is also honored when it's attached to a closely co-occurring
    *neighbor* entity rather than the literal anchor string - a causally
    coupled component's disclaimer (e.g. a known-issue note about
    inventory-service that's never been pinned down, when the anchor is
    checkout-service which calls it) is real signal about the anchor's
    own confidence, not noise. A neighbor mention WITHOUT a hedge is
    ignored either way - it isn't independent corroboration of the
    anchor, just a mention of a related component."""
    anchor, neighbors = _pick_anchor_and_neighbors(ranked)
    if anchor is None:
        return None

    anchor_pattern = re.compile(re.escape(anchor), re.IGNORECASE)
    neighbor_patterns = [re.compile(re.escape(n), re.IGNORECASE) for n in neighbors]

    supporting: dict[str, str] = {}
    hedged: dict[str, str] = {}
    for filename, text in corpus.items():
        for section in _split_sections(filename, text):
            anchor_hit = bool(anchor_pattern.search(section))
            neighbor_hit = any(p.search(section) for p in neighbor_patterns)
            if not anchor_hit and not neighbor_hit:
                continue
            # collapse whitespace/line-wraps before hedge matching - a
            # hedge phrase can be split across a soft-wrapped markdown
            # line ("no deployment\ncorrelated ...") and a literal
            # substring check on raw text would otherwise miss it.
            lowered = " ".join(section.split()).lower()
            is_hedged = _is_hedged(lowered)
            if anchor_hit and not is_hedged:
                # keep the FIRST clean match per file, not the last - a
                # later section that also happens to mention the anchor
                # (e.g. a different API endpoint noting it does *not*
                # call the anchor) shouldn't silently replace a better,
                # earlier excerpt just by appearing later in the file.
                if filename not in supporting:
                    supporting[filename] = section
                hedged.pop(filename, None)
            elif is_hedged and filename not in supporting:
                if filename not in hedged or len(section) > len(hedged[filename]):
                    hedged[filename] = section

    return {
        "entity": anchor,
        "neighbors": neighbors,
        "supporting": supporting,
        "hedged": hedged,
        "net": len(supporting) - len(hedged),
    }


# ---------------------------------------------------------------------------
# Stage 2c: fact extraction from corroborating sections (generic patterns,
# not incident-specific strings)
# ---------------------------------------------------------------------------

_MTTR_RE = re.compile(r"Typical MTTR:\s*(\d+)\s*minutes?", re.IGNORECASE)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$", re.MULTILINE)
_ERROR_LINE_RE = re.compile(r"^.*\bERROR\b.*$", re.MULTILINE)
_WARN_LINE_RE = re.compile(r"^.*\bWARN\b.*$", re.MULTILINE)
_BOLD_COMPONENT_RE = re.compile(r"\*\*([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\*\*")


def _first_match(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(" ".join(text.split()))
    return m.group(1).strip() if m else None


def _capture_field(section: str, label: str) -> str | None:
    """Grabs the text after a "**Label**:" marker up to the next blank
    line or bold marker - handles the label's value soft-wrapping across
    multiple source lines, which a single-line regex would truncate."""
    m = re.search(rf"\*\*{re.escape(label)}\*\*:\s*(.+?)(?=\n\s*\n|\n\s*\*\*[A-Z]|\Z)", section, re.DOTALL)
    if not m:
        return None
    return " ".join(m.group(1).split())


def _canonical_components(corpus: dict) -> set[str]:
    """Component names as declared in the architecture doc (bulleted,
    bolded, e.g. "- **payment-service**: ..."). Used to keep
    impacted_systems to real components instead of any hyphenated word
    that happens to appear near the evidence."""
    components: set[str] = set()
    for filename, text in corpus.items():
        if _file_type(filename) == "architecture":
            components.update(_BOLD_COMPONENT_RE.findall(text.lower()))
    return components


def _fact_for_section(filename: str, section: str, entity: str) -> str | None:
    ftype = _file_type(filename)
    if ftype == "deployment":
        for row in _TABLE_ROW_RE.findall(section):
            if entity.lower() in row.lower():
                cells = [c.strip().strip("*") for c in row.split("|") if c.strip()]
                return "deployment change: " + " / ".join(cells)
    if ftype == "known_issue":
        return f"known-issue catalog match: {section.strip()}"
    if ftype == "previous_incident":
        rc = _capture_field(section, "Root cause")
        header = section.strip().splitlines()[0].lstrip("#").strip() if section.strip() else ""
        if rc:
            return f"precedent {header}: {rc}"
    if ftype == "runbook":
        sym = _capture_field(section, "Symptoms")
        if sym:
            return f"matches runbook symptoms: {sym}"
    if ftype == "log":
        for line in _ERROR_LINE_RE.findall(section):
            if entity.lower() in line.lower():
                return f"log evidence: {line.strip()}"
        # no ERROR-level line naming this entity - a latency-style incident
        # (no failures, just delay) may only ever show up as a WARN
        for line in _WARN_LINE_RE.findall(section):
            if entity.lower() in line.lower():
                return f"log evidence (warning only, no error): {line.strip()}"
    return None


def _excerpt(section: str, entity: str, limit: int = 240) -> str:
    for line in section.splitlines():
        if entity.lower() in line.lower():
            line = line.strip()
            return line if len(line) <= limit else line[:limit] + "..."
    text = section.strip()
    return text if len(text) <= limit else text[:limit] + "..."


# ---------------------------------------------------------------------------
# Stage 2d: confidence calibration
# ---------------------------------------------------------------------------

def _calibrate_confidence(evidence: dict) -> float:
    net = evidence["net"]
    hedged_count = len(evidence["hedged"])
    if net <= 0:
        score = 15.0 + 5.0 * len(evidence["supporting"])
    elif net == 1:
        score = 35.0
    else:
        score = min(100.0, 40.0 + 15.0 * (net - 1))
    if hedged_count > 0 and net <= 1:
        score = min(score, 40.0)
    return round(min(max(score, 0.0), 100.0), 1)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def investigate(query: str, corpus: dict) -> dict:
    ranked = _retrieve_relevant_documents(query, corpus)
    evidence = _correlate_evidence(corpus, ranked)

    if evidence is None:
        return {
            "root_cause": "No corroborated evidence found in the corpus for this query.",
            "supporting_evidence": [],
            "impacted_systems": [],
            "mttr_minutes": None,
            "remediation": "Insufficient evidence to recommend a specific remediation; escalate for manual investigation.",
            "confidence_score": 0.0,
            "needs_human_review": True,
        }

    entity = evidence["entity"]
    supporting = evidence["supporting"]
    hedged = evidence["hedged"]
    net = evidence["net"]

    supporting_evidence = [
        {"source": filename, "excerpt": _excerpt(section, entity)}
        for filename, section in supporting.items()
    ]

    facts = [f for f in (_fact_for_section(fn, sec, entity) for fn, sec in supporting.items()) if f]

    # Only pull components mentioned on the same line as the anchor entity
    # (not the whole section) - a section can legitimately describe every
    # component in the system (e.g. an architecture doc's full component
    # list), and that shouldn't make all of them "impacted".
    canonical = _canonical_components(corpus)
    impacted = set()
    for section in supporting.values():
        for line in section.splitlines():
            if entity.lower() not in line.lower():
                continue
            found = _COMPONENT_RE.findall(line.lower())
            impacted.update(c for c in found if not canonical or c in canonical)
    if _COMPONENT_RE.fullmatch(entity) and (not canonical or entity in canonical):
        impacted.add(entity)
    impacted_systems = sorted(impacted)[:6]
    if not impacted_systems:
        impacted_systems = [entity]

    mttr_minutes = None
    remediation = None
    for section in supporting.values():
        if mttr_minutes is None:
            m = _first_match(_MTTR_RE, section)
            if m is not None:
                mttr_minutes = int(m)
        if remediation is None:
            r = _capture_field(section, "Remediation")
            if r:
                remediation = r

    confidence_score = _calibrate_confidence(evidence)
    needs_human_review = confidence_score < 50

    if net >= 2:
        root_cause = (
            f"{entity} is the probable root cause, corroborated by {net} "
            f"independent sources ({', '.join(sorted(supporting))}): "
            + "; ".join(facts[:4]) + "."
        )
        if remediation is None:
            remediation = f"Address the issue in {entity} per the corroborating evidence above."
    else:
        missing_types = sorted({
            _file_type(fn) for fn in corpus
            if fn not in supporting
        } | {_file_type(fn) for fn in hedged})
        gap_note = (
            f"No unhedged corroboration was found across independent sources "
            f"(gaps or explicit disclaimers in: {', '.join(missing_types)})."
            if missing_types else
            "No independent source corroborates this without a hedge/disclaimer."
        )
        weak_lead = facts[0] if facts else (
            _excerpt(next(iter(supporting.values())), entity) if supporting
            else _excerpt(next(iter(hedged.values())), entity) if hedged else "no direct textual evidence"
        )
        root_cause = (
            f"Evidence is too thin for a confident root cause. The only lead points at "
            f"{entity} ({weak_lead}). {gap_note} This should be treated as unresolved, "
            f"not a confirmed diagnosis."
        )
        if remediation is None:
            remediation = (
                f"Do not act on {entity} as a confirmed root cause yet; add monitoring/"
                f"instrumentation to confirm or rule it out, then re-investigate once "
                f"corroborating data exists. Escalate for manual investigation in the meantime."
            )
        if not supporting_evidence and hedged:
            supporting_evidence = [
                {"source": filename, "excerpt": _excerpt(section, entity)}
                for filename, section in hedged.items()
            ]

    return {
        "root_cause": root_cause,
        "supporting_evidence": supporting_evidence,
        "impacted_systems": impacted_systems,
        "mttr_minutes": mttr_minutes,
        "remediation": remediation,
        "confidence_score": confidence_score,
        "needs_human_review": needs_human_review,
    }
