# Runbook — Path A: vast.ai GPU via SSH tunnel (RECOMMENDED FIRST)

Lab + backend + runner stay on YOUR PC. The rented GPU runs only Ollama,
reached through an SSH tunnel to `localhost:11434`. Zero code changes; results
are born locally in `data/experiments/` — nothing to sync, nothing to lose.
Time-to-think is ~93% of a rep's wall time, so this captures nearly the full
GPU speedup.

## One-time prep (before any rental)

1. Account at https://cloud.vast.ai + add credits (~$10 covers several evenings).
2. Add your SSH public key at https://cloud.vast.ai/manage-keys/
   (`cat ~/.ssh/id_ed25519.pub`; create with `ssh-keygen -t ed25519` if none).
3. CLI: `pip install --user vastai && vastai set api-key <KEY-from-manage-keys>`.

## Rent (48 GB-class, ~$0.6–1.1/hr)

```bash
# Zero-bandwidth-cost hosts only — pulling llama3.1:70b is ~43 GB of ingress.
vastai search offers 'gpu_ram>=48 inet_down_cost=0 inet_up_cost=0 reliability>0.99 rentable=true' -o dph
# Pick the cheapest sane offer (A6000 / A40 / 2x RTX 4090 all fine), then:
vastai create instance <OFFER_ID> --image vastai/base-image --disk 80 --ssh --direct
vastai show instances          # wait until running
vastai ssh-url <INSTANCE_ID>   # -> ssh://root@HOST:PORT
```

(Alternative: the "Ollama + WebUI" template from the web UI — also fine; the
tunnel makes its exposed port/token irrelevant.)

## Run everything with the MASTER SCRIPT (recommended)

After renting, only two manual steps remain — fill in the connection file,
then one command does provisioning + tunnel + run:

```bash
cd ~/thesis/networkLLMApp
cp scripts/vast-instance.env.example scripts/vast-instance.env   # fill HOST/PORT from `vastai ssh-url`

# ALWAYS calibrate first — "GPU = ~10x" is an assumption, not a measurement:
./experiment.sh --gpu experiments/s1-baseline-llama31-70b.yaml 1
# note the rep time, then plan the round (reps APPEND):
./experiment.sh --gpu experiments/s1-baseline-llama31-70b.yaml 5
./experiment.sh --gpu experiments/s1-baseline-qwen25-32b.yaml 5
```

`--gpu` automatically: installs Ollama on the instance + pulls the spec's
model (`scripts/vast-provision-gpu.sh`), stops the local Ollama service to
free port 11434 (asks for your sudo password), starts the auto-reconnect
tunnel in the background, waits until the remote model answers, then runs
with the RAM guard as usual. Nothing is done manually on the instance.

Model downloads run detached ON the instance with live progress in your
terminal (percent / GB / speed / ETA). A closed terminal, dropped SSH, or
sleeping laptop cannot kill a download — re-run the same command and it
re-attaches (interrupted downloads resume where they left off).

The demo app works the same way: `./app.sh --gpu` provisions + tunnels and
serves GUI chat from the rented GPU (default model llama3.1:8b; pass another
as `./app.sh --gpu qwen2.5-coder:32b` and pick it in the GUI's model list).

## GPU vs local — the mode-switch rules

Same scripts without `--gpu` = local mode (`experiment.sh`'s model always
comes from the spec; only `app.sh --gpu` takes an optional model). BUT:
going GPU → local is NOT automatic. A `--gpu` session leaves the local
Ollama stopped and the tunnel holding :11434 (on purpose — follow-up GPU
runs are instant). Before any local run:

```bash
kill $(cat /tmp/nllm-tunnel.pid)   # free :11434 from the tunnel
sudo systemctl start ollama        # laptop gets its local LLM back
```

Otherwise a "local" run silently uses the instance through the leftover
tunnel — or fails confusingly if the instance is already destroyed.
`./shutdown.sh` stops lab/backend/GUI only; it never touches the tunnel or
local Ollama. Local → GPU needs nothing: the `--gpu` scripts stop the local
Ollama themselves (the sudo prompt). Local mode only fits laptop-sized
models (7B/8B specs); 32b/70b specs are GPU-only.

<details>
<summary>Manual steps (what --gpu does under the hood / fallback)</summary>

```bash
scripts/vast-provision-gpu.sh llama3.1:70b   # install ollama + pull model on the instance
sudo systemctl stop ollama                   # free local :11434
scripts/vast-tunnel.sh                       # leave running (auto-reconnects)
curl -s localhost:11434/api/tags             # must list the model
./run-experiment.sh --check experiments/s1-baseline-llama31-70b.yaml
./run-experiment.sh experiments/s1-baseline-llama31-70b.yaml 1
```
</details>

Results land in `data/experiments/<spec-id>/` on your PC as always.
`--reps N` means N MORE reps, not top-up-to-N. If a run aborts, the partial
rep is orphaned (excluded from stats) — check `reps/` for `"complete": false`.

## Kill checklist (cost discipline)

1. Runs finished, plots regenerated (`.venv/bin/python -m app.experiments aggregate <spec>` if needed).
2. `vastai destroy instance <INSTANCE_ID>` — or `vastai stop instance <ID>` ONLY
   if you'll reuse it within days (storage keeps billing; models stay pulled).
3. `vastai show instances` → confirm gone/stopped.
4. Stop the tunnel: `kill $(cat /tmp/nllm-tunnel.pid)` (harmless if already dead).
5. `sudo systemctl start ollama` — restore local Ollama.

## Cost math

| Item | Estimate |
|---|---|
| 48 GB GPU on-demand | $0.6–1.1/hr |
| model pulls (one-time per instance) | ~30–60 min wall, $0 bandwidth if `inet_down_cost=0` |
| k=5 S1 round at ~10x speedup | minutes of compute |
| full evening incl. setup + calibration | **$3–8** |

## Resource guardrails (2026-08-05 near-crash)

A full lab bring-up once swap-thrashed the laptop (load avg 14, system nearly
froze). Protections now built in — nothing to do manually, but know they exist:

- `run-experiment.sh` **refuses to deploy** the lab with less than
  `MIN_FREE_MB` (default 6000) MB of free RAM — close apps and retry, or
  consciously override: `MIN_FREE_MB=4000 ./run-experiment.sh ...`
- A watchdog (`scripts/resource-guard.sh`) runs for the whole deploy+run and
  **auto-tears the stack down** if free RAM drops below `CRIT_MB` (default
  1200). Log: `/tmp/nllm-resource-guard.log`.
- **Emergency stop at any time: `./shutdown.sh`** — kills lab, backend, GUI,
  and verifies everything is gone.

## Troubleshooting

- `curl localhost:11434` fails → tunnel not up, or local Ollama grabbed the port
  (`ss -ltnp | grep 11434`).
- Tunnel keeps dropping → keep `vast-tunnel.sh` running (it reconnects); a rep
  that died mid-chat is orphaned but the run can simply be re-launched (reps append).
- Model missing in `/api/tags` → you pulled on the wrong side; models live on the INSTANCE.
