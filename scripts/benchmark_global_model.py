"""Evaluate the existing frozen checkpoint on every internal date of holdouts."""
from pathlib import Path
import json
from surface_dynamics.benchmark import benchmark
ROOT=Path(__file__).resolve().parents[1]
result=benchmark([ROOT/'products/cubes'/f'{name}.npz' for name in ['amazon','murray']],
                 ROOT/'models/global_explorer_v1/masked_temporal_transformer.pt')
output=ROOT/'products/model_benchmark_all_dates.json'
output.write_text(json.dumps(result,indent=2,allow_nan=False))
print(json.dumps({c['case_id']:{'acquisitions':c['scored_acquisitions'],**c['pooled']} for c in result['cases']},indent=2))
