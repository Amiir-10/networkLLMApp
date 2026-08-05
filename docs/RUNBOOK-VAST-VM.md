# Runbook — Path B: everything on a vast.ai VM instance

The FULL stack (Docker, containerlab, backend, runner, Ollama) runs on a
rented vast.ai **VM instance**. Your PC can be off during runs; the sync timer
pulls results home every 10 minutes (supervisor's results-safety requirement).

Use this path for long unattended rounds. For a first GPU session prefer
Path A (RUNBOOK-VAST-TUNNEL.md) — simpler, and VM supply on vast.ai is thin.

## Why a VM instance (not a normal one)

Standard vast.ai instances ARE Docker containers and **cannot run Docker
inside** (no docker-in-docker) — containerlab is impossible there. VM
instances provide a full Linux with systemd + Docker support. Search them with
`vms_enabled=true`. If no acceptable VM offers exist, fall back to Path A.

## One-time prep

1. **Add your SSH key at https://cloud.vast.ai/manage-keys/ BEFORE creating the
   VM.** A VM created without your key is PERMANENTLY inaccessible — keys
   cannot be added to a running VM.
2. Private GitHub remote pushed and reachable (the VM clones from it).
3. `pip install --user vastai && vastai set api-key <KEY>`.

## Rent

```bash
vastai search offers 'vms_enabled=true gpu_ram>=48 inet_down_cost=0 inet_up_cost=0 reliability>0.99 rentable=true' -o dph
# Disk is FIXED at creation and 70b alone is 43 GB — take 100:
vastai create instance <OFFER_ID> --disk 100 --ssh --direct
vastai ssh-url <INSTANCE_ID>     # -> ssh://root@HOST:PORT
```

## Bootstrap (on the VM)

```bash
ssh -p <PORT> root@<HOST>        # lands in tmux — STAY in tmux for runs
git clone https://<user>:<token>@github.com/<user>/networkLLMApp.git
cd networkLLMApp
scripts/vast-vm-bootstrap.sh     # setup.sh --with-ollama llama3.1:70b qwen2.5-coder:32b
                                 # + healthcheck --smoke (deploys+tears down central-hub)
```

The bootstrap is idempotent — re-run it after any failure.

## Sync timer (on your PC, before long runs)

```bash
cd ~/thesis/networkLLMApp
cp scripts/vast-instance.env.example scripts/vast-instance.env   # fill HOST/PORT/REMOTE_REPO_DIR
scripts/sync-results.sh                    # manual first pull — verify it works
scripts/install-sync-timer.sh              # then every 10 min automatically
systemctl --user list-timers | grep vast-sync
```

Results are pulled to `data/experiments-remote/<date>/` — NEVER merged into
local `data/experiments/`. **One spec id runs on exactly ONE host, ever**
(rep numbering is filesystem-derived; two hosts under one id = collisions).

## Run (on the VM, inside tmux)

```bash
./run-experiment.sh experiments/s1-baseline-llama31-70b.yaml 1   # calibration rep FIRST
./run-experiment.sh experiments/s1-baseline-llama31-70b.yaml 5
./run-experiment.sh experiments/s1-baseline-qwen25-32b.yaml 5
```

If SSH drops, reattach: `ssh -p <PORT> root@<HOST>` then `tmux attach`.

## End-of-experiment checklist (ORDER MATTERS)

1. On the VM: runs finished (`ls data/experiments/*/reps/`).
2. On PC: `scripts/sync-results.sh --final` → must print **MATCH**.
3. Only after MATCH: `vastai destroy instance <INSTANCE_ID>`.
4. `vastai show instances` → confirm destroyed (billing stopped).
5. `scripts/install-sync-timer.sh --uninstall`.
6. Aggregate locally if needed: copy `data/experiments-remote/<date>/<spec-id>`
   into a scratch checkout's `data/experiments/` and run
   `.venv/bin/python -m app.experiments aggregate <spec>` (works from disk, no lab).

## Resource guardrails

Same protections as local (see RUNBOOK-VAST-TUNNEL.md §guardrails):
`run-experiment.sh` refuses to deploy below `MIN_FREE_MB` free RAM and arms
`scripts/resource-guard.sh` (auto-teardown below `CRIT_MB`). On a dedicated
VM you can lower the bar (`MIN_FREE_MB=3000`) since nothing else competes for
RAM — but keep the guard armed: an OOM'd VM you can't SSH into is unrecoverable
short of destroying it. Emergency stop: `./shutdown.sh`.

## Troubleshooting

- No VM offers at sane prices → Path A.
- `git clone` fails → token expired / repo private without token in URL.
- Lab smoke fails on Docker networking → re-run bootstrap; if persistent,
  the host may restrict nested networking — destroy, pick another host.
- Sync timer failing → `journalctl --user -u vast-sync.service -n 20`; usually
  a stale `vast-instance.env` after re-renting.
