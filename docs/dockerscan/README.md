# dockerscan (patched) — how this binary was built

The stock `cr0hn/dockerscan` binary does NOT work for our lab scans. Two bugs:

1. **Non-root extraction fails** — `archive.Unpack` used `fchownat` (root-only);
   under the non-root/systemd backend it hit EPERM and extracted 0 packages, so
   the CVE matcher saw nothing ("CVE database not available"). Fixed upstream
   with `NoLchown: true`.
2. **Gzip-suffix bug (still in upstream)** — `processLayer` only gunzipped layer
   blobs whose filename ends `.gz`. Docker's containerd image store names blobs
   `blobs/sha256/<digest>` (gzip, NO suffix) → "invalid tar header" → 0 packages.
   Fixed by sniffing the gzip magic bytes (`1f 8b`) — see
   `dockerscan-gzip-sniff.patch`.

## Rebuild
```
git clone https://github.com/cr0hn/dockerscan.git && cd dockerscan
git checkout 08625c50c92a699caf0ca30177bba609511d60cc   # base this binary was built on
git apply /path/to/dockerscan-gzip-sniff.patch
CGO_ENABLED=0 go build -ldflags="-s -w" -o dockerscan ./cmd/dockerscan   # static, portable
```

## CVE database
`cve-db.sqlite` here is the full NVD DB (`dockerscan update-db`, ~139k CVEs, 198MB)
PRUNED to only the packages the lab images actually ship (openssl, musl, busybox,
nginx, curl, ...): 353 CVEs, ~1MB. Rich enough that weblab-vuln shows 17 CRITICALs
incl. CVE-2024-5535. Regenerate the prune with the script in the session note.

Backend needs `HOME` set (it reads `~/.dockerscan/cve-db.sqlite`); the systemd unit
sets it, and `app/scanner.py` also derives HOME from the binary path defensively.
