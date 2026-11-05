from pathlib import Path
import json
from surface_dynamics.dynamic_view import fit_dynamic_projection
ROOT=Path(__file__).resolve().parents[1]
metadata=fit_dynamic_projection(ROOT/'products/global_v2/cubes',ROOT/'models/global_explorer_v2')
(ROOT/'models/global_explorer_v2/dynamic_view.json').write_text(json.dumps(metadata,indent=2))
print(json.dumps(metadata))
