"""Package only the reviewed source, measured analysis inputs and verified reports."""
import io
import json
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

from prepare_flood_evidence import ROOT, digest, read_json

COHORTS = ("flood_evidence_a", "flood_evidence_b")
NAME = "nisar-flood-evidence-2026-11-05.zip"


def inputs(root):
    names = ("protocol_freeze.json", "analysis_freeze.json", "admission.json", "index.json", "validation.json")
    files = {root / name for name in names}
    for entry in read_json(root / "index.json")["cases"]:
        path = root / entry["href"]
        assert path.resolve().is_relative_to(root.resolve()) and digest(path) == entry["sha256"]
        files.add(path)
        report = read_json(path)
        for name, checksum in report["admission"].get("input_sha256", {}).items():
            source = path.parent.parent / name
            assert source.resolve().parent == path.parent.parent.resolve() and digest(source) == checksum
            files.add(source)
        for item in report["artifacts"]:
            artifact = path.parent / item["href"]
            assert artifact.resolve().parent == path.parent.resolve() and digest(artifact) == item["sha256"]
            files.add(artifact)
        # These are the public CMR records used to admit or exclude the case.
        files.update((path.parent.parent / "catalogs").glob("*.json"))
        files.update((path.parent.parent).glob("metadata_screen.json"))
    return files


def main():
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=zip", source], cwd=ROOT)
    destination = ROOT / "deliverables" / NAME
    destination.parent.mkdir(exist_ok=True)
    files = set()
    for cohort in COHORTS:
        root = ROOT / "products" / cohort
        assert read_json(root / "validation.json")["status"] == "share_with_caveats"
        files.update(inputs(root))
    records = [{"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p)}
               for p in sorted(files)]
    with ZipFile(destination, "w", ZIP_DEFLATED, compresslevel=6) as target, ZipFile(io.BytesIO(archive)) as tracked:
        for name in tracked.namelist():
            if not name.endswith("/"):
                target.writestr(name, tracked.read(name))
        for item in records:
            target.write(ROOT / item["path"], item["path"])
        target.writestr("BUNDLE_MANIFEST.json", json.dumps({"source_commit": source, "analysis_files": records}, indent=2))
        target.writestr("START_HERE.md", f"""# Flood evidence in new regions

Source commit `{source}`. Open `docs/FLOOD_EVIDENCE.md` for the findings and contract. Each cohort's `index.json` lists report and map locations. Open a report's `evidence.png` to compare measured histories and optical transitions.

Install the source using the repository README. The measured inputs support an offline rerun:

```text
python scripts/report_flood_evidence.py
python scripts/verify_flood_evidence.py
python scripts/report_flood_evidence.py --recipe configs/flood_evidence_followup.json --output products/flood_evidence_b
python scripts/verify_flood_evidence.py --recipe configs/flood_evidence_followup.json --output products/flood_evidence_b
```

`BUNDLE_MANIFEST.json` records measured-input and report checksums. The separate checksum verifies the full ZIP.
""")
    checksum = digest(destination)
    destination.with_suffix(".zip.sha256").write_text(f"{checksum}  {NAME}\n", encoding="utf-8")
    print(json.dumps({"source_commit": source, "file": NAME, "bytes": destination.stat().st_size,
                      "sha256": checksum, "analysis_files": len(records)}))


if __name__ == "__main__":
    main()
