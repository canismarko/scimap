# -*- coding: utf-8 -*-
#
# Copyright © 2016 Mark Wolf
#
# This file is part of scimap.
#
# Scimap is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Scimap is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Scimap.  If not, see <http://www.gnu.org/licenses/>.

# flake8: noqa

import unittest
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import matplotlib.pyplot as plt
import numpy as np

from scimap import XRDScan, standards
from scimap.peakfitting import remove_peak_from_df
from scimap.native_refinement import NativeRefinement, contains_peak, peak_area

TESTDIR = os.path.join(os.path.dirname(__file__), "test-data-xrd")
COR_BRML = os.path.join(TESTDIR, 'corundum.brml')

class NativeRefinementTest(unittest.TestCase):

    def test_fit_background(self):
        corundum = standards.Corundum()
        # Get sample data from Mew XRD
        scan = XRDScan(filename=COR_BRML,
                       phase=corundum)
        df = scan.diffractogram
        q = df.index
        I = df.counts
        # Remove expected XRD peaks
        for reflection in corundum.reflection_list:
            q, I = remove_peak_from_df(x=q, y=I, xrange=reflection.qrange)
            # Do the background fitting
        refinement = NativeRefinement(phases=[corundum])
        bg = refinement.refine_background(q, I)
        # plt.plot(q, I)
        # plt.plot(q, bg)
        # plt.show()
        
        # Spot-check some values on the resulting background
        new_bg = refinement.background(df.index)
        # plt.plot(df.counts.values)
        # plt.plot(new_bg)
        # plt.show()
        self.assertAlmostEqual(new_bg[2465], 141.6, places=1)
        self.assertAlmostEqual(new_bg[3270], 116.8, places=1)
        self.assertAlmostEqual(new_bg[4650], 129.4, places=1)
        self.assertAlmostEqual(new_bg[6565], 123.5, places=1)

    def test_phase_ratios(self):
        corundum = standards.Corundum()
        # Get sample data from Mew XRD
        scan = XRDScan(filename=COR_BRML,
                       phases=[corundum, corundum])
        df = scan.diffractogram
        q = df.index
        I = df.counts
        # Remove expected XRD peaks
        for reflection in corundum.reflection_list:
            q, I = remove_peak_from_df(x=q, y=I, xrange=reflection.qrange)
        # Do the background fitting
        refinement = NativeRefinement(phases=[corundum, corundum])
        q = df.index
        bg = refinement.refine_background(df.index, df.counts.values)
        subtracted = df.counts.values - bg

        # Do phase fraction refinement
        result = refinement.refine_phase_fractions(q, subtracted)
        np.testing.assert_equal(result, [0.5, 0.5])

    def test_net_area(self):
        scan = XRDScan(filename=COR_BRML,
                       phases=[])
        df = scan.diffractogram
        q = df.index
        I = df.counts.values
        # Pick an arbitrary q range
        idx1 = 2400; idx2 = 2500
        qrange = (q[idx1], q[idx2])
        # Find expected area (idx2+1 to make it inclusive)
        expected = np.trapz(x=q[idx1:idx2+1], y=I[idx1:idx2+1])
        # Calculate the net area and compare
        area = peak_area(q, I, qrange=qrange)
        self.assertEqual(area, expected)
        # Choose something outside the qrange (should be zero)
        qrange = (0, 0.5)
        area = peak_area(q, I, qrange=qrange)
        self.assertEqual(area, 0)

    def test_contains_peak(self):
        scan = XRDScan(filename=COR_BRML,
                       phases=[])
        df = scan.diffractogram
        q = df.index
        I = df.counts
        # Check for an existent peak
        self.assertTrue(contains_peak(q, (1, 2)))
        # Check for a nonexistent peak
        self.assertFalse(contains_peak(q, (8, 9)))
        # Check for a partially existent peak
        self.assertTrue(contains_peak(q, (0, 1)))

if __name__ == '__main__':
    unittest.main()
