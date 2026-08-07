"""Build PostgreSQL tsquery strings from user search input.

This module imports nothing from cast2md, so both db/repository.py and
search/repository.py can pull from it without creating an import cycle.
"""

import re

# Common stop words (German + English) to filter from OR queries
# These words are too common to be useful for matching
# fmt: off
# One line per thematic group, not one word per line: this is search data,
# and the grouping is what makes it reviewable. The formatter would explode
# it to ~340 single-word lines, which also breaks the per-line suppressions
# in tools/check-docs.allow that mark it as German language data.
STOP_WORDS = {
    # German pronouns and possessives (with inflections)
    "ich", "du", "er", "sie", "es", "wir", "ihr", "uns", "euch", "ihnen",
    "mein", "meine", "meinen", "meiner", "meinem", "meines",
    "dein", "deine", "deinen", "deiner", "deinem", "deines",
    "sein", "seine", "seinen", "seiner", "seinem", "seines",
    "unser", "unsere", "unseren", "unserer", "unserem", "unseres",
    "euer", "eure", "euren", "eurer", "eurem", "eures",
    "eigen", "eigene", "eigenen", "eigener", "eigenem", "eigenes",
    # German articles
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einen", "einer", "einem", "eines",
    # German conjunctions and prepositions
    "und", "oder", "aber", "wenn", "weil", "dass", "als", "auch", "noch",
    "wie", "was", "wer", "wo", "wann", "warum", "welche", "welcher", "welches",
    "mit", "von", "zu", "zum", "zur", "bei", "beim", "nach", "vor", "für",
    "über", "unter", "auf", "an", "am", "in", "im", "aus", "um",
    "durch", "gegen", "ohne", "bis", "seit", "ob",
    # German verbs (common)
    "ist", "sind", "war", "waren", "wird", "werden", "wurde", "wurden",
    "hat", "haben", "hatte", "hatten", "kann", "können", "konnte", "konnten",
    "muss", "müssen", "musste", "mussten", "soll", "sollen", "sollte", "sollten",
    "will", "wollen", "wollte", "wollten", "darf", "dürfen", "durfte", "durften",
    "macht", "machen", "machte", "machten", "geht", "gehen", "ging", "gingen",
    "gibt", "geben", "gab", "gaben", "kommt", "kommen", "kam", "kamen",
    "sagt", "sagen", "sagte", "sagten", "weiß", "wissen", "wusste", "wussten",
    # German adverbs and misc
    "nicht", "nur", "schon", "sehr", "so", "ja", "nein", "man", "sich",
    "alle", "alles", "andere", "anderen", "anderer", "anderem", "anderes",
    "diesem", "dieser", "dieses", "diese", "diesen",
    "jetzt", "hier", "dort", "dann", "denn", "doch", "immer", "wieder",
    "mehr", "viel", "viele", "vielen", "gut", "ganz", "etwa", "wohl",
    "mal", "eben", "halt", "also", "zwar", "dabei", "davon", "dazu",
    # English
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "them", "us",
    "my", "your", "his", "her", "its", "our", "their", "mine", "yours", "ours",
    "and", "or", "but", "if", "because", "that", "as", "also", "still",
    "the", "a", "an", "this", "these", "those", "some", "any",
    "how", "what", "who", "where", "when", "why", "which",
    "with", "of", "to", "at", "after", "before", "for", "about", "under", "on",
    "in", "from", "around", "through", "against", "without", "until", "since",
    "is", "are", "was", "were", "will", "be", "has", "have", "can", "been",
    "not", "only", "already", "very", "so", "yes", "no", "one", "self",
    "all", "other", "others", "now", "here", "there", "then", "always", "again",
    "more", "much", "many", "most", "such", "same", "both", "each", "every",
    "do", "does", "did", "would", "could", "should", "may", "might",
    "just", "own", "being", "had", "having", "get", "got", "getting",
    "make", "makes", "made", "making", "go", "goes", "went", "going", "gone",
    "come", "comes", "came", "coming", "say", "says", "said", "saying",
    "know", "knows", "knew", "knowing", "think", "thinks", "thought",
}
# fmt: on


def build_flexible_tsquery(query: str) -> str:
    """Build a flexible tsquery string with OR between words.

    - Quoted phrases use AND (exact phrase matching)
    - Unquoted words use OR (flexible matching)
    - Hyphenated words are split into separate OR terms
    - Results are ranked by number of matching terms

    Examples:
        "hello world" -> 'hello' | 'world'
        '"exact phrase"' -> 'exact' & 'phrase'
        'hello "exact phrase" world' -> 'hello' | ('exact' & 'phrase') | 'world'
        'KI-Agenten' -> 'KI' | 'Agenten'
    """
    if not query.strip():
        return ""

    parts = []
    remaining = query.strip()

    # Extract quoted phrases first
    while '"' in remaining:
        # Find quoted phrase
        start = remaining.find('"')
        end = remaining.find('"', start + 1)
        if end == -1:
            # Unclosed quote, treat rest as regular words
            break

        # Add words before the quote as OR terms
        before = remaining[:start].strip()
        if before:
            for word in _split_word(before):
                if word:
                    parts.append(f"'{word}'")

        # Add quoted phrase as AND terms (keep stop words for exact phrases)
        phrase = remaining[start + 1 : end].strip()
        phrase_words = []
        for w in phrase.split():
            phrase_words.extend(_split_word(w, filter_stop_words=False))
        if phrase_words:
            phrase_query = " & ".join(f"'{w}'" for w in phrase_words)
            parts.append(f"({phrase_query})")

        remaining = remaining[end + 1 :]

    # Add remaining words as OR terms
    for word in _split_word(remaining):
        if word:
            parts.append(f"'{word}'")

    if not parts:
        return ""

    return " | ".join(parts)


def _split_word(text: str, filter_stop_words: bool = True) -> list[str]:
    """Split text into words, handling hyphens and special characters.

    - Splits on whitespace and hyphens
    - Removes other non-alphanumeric characters
    - Optionally filters out common stop words
    - Returns list of clean words
    """
    # Replace hyphens with spaces, then split
    text = text.replace("-", " ")
    words = []
    for word in text.split():
        # Remove non-alphanumeric characters (keep umlauts etc via \w)
        clean = re.sub(r"[^\w]", "", word)
        if clean:
            # Filter stop words (case-insensitive)
            if filter_stop_words and clean.lower() in STOP_WORDS:
                continue
            words.append(clean)
    return words
