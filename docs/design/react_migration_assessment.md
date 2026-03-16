# Migration Assessment: Streamlit → Node.js / React

**Date:** 11 Mar 2026
**Status:** Assessment only — no action planned
**Context:** The Streamlit PoC is fully operational. This documents what a future migration would involve if the need arises.

---

## Current State

The Streamlit interface serves its purpose well. Known minor inconveniences (full-page reruns on interaction, no real-time updates, CSS hacks for layout control) are acceptable for a PoC with a single user.

## What You'd Gain

- Incremental rendering (no full-page reruns)
- Real-time signal updates (Firestore listeners or MongoDB Change Streams)
- URL-based navigation with browser back/forward
- Full CSS/HTML control (no Streamlit layout workarounds)
- Mobile-friendly responsive layouts
- Optional: zero cloud dependency if paired with local MongoDB

## Architecture (Fully Local Option)

```
React SPA (Vite + TypeScript)     ← localhost:3000
    ↕ REST API
Node.js API (Express/Fastify)     ← localhost:4000
    ↕                ↕
MongoDB              Playwright
(localhost:27017)    (headless Chromium, same machine)
```

## Effort Summary

| Aspect | Effort | Notes |
|---|---|---|
| React views (Signals, Performance, Discovery) | Medium | Mechanical translation |
| Simple writes (dismiss, mute, position state) | Low | Same document ops |
| Config / Universe list | Low | Trivial CRUD |
| Universe CSV import | Medium | Multi-step wizard |
| Ingest tab | Medium | Complex state, but React handles it naturally |
| Playwright (Node.js port) | Medium | ~350 lines, near-identical Node.js API |
| Node.js API layer | Medium | ~15 endpoints |
| CSS/styling | Low | Existing CSS transfers verbatim |
| **Overall** | **~3-4 weeks** | For an experienced React developer |

## Key Findings

1. **Playwright complexity drops significantly when fully local** — no infrastructure split, no residential IP constraint, Playwright Node.js bindings are near-identical to Python.

2. **MongoDB is a natural fit** for the existing document model and eliminates several Firestore pain points (composite indexes, TTL console config, missing-field query bugs like the `dismissed == False` issue).

3. **The genuine hard question is where backend processes run.** The VM provides always-on autonomous pipeline execution. Options: (a) move everything local, (b) keep VM + bridge to local MongoDB, (c) keep Firestore as the shared state layer.

4. **The six-abstraction architecture pays off** — only `StorageProviderBase` and `UniverseStorageProviderBase` implementations need to change. All lens code, LLM code, classification code remains untouched.

5. **Pragmatic middle ground:** Keep Firestore as backend write target (VM stays simple), build React + Node.js frontend locally reading from Firestore via Firebase Admin SDK. MongoDB becomes optional for later.

## Recommendation

No action needed now. The Streamlit PoC is fully operational and serves its purpose. Revisit this assessment if: (a) multi-user access is needed, (b) mobile access becomes important, or (c) the Streamlit UX limitations become a genuine workflow bottleneck rather than minor inconveniences.
