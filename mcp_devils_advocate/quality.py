"""Anti-gaming quality gate for review submissions.

The length floors in ``core`` only count characters, so on their own they
accept thirty ``x`` characters, the same counterargument pasted three times,
a "counter" that repeats the claim word for word, or a rebuttal copied from
the counterargument it rebuts. This module closes those shortcuts with a few
cheap, deterministic text heuristics:

* **low information** — too few distinct content words, one word dominating
  the text, or keyboard-mash runs such as ``xxxxxxxx``;
* **near-duplicates** — two items in the same phase whose content-word sets
  overlap (Jaccard similarity) at or above :data:`DUPLICATE_SIMILARITY`;
* **restating the claim** — text that adds almost nothing beyond the words of
  the claim under review;
* **parroting the target** — a rebuttal, mitigation, test or response that
  mostly copies the item it is supposed to answer.

These are heuristics, not a semantic judge: they reject degenerate and
copy-paste submissions, they cannot certify that an argument is *good*.
Everything here is pure standard library and fully deterministic, so the same
submission always gets the same answer.

Text is compared as sets of *content words*: lowercased, accents stripped,
punctuation removed, a small English + Spanish stopword and filler list
dropped, and a tiny suffix-stripping stemmer applied so that ``rewrite`` /
``rewriting`` or ``service`` / ``services`` count as the same word.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Iterable

# ---------------------------------------------------------------------------
# Thresholds (documented in the README and returned in phase instructions)
# ---------------------------------------------------------------------------

#: Distinct content words required in a long field
#: (counterarguments and steelman points).
MIN_CONTENT_WORDS_LONG = 4
#: Distinct content words required in every other free-text field.
MIN_CONTENT_WORDS_SHORT = 3
#: A single word may not make up more than this share of all content words...
MAX_WORD_DOMINANCE = 0.5
#: ...once the text has at least this many content words.
DOMINANCE_MIN_WORDS = 6
#: Two items whose content-word sets have a Jaccard similarity at or above
#: this value are near-duplicates.
DUPLICATE_SIMILARITY = 0.8
#: Text whose content words are at least this share covered by a reference
#: text (the claim, or the item being answered) adds nothing of its own...
COPY_COVERAGE = 0.8
#: ...and so does text that brings fewer than this many content words of
#: its own that do not appear in the reference.
MIN_NEW_WORDS = 2

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# Function words, negations and empty evaluative filler, in English and
# Spanish. Negations are dropped on purpose: "we should NOT do X" adds no
# argument to "we should do X", so it must count as a restatement.
_STOPWORDS = frozenset(
    """
    a about above after again against all almost also although am an and any are
    around as at be because been before being below between both but by can
    cannot could did do does doing done down during each either else enough etc
    even ever every few for from further get gets getting go going got had has
    have having he her here hers him his how however i if in into is it its
    itself just let lets like may me might more most much must my myself neither
    no nor not now of off on once one only or other others our ours ourselves out
    over own per quite rather same shall she should so some such than that the
    their theirs them themselves then there these they this those though through
    thus to too under until up upon us very via was we were what when where
    whether which while who whom whose why will with within without would yes yet
    you your yours yourself dont doesnt isnt arent wasnt werent wont wouldnt
    shouldnt cant couldnt didnt hasnt havent hadnt thing things stuff lot lots
    really actually basically simply clearly obviously definitely probably maybe
    perhaps certainly surely totally good bad great terrible awful nice fine
    right wrong better worse best worst idea ideas point points claim claims
    argument arguments counterargument counter isn aren wasn weren don doesn didn
    won wouldn shouldn couldn hasn haven hadn ll ve re
    el la los las un una unos unas lo al del de en y e o u ni que se su sus es son
    ser fue era eran esta este esto estos estas ese esa eso esos esas aquel
    aquella para por con sin sobre entre hasta desde muy mas menos pero sino
    tambien ya no si como cuando donde porque pues tan tanto le les me te nos
    mi mis tu tus nuestro nuestra nuestros nuestras hay ha han he hemos debe
    deben deberiamos deberia debemos puede pueden cosa cosas bueno buena malo
    mala mejor peor idea
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")
_MASH_RE = re.compile(r"([a-z])\1{3,}")  # the same letter four or more times in a row


