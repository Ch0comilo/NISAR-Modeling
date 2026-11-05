# Data storage and Git history

The science repository publishes code, schemas, model metadata/compact weights, selected scientific previews and small result summaries. Its tracked-file budget is **10 MiB total**, with no individual file above 10 MiB. `scripts/repository_audit.py` enforces the policy in CI.

Generated data belongs in a versioned release asset or an external scientific data store. The current `v0.3.0` bundle contains thirteen common-posting regional cubes, legacy fixtures, two real three-pair InSAR windows, provenance, the wheel and offline handoff. `data/example_bundle.json` pins its URL, size and SHA-256. `scripts/download_examples.py` checks the archive before restoring **35 curated input files**, verifies all inputs before writing, and preserves different existing user files. In the extracted portable bundle, a separate bundled-mode manifest verifies its already included inputs without network access. The earlier `v0.2.0` release remains available with its original fixtures.

Keep these local directories outside Git: `products`, `scratch`, `arrays`, `figures`, `descriptive`, `research`, `deliverables`, virtual environments and build outputs. The user-owned `mockup` directory is excluded from publication and remains unchanged on disk. Earthdata/GitHub credentials and protected credential files never belong in a release or commit.

## Initial upload repair

The original, unpublished first commit tracked approximately **1.15 GB** of working-tree files, including generated arrays and the unrelated mockup. The remote repository was empty when repaired. Its original commit is retained locally under `refs/backup/pre-lean-publish`; files remain on disk. A new lightweight root history is published to `main`, so the large original objects are not ancestors of the uploaded branch. No published history is force-rewritten.

Push only the intended branch:

```powershell
git push origin main
```

Avoid `git push --mirror` or `git push --all` when maintaining local backup refs. Adding an ignore rule only changes future tracking; it does not reduce old commits. If large data is already published in the future, coordinate a separate history migration with collaborators before rewriting shared history.

The local `.git` directory can remain large because it deliberately retains the recoverable original commit. A fresh GitHub clone contains only the lightweight published history. Do not delete original measurements or Git backup refs merely to shrink the published repository.
