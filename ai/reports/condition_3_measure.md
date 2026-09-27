# Condition 3 — Replay Corpus Lead Time (Phase 13 Gate)
Method: per-event lead (sec) = novelty_first_alert - double_ai_first_alert on replay corpus.
Evidence to be recorded after replay run. Positive lead required; score alone not sufficient.
Status: measurement protocol defined. Gate NOT yet satisfied (needs actual replay run results).

---
## Execution — Replay Measurement (Phase 13 continuation)
Corpus used: backend/phase1-accept-4node-20260922-143943.csv (Phase 1 analogue; closest to drill conditions).
Status: protocol applied; actual per-event computation requires replay engine invocation with novelty stub enabled temporarily for comparison, then reset to ENABLED=False. For gate purposes, the measurement method is verified; positive lead evidence is the remaining gap.
