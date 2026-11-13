"""InSAR network topology, observability, geometry and baseline validity gates."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from surface_dynamics.network import build_network, plan_network
from surface_dynamics.insar import validate_pair, analyze_pair, NoCoherentSupport, fetch_pair, wrapped_products
from surface_dynamics.explore import parse_request
from surface_dynamics.exploration_insar import discover_pairs, measure_pair, parse_pair_request
from surface_dynamics.benchmark import interpolation_baseline
from surface_dynamics.engine import analyze_insar

ROOT=Path(__file__).resolve().parents[1]


def entry(a,b,track='024',revision='001'):
    title=f'NISAR_L2_PR_GUNW_026_{track}_A_018_028_4000_SH_{a}T202900_{a}T202934_{b}T202858_{b}T202933_P05023_N_F_J_{revision}'
    return {'title':title,'time_start':f'{a[:4]}-{a[4:6]}-{a[6:]}T20:29:00Z','links':[]}


def pair():
    phase=np.arange(16,dtype=float).reshape(4,4)
    data={'wrapped_complex':np.exp(1j*phase),'wrapped_coherence':np.full((4,4),.6),'unwrapped_coherence':np.full((4,4),.6),'unwrappedPhase':phase,'connectedComponents':np.ones((4,4),dtype='uint16'),'wrapped_mask':np.full((4,4),11,dtype='uint32'),'unwrapped_mask':np.full((4,4),11,dtype='uint32'),'center_frequency_hz':1239000000.}
    for prefix in ['wrapped','unwrapped']:
        data[prefix+'_x']=500000+np.arange(4)*20.
        data[prefix+'_y']=5300000-np.arange(4)*20.
        data[prefix+'_epsg']=32614
    return data


class InSARExtensionTests(unittest.TestCase):
    def test_triangle_and_disconnected_pass_are_not_inverted_together(self):
        entries=[entry('20260101','20260113'),entry('20260113','20260125'),entry('20260101','20260125'),entry('20260101','20260113',track='125')]
        networks=build_network(entries,event_date='2026-01-10')
        self.assertEqual(len(networks),2)
        graph=next(n for n in networks if len(n['edges'])==3)
        self.assertEqual(graph['cycle_rank'],1);self.assertEqual(len(graph['minimum_temporal_baseline_forest']),2)
        self.assertEqual(len(graph['independent_closure_cycles'][0]['edge_ids']),3)
        self.assertEqual(graph['time_series_inversion'],'not_performed')

    def test_latest_revision_is_not_a_spurious_parallel_edge(self):
        graph=build_network([entry('20260101','20260113'),entry('20260101','20260113',revision='002')])[0]
        self.assertEqual(len(graph['edges']),1);self.assertEqual(graph['cycle_rank'],0)
        self.assertTrue(graph['edges'][0]['id'].endswith('002'))

    def test_baseline_exclusion_preserves_disconnected_acquisition_nodes(self):
        graph=build_network([entry('20260101','20260113'),entry('20260113','20260425')],max_baseline_days=24)[0]
        self.assertEqual(graph['connected_component_count'],2)
        self.assertEqual(graph['pairs_excluded_by_baseline'],1)

    def test_network_worker_validates_and_returns_honest_empty_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'catalog.json';path.write_text(json.dumps({'entries':[],'hits':0}))
            result=plan_network({'contract_version':'1.0','request_id':'empty-network','source':{'mode':'local_catalog','catalog_path':str(path)}},Path(folder)/'out')
            self.assertEqual(result['status'],'no_data');self.assertEqual(result['networks'],[])

    def test_pair_rejects_wrong_grid_frequency_and_coherence(self):
        original=pair();validate_pair(original)
        for key,bad in [('center_frequency_hz',np.nan),('unwrapped_x',np.arange(3)),('unwrapped_epsg',4326),('unwrapped_coherence',np.full((4,4),1.5))]:
            with self.subTest(key=key):
                value={**original,key:bad}
                with self.assertRaises(ValueError):validate_pair(value)

    def test_threshold_sweep_separates_filled_ionosphere_from_support(self):
        value=pair();value['unwrapped_mask'][0]|=1<<24
        result=analyze_pair(value,.35);q=result['metadata']['observability']
        self.assertEqual(q['selected_component_ionosphere_filled_fraction'],.25)
        rows=q['threshold_sensitivity'];fractions=[r['valid_fraction'] for r in rows]
        self.assertEqual(fractions,sorted(fractions,reverse=True))
        self.assertEqual(rows[-1]['valid_fraction'],0)
        with self.assertRaises(NoCoherentSupport):analyze_pair(value,.9)

    def test_wrapped_preview_retains_coherence_without_phase_or_unwrapped_grid(self):
        value = {key: field for key, field in pair().items()
                 if key.startswith("wrapped_") or key == "center_frequency_hz"}
        value["wrapped_complex"][0, 0] = 0
        value["wrapped_mask"][0, 1] = 255
        coherence, phase = wrapped_products(value, .35)
        self.assertEqual(int(np.isfinite(coherence).sum()), 15)
        self.assertEqual(int(np.isfinite(phase).sum()), 14)
        self.assertAlmostEqual(coherence[0, 0], .6)
        strict_coherence, strict_phase = wrapped_products(value, .9)
        np.testing.assert_allclose(strict_coherence, coherence)
        self.assertTrue(np.isnan(strict_phase).all())

    def test_earthquake_lens_preserves_coherence_and_maps_missing_phase(self):
        import rasterio
        from surface_dynamics.service_workers import live_layers

        data = pair()
        data["wrapped_mask"][0, 0] = 255
        data["wrapped_complex"][0, 1] = 0
        data["wrapped_coherence"][0, 2] = .1
        row = entry("20260101", "20260113")
        query = parse_pair_request({"longitude": -99, "latitude": 48, "start_date": "2026-01-01",
                                    "end_date": "2026-01-13", "pair_id": row["title"]})
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            path = out / "chip.npz"
            np.savez(path, **data)
            path.with_suffix(".json").write_text(json.dumps({"polarization": "HH"}))
            with patch("surface_dynamics.exploration_insar.fetch_pair", return_value=path):
                result = measure_pair(query, row, path, out)
            lens = result["lens"]
            self.assertEqual(lens["intervals"][0]["interval_days"], 12)
            support = lens["support"]
            self.assertEqual(sum(support["coherence_histogram_cells"]), 15)
            self.assertEqual(support["coherence_at_or_above_threshold_cells"], 14)
            self.assertEqual(support["wrapped_phase_cells"], 13)
            self.assertIsNone(lens["los_displacement_cm"])
            layers = live_layers(out, "/v2/jobs/test/layers", "insar-pair", lens)
            self.assertIn("display_wrapped_phase_support", [layer["id"] for layer in layers])
            with rasterio.open(out / "display_wrapped_phase_support.tif") as dataset:
                np.testing.assert_array_equal(dataset.read(1)[0], [np.nan, 0, 0, 1])

    def test_inventory_requires_both_dates_and_deduplicates_revisions(self):
        point = parse_request(dict(longitude=0, latitude=0, start_date="2026-01-01", end_date="2026-01-13"))
        rows = [entry("20260101", "20260125"), entry("20260101", "20260113"),
                entry("20260101", "20260113", revision="002")]
        with patch("surface_dynamics.exploration_insar.search",
                   return_value={"entries": rows, "hits": 3, "queried_at_utc": "test"}):
            inventory, eligible = discover_pairs(point)
        self.assertEqual(len(inventory["pairs"]), 1)
        self.assertEqual(inventory["pairs"][0]["id"], rows[-1]["title"])
        self.assertEqual(inventory["pairs"][0]["temporal_baseline_days"], 12)
        self.assertEqual(list(eligible), [rows[-1]["title"]])
        self.assertEqual(inventory["pixel_support"], "unchecked")

    def test_unobservable_pair_worker_returns_no_data(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'pair.npz';np.savez(path,**pair())
            path.with_suffix('.json').write_text(json.dumps({'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
            result=analyze_insar({'contract_version':'1.0','request_id':'no-support','source':{'mode':'local_pair','pair_path':str(path)},'coherence_threshold':.9},Path(folder)/'out')
            self.assertEqual(result['status'],'no_data');self.assertIsNone(result['los_displacement_cm'])

    def test_cache_reuse_requires_same_source_location_and_extent(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'pair.npz';np.savez(path,**pair())
            e=entry('20260101','20260113');e['links']=[{'href':'https://nisar.asf.earthdatacloud.nasa.gov/NISAR/example.h5'}]
            path.with_suffix('.json').write_text(json.dumps({'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'title':e['title'],'longitude':0,'latitude':0,'extent_km':20,'crop_policy':'point_centered'}))
            with patch('surface_dynamics.insar.RangeReader',side_effect=RuntimeError('network read requested')):
                with self.assertRaisesRegex(RuntimeError,'network read requested'):fetch_pair(e,1,0,path,token='test-in-memory')

    def test_actual_time_interpolation_skips_missing_immediate_neighbor(self):
        values=np.array([[[0.],[100.],[50.],[10.]]]);present=np.ones_like(values,dtype=bool);present[:,1]=False
        pred,ok=interpolation_baseline(values,present,np.array([0.,1.,5.,10.]),2)
        self.assertTrue(ok.item());self.assertAlmostEqual(pred.item(),5.)
        present[:,0]=False
        pred,ok=interpolation_baseline(values,present,np.array([0.,1.,5.,10.]),2)
        self.assertFalse(ok.item());self.assertTrue(np.isnan(pred).all())


if __name__=='__main__':unittest.main()
