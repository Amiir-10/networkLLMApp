# Task Plan — Experiment Runner (2026-07-05)

Vault design doc: `01_Projects/Bachelors-Project/Notes/Experiment-Runner-Design-2026-07-05.md` (READ FIRST).
(Previous Demo-2 plan fully shipped — see git history + vault Demo-2 notes.)

## Status legend: [ ] todo · [~] in progress · [x] done · [!] blocked

## Done (2026-07-05, uncommitted)

- [x] `app/experiments/` module: specs / prober / replay (vendored prompt-replay pattern) / runner / metrics / stats / plots / `__main__` CLI
- [x] Backend: `POST /rules/flush`; `options` passthrough on `/chat` (`ChatRequest.options` → `call_ollama`)
- [x] `experiments/s1-baseline-llama31.yaml` (S1 = D A D U D, k=2, temp 0)
- [x] matplotlib pinned in requirements.txt; offline sanity pass green (synthetic 3-rep stats + 3 PNGs)
- [x] Lab redeployed fresh (stale central-hub crash-loop destroyed); backend relaunched detached with new code
- [x] S1 smoke launched; rep-1 verified perfect (block → exactly pc1a->pc1b unreachable, 1 DROP; undo → clean, 0 DROPs)

## Next

- [ ] Verify smoke completion (2 complete reps + aggregate.json + 3 plots); verify a rerun APPENDS rep-3+
- [ ] Commit everything on `main`
- [ ] S2/S5 spec YAMLs (vault Methodology-Brainstorm §2); S4/S6 need multi-block ground truth
- [ ] Real run: k=5 S1 on llama3.1:8b, then qwen2.5-coder:7b
- [ ] Phase 2: per-protocol probes (curl 80/443, UDP 9999), undo-fidelity / descriptive-accuracy / invalid-action-rate metrics, prompt-replay Replayer cross-model replay
