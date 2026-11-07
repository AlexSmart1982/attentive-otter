import unittest

import cv2
import numpy as np

from alignment import align_translation
from measure_large_sample import projection


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(53)
        self.image = cv2.resize(rng.integers(0, 256, (32, 24, 3), dtype=np.uint8), (384, 512))

    def test_known_subpixel_translation_is_recovered(self):
        b = cv2.warpAffine(self.image, np.array([[1, 0, 1.5], [0, 1, -.75]], dtype=np.float32),
                           (384, 512), borderMode=cv2.BORDER_REFLECT_101)
        _, result = align_translation(self.image, b)
        self.assertAlmostEqual(result["dx"], 1.5, delta=.15)
        self.assertAlmostEqual(result["dy"], -.75, delta=.15)
        self.assertGreater(result["rmse_reduction"], .8)

    def test_local_change_is_not_removed_by_global_translation(self):
        b = self.image.copy()
        b[200:232, 150:182] = 255-b[200:232, 150:182]
        _, result = align_translation(self.image, b)
        self.assertGreater(result["after"]["rmse"], .005)
        self.assertGreater(result["after"]["top_1_percent_energy_share"], .7)

    def test_candidate_projections_are_distance_lower_bounds(self):
        rng = np.random.default_rng(17)
        a, b = rng.random((2, 36))
        reference = np.sqrt(np.mean((a-b)**2))
        for method in ("rgb36", "gray12", "color3", "combined15"):
            projected = projection(np.stack([a, b]), method)
            self.assertLessEqual(np.linalg.norm(projected[0]-projected[1]), reference+1e-12)


if __name__ == "__main__":
    unittest.main()
