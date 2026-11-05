"""Choose independent optical references on date/coverage, never SAR agreement."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
from surface_dynamics.io import load_cube
from surface_dynamics.optical_water import prepare_reference_pair
ROOT=Path(__file__).resolve().parents[1]
recipe=json.loads((ROOT/'configs/flood_pilot.json').read_text())


def run(case):
    folder=ROOT/'products/flood_pilot/cases'/case['id'];cube=load_cube(folder/'cube.npz')
    before=[str(d) for d in cube['dates'] if case['baseline_start']<=str(d)<=case['baseline_end']]
    after=[str(d) for d in cube['dates'] if case['assessment_start']<=str(d)<=case['assessment_end']]
    result=prepare_reference_pair(cube,case['longitude'],case['latitude'],before[-1],after[0],folder/'reference_pair.npz',recipe['optical_max_offset_days'],recipe['optical_candidate_cap'],boundary_date=case['assessment_start'])
    (folder/'reference_admission.json').write_text(json.dumps(result,indent=2));print('OPTICAL_REFERENCE',case['id'],json.dumps(result),flush=True);return result


if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(run,recipe['cases']))
