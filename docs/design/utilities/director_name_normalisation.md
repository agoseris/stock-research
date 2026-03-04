# Utility: Director Name Normalisation

## Purpose

A shared utility for normalising and matching director names across 
data sources. Used by multiple components in the pipeline — called 
at ingestion time to normalise stored names, and at query time to 
match names across collections and APIs.

Director names in RNS filings are inconsistently formatted. Silent 
matching failures — where the same director is not recognised as the 
same person across two sources — cause incorrect transaction history 
lookups and missed corroboration signals. This utility must be built 
and tested independently before any Layer 2 tools are implemented.

---

## Problem Patterns Observed

Based on real PDMR filing examples:

```
Pattern                     Example                   Handling
──────────────────────────────────────────────────────────────────
Standard format             "Stephanie Coxon"         Straightforward
Middle initial present      "James S. Metcalf"        Strip initial
Surname-first format        "SMITH, John"             Reorder
Abbreviated first name      "J. Low"                  Flag — cannot expand
All caps surname            "COXON, Stephanie"        Normalise case
Honorific present           "Dr. Jane Smith"          Strip honorific
Double-barrelled surname    "Sarah Jones-Williams"    Preserve hyphen
Inconsistent spacing        "James  Low"              Normalise whitespace
Punctuation variants        "J.Low", "J Low"          Normalise
```

---

## Specification

### `normalise_director_name(raw_name: str) -> dict`

Normalises a raw director name string to a consistent canonical form.

```python
Input:
  raw_name:     string   ← name as it appears in source document

Returns:
  normalised:   string   ← canonical form:
                            - lowercase
                            - whitespace normalised (single spaces)
                            - punctuation stripped except hyphens
                            - surname-first format corrected to
                              firstname-surname order
                            - middle initials stripped
                            - honorifics stripped (Dr, Mr, Mrs, 
                              Ms, Prof, Sir, Dame, Lord, Lady)
  abbreviated:  boolean  ← true if first name is initial only
                            e.g. "J. Low" → abbreviated = true
                            matching against abbreviated names
                            must be flagged as lower confidence
  format_detected: string ← "standard" | "surname_first" | 
                              "abbreviated" | "all_caps" | "unknown"
  confidence:   string   ← "high": standard format, no ambiguity
                            "medium": format corrected but clear
                            "low": abbreviated or ambiguous
  notes:        string   ← describes transformations applied

Examples:
  "Stephanie Coxon"   → {normalised: "stephanie coxon", 
                          abbreviated: false, confidence: "high"}
  "James S. Metcalf"  → {normalised: "james metcalf",
                          abbreviated: false, confidence: "high",
                          notes: "middle initial stripped"}
  "SMITH, John"       → {normalised: "john smith",
                          abbreviated: false, confidence: "high",
                          notes: "surname-first format corrected"}
  "J. Low"            → {normalised: "j low",
                          abbreviated: true, confidence: "low",
                          notes: "abbreviated first name — 
                          cannot expand"}
  "Dr. Jane Smith"    → {normalised: "jane smith",
                          abbreviated: false, confidence: "high",
                          notes: "honorific stripped"}
```

---

### `names_match(name_a: str, name_b: str, threshold: float = 0.85) -> dict`

Determines whether two name strings refer to the same person.

```python
Input:
  name_a:       string   ← first name (raw or pre-normalised)
  name_b:       string   ← second name (raw or pre-normalised)
  threshold:    float    ← fuzzy match threshold, default 0.85

Returns:
  match:        boolean
  confidence:   string   ← "high" | "medium" | "low"
  method:       string   ← "exact" | "fuzzy" | "abbreviated" | 
                            "no_match"
  similarity_score: float ← raw rapidfuzz score (0.0 to 1.0)
  notes:        string

Examples:
  ("Stephanie Coxon", "COXON, Stephanie")
    → {match: true, confidence: "high", method: "exact"}

  ("James S. Metcalf", "James Metcalf")
    → {match: true, confidence: "high", method: "exact",
       notes: "middle initial stripped from first name"}

  ("J. Low", "Jim Low")
    → {match: true, confidence: "medium", method: "abbreviated",
       notes: "first name abbreviated — J matches Jim"}

  ("James Low", "Jim Low")
    → {match: false, confidence: "low", method: "fuzzy",
       notes: "James and Jim are common nicknames but fuzzy
       match score below threshold — flag for review"}
```

---

### `normalise_for_storage(raw_name: str) -> str`

Convenience wrapper for use at ingestion time. Returns the normalised 
string only — used when writing `director_name` to Firestore to 
ensure consistent storage format.

```python
Input:  raw_name: string
Output: normalised name string

Behaviour: calls normalise_director_name() and returns 
           result["normalised"]
           Logs confidence and notes if confidence != "high"
```

---

## Implementation Notes

**Library dependency: `rapidfuzz`**

Use `rapidfuzz` for fuzzy string matching.
```bash
pip install rapidfuzz --break-system-packages
```

Use `rapidfuzz.fuzz.token_sort_ratio` for name matching — this 
handles word order differences better than simple ratio matching.

**Honorifics list to strip:**
```python
HONORIFICS = [
    "dr", "mr", "mrs", "ms", "miss", "prof", "professor",
    "sir", "dame", "lord", "lady", "rev", "reverend",
    "captain", "capt", "major", "col", "colonel"
]
```

**Surname-first detection:**
```python
if "," in raw_name:
    parts = raw_name.split(",", 1)
    raw_name = f"{parts[1].strip()} {parts[0].strip()}"
```

**Middle initial detection and stripping:**
```python
import re
middle_initial_pattern = re.compile(r'\b[A-Z]\.\s+|\b[A-Z]\s+(?=[A-Z])')
```

---

## Where This Utility Is Used

```
Component                             Function called
────────────────────────────────────────────────────────────────
Classification + extraction prompt    normalise_for_storage()
  → stores normalised name in pdmr_transactions at write time

get_director_transaction_history      names_match()
  → matches query name against stored names

get_company_insider_activity          names_match()
  → deduplicates director names in results

get_director_companies_house_profile  names_match()
  → matches against Companies House person search results
```

---

## Testing Requirements

Before integrating into any Layer 2 tool, test against these cases:

```python
test_cases = [
    # (input_a, input_b, expected_match, expected_confidence)
    ("Stephanie Coxon", "Stephanie Coxon", True, "high"),
    ("Stephanie Coxon", "COXON, Stephanie", True, "high"),
    ("James S. Metcalf", "James Metcalf", True, "high"),
    ("J. Low", "Jim Low", True, "medium"),
    ("J. Low", "James Low", True, "medium"),
    ("James Low", "Jim Low", False, "low"),
    ("Kelly Baker", "Kelly Baker", True, "high"),
    ("Dr. Jane Smith", "Jane Smith", True, "high"),
    ("Sarah Jones-Williams", "Sarah Jones Williams", True, "medium"),
    ("John Smith", "Jane Smith", False, "high"),
]
```

All test cases must pass before the utility is used in production.

---

## File Location

```
utilities/
  director_name_normalisation.py
  tests/
    test_director_name_normalisation.py
```
