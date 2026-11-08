"""Headline tokenizer.

A headline becomes a *set* of keywords: one article counts at most once per
keyword, no matter how often the word appears in it (document frequency).
"""
from __future__ import annotations

import re

# Standard English function words.
_BASE_STOPWORDS = """
a about above after again against all almost also am among an and any are aren't as at
be because been before being below between both but by can can't cannot could couldn't
did didn't do does doesn't doing don't down during each either else ever every few for
from further get gets getting got had hadn't has hasn't have haven't having he he'd he'll
he's her here here's hers herself him himself his how how's however i i'd i'll i'm i've if
in into is isn't it it's its itself just let's like many may me might more most much must
mustn't my myself neither no nor not now of off often on once one only or other ought our
ours ourselves out over own per rather same shan't she she'd she'll she's should shouldn't
since so some still such than that that's the their theirs them themselves then there
there's these they they'd they'll they're they've this those though through to too toward
towards under until up upon us very via was wasn't we we'd we'll we're we've were weren't
what what's when when's where where's whether which while who who's whom whose why why's
will with within without won't would wouldn't yet you you'd you'll you're you've your yours
yourself yourselves
""".split()

# Words that are frequent in HuffPost headlines but say nothing about the topic
# (headline templates, numerals, generic verbs). Chosen by inspecting the most
# frequent tokens of the full dataset; see README "Tokenizer" section.
_NEWS_STOPWORDS = """
new says said say saying video videos photo photos watch see seen look looks looking
best worst first last next ways way things thing make makes making made get take takes
taking took give gives giving gave go goes going gone come comes coming came know knows
want wants need needs needed tell tells told think thinks try trying day days week weeks
year years time times today tonight yesterday tomorrow one two three four five six seven
eight nine ten 'll 're 've n't don't really actually even back big little good great better
well right real reasons reason why here's what's lot lots find found finds ever never
everything something nothing anything everyone someone people person man woman men women
kid kids guy guys life world thing stuff part show shows shown via amid us u.s huffpost
huff post read reads live lives keep keeps put puts call calls called run runs ask asks
asked feel feels let lets help helps use used using start starts end ends move moves
long high top full old young another may might could would should must every week's
year's day's time's mr ms mrs dr st vs ok yes no reveals around
story news report tips
""".split()

STOPWORDS = frozenset(_BASE_STOPWORDS) | frozenset(_NEWS_STOPWORDS)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9']*[A-Za-z0-9]|[A-Za-z]")
_POSSESSIVE_RE = re.compile(r"(?:'s|s')$")
_QUOTE_TRANSLATE = str.maketrans({"’": "'", "‘": "'", "—": " ", "–": " ", "-": " "})
_DOTTED_RE = re.compile(r"\b(?:[A-Z]\.){2,}")          # U.S. / U.K. / U.N.

# Short upper-case acronyms that are real topics but would fail MIN_LEN or the
# stopword list ("US" vs the pronoun "us"). Matched case-sensitively, so the
# pronoun "Us" in a Title Case headline is not affected. Output names keep the
# dots for country codes so "u.s." never collides with the pronoun.
ACRONYMS = {"US": "u.s.", "UK": "u.k.", "EU": "eu", "UN": "un", "AI": "ai"}

MIN_LEN = 3


def normalize_token(tok: str) -> str:
    tok = _POSSESSIVE_RE.sub("", tok.strip("'-"))
    return tok.strip("'-")


def _prepare(text: str) -> tuple[str, bool]:
    text = text.translate(_QUOTE_TRANSLATE)
    text = _DOTTED_RE.sub(lambda m: m.group(0).replace(".", ""), text)
    return text, text.isupper()        # all-caps headline: acronym test is meaningless


def _keyword(raw: str, shouting: bool) -> str | None:
    """The keyword a raw word maps to, or None if it is filtered out."""
    if not shouting and raw in ACRONYMS:
        return ACRONYMS[raw]
    tok = normalize_token(raw.lower())
    if len(tok) < MIN_LEN or tok in STOPWORDS or tok.isdigit():
        return None
    if any(ch.isdigit() for ch in tok):  # "2016", "5g" style tokens are noise here
        return None
    return tok


def tokenize(text: str) -> list[str]:
    """Return the distinct keywords of ``text`` in first-appearance order.

    Rules (see README "전처리 규칙"): lower-case words of length >= 3 that are
    not stopwords and contain no digits; hyphenated words are split
    ("COVID-19" -> "covid"); possessives are dropped; whitelisted acronyms
    (AI, UK, EU, UN, US / U.S.) are kept. Multi-word names are handled
    separately by :func:`phrases`.
    """
    text, shouting = _prepare(text)
    seen: dict[str, None] = {}
    for raw in _TOKEN_RE.findall(text):
        tok = _keyword(raw, shouting)
        if tok is not None:
            seen.setdefault(tok, None)
    return list(seen)


# Phrase (bigram) extraction ------------------------------------------------------
# Headlines are Title Case, so capitalisation cannot find names. Instead every pair
# of *adjacent* keywords inside one clause becomes a phrase candidate
# ("Donald Trump" -> "donald trump", "Supreme Court Justice" -> "supreme court",
# "court justice"). Frequent pairs are mostly collocations and names; rare pairs
# are pruned by the decaying window like any other rare keyword.
#   * pairs never cross punctuation (":", ",", "?", quotes ...) or a removed word,
#     so "Trump: White House ..." does not produce "trump white"
#   * "new" is a stopword but may start a phrase ("new york", "new hampshire")
_CLAUSE_SPLIT_RE = re.compile(r"[:;,.!?\"()\[\]|/]+|\s'|'\s")
PHRASE_STARTERS = frozenset({"new"})


def phrases(text: str) -> list[str]:
    """Distinct adjacent keyword pairs of ``text`` ("a b"), first-appearance order."""
    text, shouting = _prepare(text)
    seen: dict[str, None] = {}
    for clause in _CLAUSE_SPLIT_RE.split(text):
        prev: str | None = None
        for raw in _TOKEN_RE.findall(clause):
            tok = _keyword(raw, shouting)
            if tok is not None and prev is not None and tok != prev:
                seen.setdefault(f"{prev} {tok}", None)
            if tok is not None:
                prev = tok
            elif raw.lower() in PHRASE_STARTERS:
                prev = raw.lower()
            else:
                prev = None
    return list(seen)
