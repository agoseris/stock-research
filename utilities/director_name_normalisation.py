"""
director_name_normalisation.py

Shared utility for normalising and matching director names across data sources.

Used at ingestion time (normalise_for_storage) and at query time (names_match).
Handles the inconsistent formatting observed in real RNS/PDMR filings.
"""

import re
from rapidfuzz import fuzz


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HONORIFICS = {
    "dr", "mr", "mrs", "ms", "miss", "prof", "professor",
    "sir", "dame", "lord", "lady", "rev", "reverend",
    "captain", "capt", "major", "col", "colonel",
}

# Common nickname pairs — used to flag no-match results as low-confidence
# when first names are known short forms of each other.
# Keys map to the set of names that are plausible aliases.
_NICKNAME_LOOKUP: dict = {
    "james":       {"jim", "jamie"},
    "jim":         {"james", "jamie"},
    "jamie":       {"james", "jim"},
    "william":     {"will", "bill", "billy"},
    "will":        {"william", "bill"},
    "bill":        {"william", "will"},
    "billy":       {"william"},
    "robert":      {"rob", "bob", "bobby"},
    "rob":         {"robert", "bob"},
    "bob":         {"robert", "rob"},
    "bobby":       {"robert", "rob"},
    "richard":     {"rick", "dick", "rich"},
    "rick":        {"richard"},
    "dick":        {"richard"},
    "thomas":      {"tom", "tommy"},
    "tom":         {"thomas"},
    "edward":      {"ed", "ted", "ned"},
    "ted":         {"edward"},
    "ed":          {"edward"},
    "michael":     {"mike", "mick"},
    "mike":        {"michael"},
    "mick":        {"michael"},
    "christopher": {"chris"},
    "chris":       {"christopher"},
    "nicholas":    {"nick"},
    "nick":        {"nicholas"},
    "jonathan":    {"jon"},
    "jon":         {"jonathan"},
    "katherine":   {"kate", "kathy", "cathy"},
    "catherine":   {"kate", "kathy", "cathy"},
    "kate":        {"katherine", "catherine"},
    "kathy":       {"katherine", "catherine"},
    "elizabeth":   {"liz", "beth", "eliza"},
    "liz":         {"elizabeth"},
    "beth":        {"elizabeth"},
    "patricia":    {"pat", "patty", "tricia"},
    "pat":         {"patricia"},
    "jennifer":    {"jenny", "jen"},
    "jenny":       {"jennifer"},
    "jen":         {"jennifer"},
    "margaret":    {"maggie", "peggy"},
    "maggie":      {"margaret"},
    "peggy":       {"margaret"},
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalise_director_name(raw_name: str) -> dict:
    """
    Normalise a raw director name string to a consistent canonical form.

    Returns a dict with keys:
      normalised      - canonical lowercase form
      abbreviated     - True if first name is initial only
      format_detected - "standard" | "surname_first" | "abbreviated" |
                        "all_caps" | "unknown"
      confidence      - "high" | "medium" | "low"
      notes           - description of transformations applied
    """
    if not raw_name or not raw_name.strip():
        return {
            "normalised": "",
            "abbreviated": False,
            "format_detected": "unknown",
            "confidence": "low",
            "notes": "empty input",
        }

    name = raw_name.strip()
    notes = []
    format_detected = "standard"

    # --- Step 1: detect and correct surname-first format ("SMITH, John") -----
    if "," in name:
        parts = name.split(",", 1)
        surname = parts[0].strip()
        forename = parts[1].strip()
        name = f"{forename} {surname}"
        format_detected = "surname_first"
        notes.append("surname-first format corrected")

    # --- Step 2: detect all-caps (before title-casing) -----------------------
    # All-caps if every alpha char is uppercase and there are alpha chars
    alpha_chars = [c for c in name if c.isalpha()]
    if alpha_chars and all(c.isupper() for c in alpha_chars):
        if format_detected == "standard":
            format_detected = "all_caps"
        notes.append("all-caps normalised")

    # --- Step 3: title-case for consistent processing ------------------------
    # Preserve hyphens: split on spaces, title-case each token, rejoin
    # For hyphenated surnames, title-case each hyphen-part independently
    def _title_token(token):
        if "-" in token:
            return "-".join(part.capitalize() for part in token.split("-"))
        return token.capitalize()

    name = " ".join(_title_token(t) for t in name.split())

    # --- Step 4: strip honorifics --------------------------------------------
    tokens = name.split()
    # Strip leading honorifics (may be multiple: "Prof. Dr. Smith")
    while tokens:
        candidate = re.sub(r"[.\s]", "", tokens[0]).lower()
        if candidate in HONORIFICS:
            notes.append(f"honorific stripped ({tokens[0]})")
            tokens = tokens[1:]
        else:
            break
    name = " ".join(tokens)

    # --- Step 5: strip middle initials ---------------------------------------
    # A middle initial is: a single letter (possibly followed by a dot)
    # that is neither the first nor the last token
    tokens = name.split()
    if len(tokens) >= 3:
        # Check positions 1..n-2 (not first or last)
        filtered = [tokens[0]]
        for tok in tokens[1:-1]:
            clean = tok.rstrip(".")
            if len(clean) == 1 and clean.isalpha():
                notes.append("middle initial stripped")
                # don't append — skip it
            else:
                filtered.append(tok)
        filtered.append(tokens[-1])
        tokens = filtered
        name = " ".join(tokens)

    # --- Step 6: strip remaining punctuation (except hyphens) ----------------
    name = re.sub(r"[^\w\s\-]", "", name)

    # --- Step 7: normalise whitespace ----------------------------------------
    name = re.sub(r"\s+", " ", name).strip()

    # --- Step 8: lowercase ---------------------------------------------------
    name = name.lower()

    # --- Step 9: detect abbreviated first name -------------------------------
    # After all transforms, check if first token is a single letter
    tokens = name.split()
    abbreviated = bool(tokens) and len(tokens[0]) == 1 and tokens[0].isalpha()
    if abbreviated:
        if format_detected == "standard":
            format_detected = "abbreviated"
        notes.append("abbreviated first name — cannot expand")

    # --- Step 10: assign confidence ------------------------------------------
    if abbreviated:
        confidence = "low"
    elif format_detected in ("surname_first", "all_caps"):
        confidence = "medium" if notes else "high"
        # If the only transformation was reordering/casing, that's still clear
        confidence = "high"
    else:
        confidence = "high"

    if not notes:
        notes_str = ""
    else:
        notes_str = "; ".join(notes)

    return {
        "normalised": name,
        "abbreviated": abbreviated,
        "format_detected": format_detected,
        "confidence": confidence,
        "notes": notes_str,
    }


def names_match(name_a: str, name_b: str, threshold: float = 0.85) -> dict:
    """
    Determine whether two name strings refer to the same person.

    Returns a dict with keys:
      match            - bool
      confidence       - "high" | "medium" | "low"
      method           - "exact" | "fuzzy" | "abbreviated" | "no_match"
      similarity_score - raw rapidfuzz token_sort_ratio score (0.0–1.0)
      notes            - description of match logic applied
    """
    norm_a = normalise_director_name(name_a)
    norm_b = normalise_director_name(name_b)

    na = norm_a["normalised"]
    nb = norm_b["normalised"]

    either_abbreviated = norm_a["abbreviated"] or norm_b["abbreviated"]

    # --- Exact match after normalisation -------------------------------------
    if na == nb:
        notes_parts = []
        if norm_a["notes"]:
            notes_parts.append(norm_a["notes"])
        if norm_b["notes"] and norm_b["notes"] != norm_a["notes"]:
            notes_parts.append(norm_b["notes"])
        return {
            "match": True,
            "confidence": "high",
            "method": "exact",
            "similarity_score": 1.0,
            "notes": "; ".join(notes_parts) if notes_parts else "",
        }

    # --- Abbreviated first-name matching -------------------------------------
    # If one name has an abbreviated first name, check whether the initial
    # matches the first letter of the other name's first name.
    if either_abbreviated:
        tokens_a = na.split()
        tokens_b = nb.split()
        if tokens_a and tokens_b:
            initial_a = tokens_a[0]   # e.g. "j"
            initial_b = tokens_b[0]   # e.g. "jim"
            surname_a = " ".join(tokens_a[1:])
            surname_b = " ".join(tokens_b[1:])

            # Surnames must match (exact or near-exact) for abbreviated match
            surname_score = fuzz.token_sort_ratio(surname_a, surname_b) / 100.0

            if surname_score >= threshold:
                # Check initial matches first letter of first name
                if (len(initial_a) == 1 and initial_b.startswith(initial_a)) or \
                   (len(initial_b) == 1 and initial_a.startswith(initial_b)):
                    return {
                        "match": True,
                        "confidence": "medium",
                        "method": "abbreviated",
                        "similarity_score": round(surname_score, 4),
                        "notes": "first name abbreviated — initial matches",
                    }

        # Abbreviated but initial doesn't match or surnames differ → no match
        raw_score = fuzz.token_sort_ratio(na, nb) / 100.0
        return {
            "match": False,
            "confidence": "low",
            "method": "no_match",
            "similarity_score": round(raw_score, 4),
            "notes": "abbreviated first name — initial does not match or surnames differ",
        }

    # --- Fuzzy match (including hyphenated surname variant) ------------------
    score = fuzz.token_sort_ratio(na, nb) / 100.0

    if score >= threshold:
        return {
            "match": True,
            "confidence": "medium",
            "method": "fuzzy",
            "similarity_score": round(score, 4),
            "notes": f"fuzzy match score {score:.2f}",
        }

    # Hyphenated surname — try match with hyphens treated as spaces.
    # "Sarah Jones-Williams" and "Sarah Jones Williams" are the same person.
    if "-" in na or "-" in nb:
        na_h = re.sub(r"\s+", " ", na.replace("-", " ")).strip()
        nb_h = re.sub(r"\s+", " ", nb.replace("-", " ")).strip()
        score_h = fuzz.token_sort_ratio(na_h, nb_h) / 100.0
        if score_h >= threshold:
            return {
                "match": True,
                "confidence": "medium",
                "method": "fuzzy",
                "similarity_score": round(score_h, 4),
                "notes": "hyphen treated as space in double-barrelled surname",
            }

    # --- No match — set confidence based on nickname ambiguity ---------------
    # "Low" confidence: first names are known nickname variants of each other
    # (e.g. James/Jim) — flag for human review.
    # "High" confidence: no plausible nickname relationship — clearly different.
    tokens_a = na.split()
    tokens_b = nb.split()
    if tokens_a and tokens_b:
        fn_a, fn_b = tokens_a[0], tokens_b[0]
        if fn_b in _NICKNAME_LOOKUP.get(fn_a, set()) or \
           fn_a in _NICKNAME_LOOKUP.get(fn_b, set()):
            return {
                "match": False,
                "confidence": "low",
                "method": "no_match",
                "similarity_score": round(score, 4),
                "notes": f"possible nickname pair ({fn_a}/{fn_b}) — flag for review",
            }

    return {
        "match": False,
        "confidence": "high",
        "method": "no_match",
        "similarity_score": round(score, 4),
        "notes": f"score {score:.2f} below threshold {threshold}",
    }


def normalise_for_storage(raw_name: str) -> str:
    """
    Convenience wrapper for ingestion-time use.

    Returns the normalised string only. Logs a warning if confidence is
    not "high" so that data quality issues are visible in pipeline logs.
    """
    result = normalise_director_name(raw_name)
    if result["confidence"] != "high":
        print(
            f"[director_name] low-confidence normalisation: "
            f"'{raw_name}' → '{result['normalised']}' "
            f"(confidence={result['confidence']}, notes={result['notes']})"
        )
    return result["normalised"]
