# Private QX VLESS Node Health Manager

A private-repository-oriented GitHub Actions job that checks a fixed 50-node Quantumult X subscription every six hours, retains exactly ten slots for each of HK/TW/SG/US/DE, and publishes a complete replacement file only after the whole set is valid.

## Safety and behavior

- Schedule: `17 */6 * * *`; manual runs are also supported.
- Parses native QX `vless = ...` lines with `password`, `obfs=wss`, `obfs-host`, `obfs-uri`, and `tag`.
- Requires exactly slots 01–10 HK, 11–20 TW, 21–30 SG, 31–40 US, and 41–50 DE.
- Each node receives two attempts and passes if either succeeds. Up to eight nodes are checked concurrently.
- A failed node is retained after its first failed round. Its stable state key is lower-cased `entry endpoint|WebSocket path`. It is eligible for replacement after its second consecutive failed round. A success resets the counter.
- Manual `force_replace_failed=true` lowers the eligibility threshold for that invocation only; every replacement candidate must still pass its own probe.
- Candidate lists come from `wanwushequ/cfyxip/random-region/{HK,TW,SG,US,DE}/100.txt`. HK/TW/SG/DE lines must contain HKG/TPE/SIN/FRA respectively. US accepts any listed US source entry because the requested policy is “any US”. Existing endpoints are excluded.
- Generated paths are `/region-auto/{region}{index}`, for example `/region-auto/HK01`.
- The maintenance endpoint receives JSON `{add_txt, expected_sha256, report}` using `Authorization: Bearer ...`.
- If any slot cannot be filled, no maintenance POST occurs and the workflow fails. State/status are written locally by the process, but because the run exits nonzero the later workflow commit step does not execute.
- Console output contains aggregate counts and anonymous region/index only. It never intentionally logs subscription content, UUIDs, full node URIs, maintenance token, or candidate endpoint credentials.

## Required repository secrets

| Secret | Meaning |
|---|---|
| `SUBSCRIPTION_URL` | URL returning the current 50 native-QX node lines |
| `NODE_UUID` | UUID placed in VLESS requests and generated `ADD.txt` content |
| `MAINT_URL` | Protected Pages maintenance endpoint |
| `MAINT_TOKEN` | Bearer token accepted by that endpoint |

The repository may be public: node UUID, subscription URL, maintenance URL/token remain Actions Secrets. Only anonymous slot failure counters and aggregate status are committed. Full node URIs and generated `ADD.txt` exist only in workflow memory and are sent directly to the protected Pages endpoint. `GITHUB_TOKEN` is limited to `contents: write`.

## Probe details

The checker opens WSS to the node's entry endpoint while using `quiet-meadow-pages-qx.pages.dev` as TLS SNI and HTTP Host. It sends a VLESS v0 TCP request for `www.gstatic.com:443`. Inside the VLESS stream it performs a second, certificate-verifying TLS handshake using Python `ssl.MemoryBIO`, sends `HEAD /generate_204`, and requires HTTP status 204. Connect, I/O, total-probe, job, and workflow timeouts are bounded.

## Local development

Python 3.11+ is required.

```sh
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -v
```

A real local run requires all four environment variables:

```sh
cf-node-health
cf-node-health --force-replace-failed
```

Do not paste secrets into shell history. The repository never commits generated node lists.

## Files

- `.github/workflows/health.yml` — scheduled/manual workflow and anonymous-state-only changed commit.
- `src/cf_node_health/checker.py` — WSS, VLESS, MemoryBIO TLS, and HTTP 204 probe.
- `src/cf_node_health/parser.py` — strict QX parser and slot mapping.
- `src/cf_node_health/manager.py` — parallel checks, failure state, candidate replacement, rendering, POST.
- `src/cf_node_health/cli.py` — secret loading and fail-closed orchestration.
- `tests/` — mocked checker, candidate/network maintenance, parser, and protocol tests.

## Honest limitations

- GitHub-hosted runner routing, IPv4/IPv6 support, DNS, TLS interception, and Cloudflare reachability can differ materially from a phone's network. A runner failure does not prove the node fails on-device.
- Candidate source region/colo labels can drift, be stale, or change format. The manager validates the requested textual markers but cannot independently prove physical geography.
- A successful synthetic `www.gstatic.com` probe is narrow evidence, not a guarantee that every destination or Quantumult X behavior works.
- User-observed Quantumult X feedback supersedes this automated signal. Operators should restore/replace entries manually when device evidence conflicts with GitHub Actions.
- GitHub schedules are best-effort and can be delayed. Repository Actions availability and quotas also affect execution.