def normalize(text: str) -> str:
    """Lowercase, strip accents, and turn every non-alphanumeric run into a space."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(_WORD_RE.findall(stripped))


def _stem(word: str) -> str:
    """Tiny, conservative suffix stripper (``services`` -> ``servic`` <- ``service``)."""
    if len(word) <= 4 or not word.isalpha():
        return word
    if word.endswith("ies") and len(word) > 5:
        return word[:-3] + "y"
    for suffix in ("ing", "edly", "ed", "es", "s", "ly", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith("ss"):
                continue
            return word[: -len(suffix)]
    return word


def content_words(text: str) -> list[str]:
    """Stemmed content words of ``text``, in order, repeats kept.

    Stopwords, one-letter tokens and keyboard-mash tokens (``xxxx``) are
    dropped. Numbers are kept: "6 months" and "12 months" differ.
    """
    words = []
    for token in normalize(text).split():
        if token in _STOPWORDS or _MASH_RE.search(token):
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        words.append(_stem(token))
    return words


def word_set(text: str) -> frozenset[str]:
    return frozenset(content_words(text))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard similarity of two word collections (0.0 when both are empty)."""
    sa, sb = set(a), set(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def coverage(words: Iterable[str], reference: Iterable[str]) -> float:
    """Share of ``words`` that also appear in ``reference``."""
    sw = set(words)
    return len(sw & set(reference)) / len(sw) if sw else 1.0


# ---------------------------------------------------------------------------
# Individual checks — each returns a human-readable problem or None
# ---------------------------------------------------------------------------


def low_information(text: str, min_words: int) -> str | None:
    """Reject junk: too few distinct content words, or one word repeated to pad length."""
    words = content_words(text)
    distinct = set(words)
    if len(distinct) < min_words:
        return (
            f"has too little information — only {len(distinct)} distinct content "
            f"word(s), need at least {min_words}; state a specific, concrete point "
            "instead of padding to the length floor"
        )
    if len(words) >= DOMINANCE_MIN_WORDS:
        word, count = Counter(words).most_common(1)[0]
        share = count / len(words)
        if share > MAX_WORD_DOMINANCE:
            return (
                f"is repetitive — the word '{word}' is {share:.0%} of the content; "
                "say something new instead of repeating yourself"
            )
    return None


def copies(text: str, reference: str) -> tuple[bool, float]:
    """Does ``text`` add fewer than :data:`MIN_NEW_WORDS` words beyond ``reference``?

    Returns ``(is_copy, coverage)`` where coverage is the share of the text's
    content words that also appear in the reference.
    """
    words = word_set(text)
    ref = word_set(reference)
    cov = coverage(words, ref)
    new = len(words - ref)
    return (cov >= COPY_COVERAGE or new < MIN_NEW_WORDS), cov


def restates_claim(text: str, claim: str) -> str | None:
    is_copy, cov = copies(text, claim)
    if is_copy:
        return (
            f"restates the claim instead of arguing ({cov:.0%} of its content words "
            "come from the claim) — bring a concrete reason, fact or mechanism"
        )
    return None


def parrots_target(text: str, target: str, target_kind: str) -> str | None:
    is_copy, cov = copies(text, target)
    if is_copy:
        return (
            f"parrots the {target_kind} it answers ({cov:.0%} of its content words are "
            f"copied from it) — respond with your own reasoning, not the {target_kind}'s words"
        )
    return None


def near_duplicate(text: str, others: list[tuple[str, str]], noun: str) -> str | None:
    """``others`` is ``[(label, text), ...]`` — earlier items in the batch and saved items."""
    words = word_set(text)
    best_label, best_sim = None, 0.0
    for label, other in others:
        sim = jaccard(words, word_set(other))
        if sim > best_sim:
            best_label, best_sim = label, sim
    if best_label is not None and best_sim >= DUPLICATE_SIMILARITY:
        return (
            f"is a near-duplicate of {best_label} (similarity {best_sim:.2f}) — each "
            f"{noun} must make a distinct point"
        )
    return None


# ---------------------------------------------------------------------------
# Batch gate used by core.ReviewStore._validate_batch
# ---------------------------------------------------------------------------

# phase -> (text field, human noun, min content words,
#           check claim restatement?, target kind or None)
PHASE_RULES: dict[str, tuple[str, str, int, bool, str | None]] = {
    "counterarguments": ("text", "counterargument", MIN_CONTENT_WORDS_LONG, True, None),
    "rebuttals": ("justification", "rebuttal", MIN_CONTENT_WORDS_SHORT, True, "counterargument"),
    "failure_causes": ("text", "failure cause", MIN_CONTENT_WORDS_SHORT, False, None),
    "mitigations": ("action", "mitigation", MIN_CONTENT_WORDS_SHORT, False, "failure cause"),
    "assumptions": ("text", "assumption", MIN_CONTENT_WORDS_SHORT, False, None),
    "tests": ("test", "test", MIN_CONTENT_WORDS_SHORT, False, "assumption"),
    "strongest_case": ("text", "opposing point", MIN_CONTENT_WORDS_LONG, True, None),
    "responses": ("text", "response", MIN_CONTENT_WORDS_SHORT, False, "opposing point"),
}


def check_batch(
    phase: str,
    claim: str,
    candidates: list[tuple[int, dict]],
    saved: list[dict],
    target_texts: dict[int, str],
) -> list[tuple[int, str]]:
    """Run every quality check for ``phase`` over already field-validated items.

    Args:
        phase: the current phase name.
        claim: the claim under review.
        candidates: ``(batch_index, cleaned_item)`` pairs that passed field validation.
        saved: items already accepted in this phase by earlier submissions.
        target_texts: for reference phases, ``{target_index: target_text}``.

    Returns:
        ``(batch_index, problem)`` pairs; empty when the batch is clean.
    """
    rule = PHASE_RULES.get(phase)
    if rule is None:
        return []
    field, noun, min_words, check_claim, target_kind = rule
    problems: list[tuple[int, str]] = []
    others: list[tuple[str, str]] = [
        (f"already-saved {noun} {i}", item[field]) for i, item in enumerate(saved)
    ]
    for idx, item in candidates:
        text = item[field]
        problem = low_information(text, min_words)
        if problem is None and check_claim:
            problem = restates_claim(text, claim)
        if problem is None and phase == "responses" and item.get("stance") == "counter":
            problem = restates_claim(text, claim)
            if problem:
                problem = "is a 'counter' that " + problem
        if problem is None and target_kind is not None and item.get("index") in target_texts:
            problem = parrots_target(text, target_texts[item["index"]], target_kind)
        if problem is None:
            problem = near_duplicate(text, others, noun)
        if problem is not None:
            problems.append((idx, f"'{field}' {problem}"))
        others.append((f"item {idx}", text))
    return problems


def rules_for(phase: str) -> list[str]:
    """Plain-language quality rules for ``phase``, returned in its instructions."""
    rule = PHASE_RULES.get(phase)
    if rule is None:
        return []
    field, noun, min_words, check_claim, target_kind = rule
    rules = [
        f"'{field}' needs at least {min_words} distinct content words (stopwords and "
        "filler like 'good', 'bad', 'really' do not count), and once it has "
        f"{DOMINANCE_MIN_WORDS}+ content words no single word may make up more than "
        f"{MAX_WORD_DOMINANCE:.0%} of them — junk padding is rejected.",
        f"Each {noun} must be distinct: a Jaccard word-overlap of "
        f">= {DUPLICATE_SIMILARITY} with another {noun} in this phase is rejected as a "
        "near-duplicate.",
    ]
    if check_claim:
        rules.append(
            f"A {noun} that only restates the claim (>= {COPY_COVERAGE:.0%} of its content "
            f"words from the claim, or fewer than {MIN_NEW_WORDS} new ones) is rejected."
        )
    if phase == "responses":
        rules.append(
            "A 'counter' response that only restates the claim is rejected — counter with "
            "a real argument or concede."
        )
    if target_kind is not None:
        rules.append(
            f"A {noun} that parrots the {target_kind} it answers (>= {COPY_COVERAGE:.0%} of "
            f"its content words copied, or fewer than {MIN_NEW_WORDS} new ones) is rejected."
        )
    return rules
