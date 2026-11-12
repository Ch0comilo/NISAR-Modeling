# Modal runbook

## Stage and verify a case bundle

Build the prepared case directory with `python scripts/prepare_api_cases.py --output <directory>` after restoring release fixtures. Use a separate staging app and job Dict. Upload the generated directory to the test Volume under a new `cases-...` name, then set `NISAR_CASES_PATH` to that name in the deploying shell. Defaults continue to read `/cases`. A versioned directory lets a later deployment switch the case set while keeping older files available.

Deploy a clean commit and compare `/health` and `/v2/capabilities` to the local Git SHA and normalized build fingerprint. The fingerprint covers shipped Python, schemas, model assets, pinned dependencies and the science explorer HTML. CRLF and LF text normalize to the same hash. Verify prepared PNG/COG assets, a measured job and its cache replay on staging before promotion.

## Verify and rotate the Earthdata secret

The worker expects EARTHDATA_TOKEN in a Modal Secret. The gateway uses public catalog reads. The project default points to the existing nisar-explorer-preview secret in main. Use a new Secret name if the old one is shared. Enter the new value in Modal's Secret UI, set NISAR_EARTHDATA_SECRET to that resource name in the deployment shell and redeploy. Run a bounded measured job, check its science_status and revoke the old token after verification. Keep token values out of Git, job JSON, terminal command arguments and logs. The local DPAPI provider is Windows-only.

## Check usage and limits

    python -m modal billing summary
    python -m modal app list
    python -m modal container list
    python -m modal app logs nisar-evidence-api --env main

The public gateway has one container with a 0.25 physical-core CPU ceiling and 512 MiB memory ceiling. The worker has one CPU container and no GPU. One job can execute and one can queue. The service admits at most 12 new jobs per UTC day and five per client IP per day. A worker runs for at most 240 seconds. General traffic has a per-IP request limit of 120/minute. Published assets are bounded per file and the service allows up to 1 GiB of artifact transfer per UTC day. Limit state is in Modal Dict except for the gateway's minute-level IP counters. The cache is checked before new-work quotas. CORS governs browser origin and the service is public.

At list rates, a four-minute worker at 1 core and 2 GiB is approximately $0.0042 in reserved-resource compute. Even 12 fully timed-out jobs/day over a 31-day month would be about $1.57 on that model. A continuously active gateway at the stated CPU and RAM ceilings for 31 days would be about $11.75. These are conservative compute-only estimates before credits. Startup, image builds, idle windows, actual metering and other workspace workloads affect the bill. The Workspace usage limit is the outer billing control. Review it in Usage & Billing. [Pricing](https://modal.com/pricing) · [Budgets](https://modal.com/docs/guide/budgets)

The hosted mock at `/mock` uses the same gateway, queue and quotas as the API. The gateway and worker scale to zero between requests, with configured idle windows of 5 and 10 seconds. An idle deployment uses no compute after its containers stop. Page views, catalog reads, job polling and artifact requests can start the gateway. Storage and network egress are metered separately against the workspace allowances. [Modal autoscaling](https://modal.com/docs/guide/scale) · [Egress billing](https://modal.com/docs/guide/network-egress-billing)

The job record store is shared by gateway and workers. Deploy with one gateway and avoid overlapping deployments that share a state Dict while high traffic is admitted. Modal Dict entries expire after seven days without access. Completed results are eligible for a 24-hour cache hit. Artifacts and the chip cache remain in Volume storage until the operator cleans them up. Check usage periodically and retire old job and chip data during a maintenance window.

## Clear cache

Pause the app before removing live files or deleting the state Dict. Check the exact resources and keep the curated cases prefix.

    python -m modal volume ls --env main nisar-evidence-v2 /
    python -m modal dict list --env main
    python -m modal app stop --env main nisar-evidence-api
    python -m modal volume rm --help
    python -m modal dict delete --help

To clear stale scientific work, remove only /chips and /jobs from the configured Volume after confirming their names and delete the configured job-state Dict. Keep /cases. Deploy again and check the case index. Use a separate NISAR_APP_NAME, NISAR_STATE_DICT and NISAR_VOLUME when running a concurrent development service.

## Failure triage

Use the job_id in app logs to correlate sanitized worker failures and the measured duration. HTTP 429 means a rate, delivery or job budget limit. HTTP 503 means queue capacity or scheduling failure. HTTP 502/504 on coverage means a catalog error. Check product entitlement and token expiry in Earthdata if archive reads fail. A coverage hit leaves quantitative support unchecked. A succeeded job can return a valid no_data result. Keep that distinction in operations and UI copy.
