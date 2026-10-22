# Write preconditions and recovery

Normal execution uses `scripts/run_ingest_pipeline.ps1`; do not reproduce transaction mechanics in model reasoning.

Before passing `-SyncConfirmed`, verify:

1. the local host-bound profile identifies production Device A or B;
2. the other production device is not running a write task;
3. Nutstore sync is complete;
4. no sync-conflict file or anomaly is present.

The profile, plan, snapshot, staging, backup, journal, validator report, Python environment, and index working directory remain outside the Vault. The executor may create only `_ops/governance.lock.json` as a short-lived coordination file; it removes the lock on exit. An expired lock recovered with `-Resume` is backed up under the local transaction directory, never beside the Vault lock.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill_dir>/scripts/run_ingest_pipeline.ps1" `
  -Plan "<profile_work_root>/ingest_plan.json" `
  -SyncConfirmed
```

Use `-Resume` only for intentional recovery of an expired transaction. Device, hostname, VaultRoot, WorkRoot, Python, and governance runtime come from the local profile or the shared skill and cannot be overridden at transaction time. Executor failure is final for that attempt; inspect its external journal rather than simulating rollback manually.
