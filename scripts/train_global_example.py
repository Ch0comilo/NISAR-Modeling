from pathlib import Path
from surface_dynamics.transformer import train
ROOT=Path(__file__).resolve().parents[1]
training=[ROOT/'products/cubes'/(x+'.npz') for x in ['forest_river','deliblato','chenab','niscemi','okavango']]
heldout=[ROOT/'products/cubes'/(x+'.npz') for x in ['amazon','murray']]
train(training,heldout,ROOT/'models/global_explorer_v1',epochs=10)
