"""Check that a stored job result can be read by the gateway image, which has no numpy."""

import pickle
from unittest import TestCase

import numpy as np

from surface_dynamics.service_state import plain_json

BUILTINS = (dict, list, str, int, float, bool, type(None))


def kinds(value):
    found = {type(value)}
    if isinstance(value, dict):
        for key, item in value.items():
            found |= {type(key)} | kinds(item)
    elif isinstance(value, list):
        for item in value:
            found |= kinds(item)
    return found


class PlainJsonTests(TestCase):
    def test_numpy_scalars_and_arrays_become_builtin_numbers_and_lists(self):
        result = {
            "median": np.float32(1.5),
            "count": np.int64(7),
            "flag": np.bool_(True),
            "bounds": np.array([1.0, 2.0]),
            "nested": [{"grid": np.zeros((2, 2))}],
        }

        plain = plain_json(result)

        self.assertEqual(plain["median"], 1.5)
        self.assertEqual(plain["count"], 7)
        self.assertIs(plain["flag"], True)
        self.assertEqual(plain["bounds"], [1.0, 2.0])
        self.assertEqual(plain["nested"], [{"grid": [[0.0, 0.0], [0.0, 0.0]]}])
        self.assertLessEqual(kinds(plain), set(BUILTINS))

    def test_the_stored_value_pickles_without_any_numpy_type(self):
        plain = plain_json({"x": np.float64(0.25), "series": np.arange(3)})

        self.assertNotIn(b"numpy", pickle.dumps(plain))

    def test_plain_values_pass_through_unchanged(self):
        value = {"status": "complete", "items": [1, 2.5, None, True], "text": "ok"}

        self.assertEqual(plain_json(value), value)

    def test_a_value_that_is_not_json_is_refused_instead_of_stored(self):
        with self.assertRaises(TypeError):
            plain_json({"x": object()})
