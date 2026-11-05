"""Fit v2 without opening either geographic test cube or their metrics."""
from pathlib import Path
import json,argparse
from surface_dynamics.temporal_v2 import train
ROOT=Path(__file__).resolve().parents[1]
recipe=json.loads((ROOT/'configs/global_v2.json').read_text())
parser=argparse.ArgumentParser();parser.add_argument('--output',default='products/experiments/global_v2_candidate');args=parser.parse_args()
output=Path(args.output)
if (output/'masked_temporal_transformer.pt').exists():raise FileExistsError('Choose a new experiment directory; existing checkpoints are preserved')
train(recipe,ROOT/'products/global_v2/cubes',output)
