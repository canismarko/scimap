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

import datetime as dt
import unittest
from unittest.mock import MagicMock, Mock
import math
import os

import numpy as np
import pandas as pd
from matplotlib.colors import Normalize
import pytz

from tests.cases import HDFTestCase, ScimapTestCase
from utilities import xycoord, prog
from peakfitting import Peak
from txm.xanes_frameset import XanesFrameset, calculate_whiteline, fit_whiteline
from txm.frame import (
    average_frames, TXMFrame, xy_to_pixel, pixel_to_xy, Extent, Pixel,
    rebin_image, apply_reference, position)
from txm.edges import Edge, k_edges
from txm.importers import import_txm_framesets
from txm.xradia import XRMFile, decode_ssrl_params, decode_aps_params
from txm.beamlines import sector8_xanes_script, Zoneplate, ZoneplatePoint, Detector
from txm import xanes_frameset
from txm import plotter

testdir = os.path.join(os.path.dirname(__file__), 'testdata')
ssrldir = os.path.join(testdir, 'ssrl-txm-data')
apsdir = os.path.join(testdir, 'aps-txm-data')


class ApsScriptTest(unittest.TestCase):
    """Verify that a script is created for running an operando
    TXM experiment at APS beamline 8-BM-B."""

    def setUp(self):
        self.output_path = os.path.join(testdir, 'aps_script.txt')
        # Check to make sure the file doesn't already exists
        if os.path.exists(self.output_path):
            os.remove(self.output_path)
        assert not os.path.exists(self.output_path)
        # Values taken from APS beamtime on 2015-11-11
        self.zp = Zoneplate(
            start=ZoneplatePoint(z=3110.7, energy=8313),
            step=9.9329 / 2 # Original script assumed 2eV steps
        )
        self.det = Detector(
            start=ZoneplatePoint(z=389.8, energy=8313),
            step=0.387465 / 2 # Original script assumed 2eV steps
        )

    def tear_down(self):
        pass
    # os.remove(self.output_path)

    def test_file_created(self):
        with open(self.output_path, 'w') as f:
            sector8_xanes_script(dest=f, edge=k_edges["Ni"],
                                 zoneplate=self.zp, detector=self.det,
                                 names=["test_sample"], sample_positions=[])
        # Check that a file was created
        self.assertTrue(
            os.path.exists(self.output_path)
        )

    def test_binning(self):
        with open(self.output_path, 'w') as f:
            sector8_xanes_script(dest=f, edge=k_edges["Ni"],
                                 binning=2, zoneplate=self.zp,
                                 detector=self.det, names=[],
                                 sample_positions=[])
        with open(self.output_path, 'r') as f:
            firstline = f.readline().strip()
        self.assertEqual(firstline, "setbinning 2")

    def test_exposure(self):
        with open(self.output_path, 'w') as f:
            sector8_xanes_script(dest=f, edge=k_edges["Ni"],
                                 exposure=44, zoneplate=self.zp,
                                 detector=self.det, names=["test_sample"],
                                 sample_positions=[])
        with open(self.output_path, 'r') as f:
            f.readline()
            secondline = f.readline().strip()
        self.assertEqual(secondline, "setexp 44")

    def test_energy_approach(self):
        """This instrument can behave poorly unless the target energy is
        approached from underneath (apparently)."""
        with open(self.output_path, 'w') as f:
            sector8_xanes_script(dest=f, edge=k_edges['Ni'],
                                 zoneplate=self.zp, detector=self.det,
                                 names=[], sample_positions=[])
        with open(self.output_path, 'r') as f:
            lines = f.readlines()
        # Check that the first zone plate is properly set

    def test_first_frame(self):
        with open(self.output_path, 'w') as f:
            sector8_xanes_script(
                dest=f,
                edge=k_edges['Ni'],
                sample_positions=[position(x=1653, y=-1727, z=0)],
                zoneplate=self.zp,
                detector=self.det,
                names=["test_sample"],
            )
        with open(self.output_path, 'r') as f:
            lines = f.readlines()
        # Check that x, y are set
        self.assertEqual(lines[2].strip(), "moveto x 1653.00")
        self.assertEqual(lines[3].strip(), "moveto y -1727.00")
        self.assertEqual(lines[4].strip(), "moveto z 0.00")
        # Check that the energy approach lines are in tact
        self.assertEqual(lines[5].strip(), "moveto energy 8150.00")
        self.assertEqual(lines[54].strip(), "moveto energy 8248.00")
        # Check that energy is set
        self.assertEqual(lines[55].strip(), "moveto energy 8250.00")
        # Check that zone-plate and detector are set
        self.assertEqual(lines[56].strip(), "moveto zpz 2797.81")
        self.assertEqual(lines[57].strip(), "moveto detz 377.59")
        # Check that collect command is sent
        self.assertEqual(
            lines[58].strip(),
            "collect test_sample_xanes0_8250_0eV.xrm"
        )

    def test_second_location(self):
        with open(self.output_path, 'w') as f:
            sector8_xanes_script(
                dest=f,
                edge=k_edges['Ni'],
                sample_positions=[position(x=1653, y=-1727, z=0),
                                  position(x=1706.20, y=-1927.20, z=0)],
                zoneplate=self.zp,
                detector=self.det,
                names=["test_sample", "test_reference"],
            )
        with open(self.output_path, 'r') as f:
            lines = f.readlines()
        self.assertEqual(lines[247], "moveto x 1706.20\n")
        self.assertEqual(lines[248], "moveto y -1927.20\n")
        self.assertEqual(lines[250].strip(), "moveto energy 8150.00")

    def test_multiple_iterations(self):
        with open(self.output_path, 'w') as f:
            sector8_xanes_script(
                dest=f,
                edge=k_edges['Ni'],
                sample_positions=[position(x=1653, y=-1727, z=0),
                                  position(x=1706.20, y=-1927.20, z=0)],
                zoneplate=self.zp,
                detector=self.det,
                iterations=["ocv"] + ["{:02d}".format(soc) for soc in range(1, 10)],
                names=["test_sample", "test_reference"],
            )
        with open(self.output_path, 'r') as f:
            lines = f.readlines()
        self.assertEqual(
            lines[58].strip(),
            "collect test_sample_xanesocv_8250_0eV.xrm"
        )
        self.assertEqual(
            lines[1090].strip(),
            "collect test_sample_xanes02_8342_0eV.xrm"
        )


class ZoneplateTest(ScimapTestCase):
    def setUp(self):
        # Values taken from APS beamtime on 2015-11-11
        self.zp = Zoneplate(
            start=ZoneplatePoint(z=3110.7, energy=8313),
            step=9.9329 / 2 # Original script assumed 2eV steps
        )

    def test_constructor(self):
        with self.assertRaises(ValueError):
            # Either `step` or `end` must be passed
            Zoneplate(start=None)
        with self.assertRaises(ValueError):
            # Passing both step and end is confusing
            Zoneplate(start=None, step=1, end=1)
        # Check that step is set if not expicitely passed
        zp = Zoneplate(
            start=ZoneplatePoint(z=3110.7, energy=8313),
            end=ZoneplatePoint(z=3120.6329, energy=8315)
        )
        self.assertApproximatelyEqual(zp.step, 9.9329 / 2)

    def test_z_from_energy(self):
        result = self.zp.z_position(energy=8315)
        self.assertApproximatelyEqual(result, 3120.6329)


class XrayEdgeTest(unittest.TestCase):
    def setUp(self):
        self.edge = Edge(
            (8250, 8290, 20),
            (8290, 8295, 1),
            pre_edge=(8250, 8290),
            post_edge=(8290, 8295),
            map_range=(8291, 8293),
            name="Test edge",
        )
    def test_energies(self):
        self.assertEqual(
            self.edge.all_energies(),
            [8250, 8270, 8290, 8291, 8292, 8293, 8294, 8295]
        )

    def test_norm_energies(self):
        self.assertEqual(
            self.edge.energies_in_range(),
            [8291, 8292, 8293]
        )


# class TXMMapTest(HDFTestCase):

#     def setUp(self):
#         ret = super().setUp()
#         # Disable progress bars and notifications
#         prog.quiet = True
#         # Create an HDF Frameset for testing
#         self.fs = XanesFrameset(filename=self.hdf_filename,
#                                 groupname='mapping-test',
#                                 edge=k_edges['Ni'])
#         for i in range(0, 3):
#             frame = TXMFrame()
#             frame.energy = i + 8342
#             print(frame.energy)
#             frame.approximate_energy = frame.energy
#             ds = np.zeros(shape=(3, 3))
#             ds[:] = i + 1
#             frame.image_data = ds
#             self.fs.add_frame(frame)
#         self.fs[1].image_data.write_direct(np.array([
#             [0, 1, 4],
#             [1, 2.5, 1],
#             [4, 6, 0]
#         ]))
#         return ret

#     def test_max_energy(self):
#         expected = [
#             [8344, 8344, 8343],
#             [8344, 8344, 8344],
#             [8343, 8343, 8344]
#         ]
#         result = self.fs.whiteline_map()
#         print(result)
#         self.assertTrue(np.array_equal(result, expected))


class TXMMathTest(ScimapTestCase):
    """Holds tests for functions that perform base-level calculations."""

    def test_calculate_whiteline(self):
        absorbances = [700, 705, 703]
        energies = [50, 55, 60]
        data = pd.Series(absorbances, index=energies)
        out, goodness = calculate_whiteline(data)
        self.assertApproximatelyEqual(out, 57.33)
        # Test using multi-dimensional absorbances (eg. image frames)
        absorbances = [np.array([700, 700]),
                       np.array([705, 703]),
                       np.array([703, 707])]
        data = pd.Series(absorbances, index=energies)
        out, goodness = calculate_whiteline(data)
        self.assertApproximatelyEqual(out[0], 57.33)
        self.assertApproximatelyEqual(out[1], 57.98)

    def test_2d_whiteline(self):
        # Test using two-dimensional absorbances (ie. image frames)
        absorbances = [
            np.array([[502, 600],
                      [700, 800]]),
            np.array([[501, 601],
                      [702, 802]]),
            np.array([[500, 603],
                      [701, 801]]),
        ]
        energies = [50, 55, 60]
        data = pd.Series(absorbances, index=energies)
        out, goodness = calculate_whiteline(data)
        expected = [[50, 60],
                    [55, 55]]
        self.assertApproximatelyEqual(out[0][0], 52.25)
        self.assertApproximatelyEqual(out[0][1], 58.16)
        self.assertApproximatelyEqual(out[1][0], 57.27)
        self.assertApproximatelyEqual(out[1][1], 57.27)

    def test_fit_whiteline(self):
        filename = 'tests/testdata/NCA-cell2-soc1-fov1-xanesspectrum.tsv'
        data = pd.Series.from_csv(filename, sep="\t")
        data = data[8325:8360]
        peak, goodness = fit_whiteline(data, width=5)
        self.assertTrue(8352 < peak.center() < 8353,
                        "Center not within range {} eV".format(peak.center()))

        # Check that the residual differences are not too high
        # residuals = peak.residuals
        self.assertTrue(
            goodness < 0.01,
            "residuals too high: {}".format(goodness)
        )


class TXMImporterTest(unittest.TestCase):
    def setUp(self):
        self.hdf = os.path.join(ssrldir, 'testdata.hdf')
        # Disable progress bars
        prog.quiet = True

    def tearDown(self):
        if os.path.exists(self.hdf):
            os.remove(self.hdf)

    def test_import_timestamp(self):
        fs, = import_txm_framesets(ssrldir, hdf_filename=self.hdf, flavor='ssrl')
        frame = fs[0]
        self.assertEqual(
            frame.starttime,
            dt.datetime(2015, 2, 22, 10, 47, 19, tzinfo=pytz.timezone('US/Pacific'))
        )
        self.assertEqual(
            frame.endtime,
            dt.datetime(2015, 2, 22, 10, 47, 26, 500000, tzinfo=pytz.timezone('US/Pacific'))
        )
        # Verify that the frameset finds the full range of timestamps
        self.assertEqual(
            fs.starttime(),
            dt.datetime(2015, 2, 22, 10, 46, 31, tzinfo=pytz.timezone('US/Pacific'))
        )
        self.assertEqual(
            fs.endtime(),
            dt.datetime(2015, 2, 22, 10, 47, 26, 500000, tzinfo=pytz.timezone('US/Pacific'))
        )

class TXMFrameTest(HDFTestCase):

    def test_average_frames(self):
        # Define three frames for testing
        frame1 = TXMFrame()
        frame1.image_data = np.array([
            [1, 2, 3],
            [3, 4, 5],
            [2, 5, 7],
        ])
        frame2 = TXMFrame()
        frame2.image_data = np.array([
            [3, 5, 7],
            [7, 9, 11],
            [5, 11, 15],
        ])
        frame3 = TXMFrame()
        frame3.image_data = np.array([
            [7, 11, 15],
            [15, 19, 23],
            [11, 23, 31],
        ])
        avg_frame = average_frames(frame1, frame2, frame3)
        expected_array = np.array([
            [11/3, 18/3, 25/3],
            [25/3, 32/3, 39/3],
            [18/3, 39/3, 53/3],
        ])
        # Check that it returns an array with same shape
        self.assertEqual(
            frame1.image_data.shape,
            avg_frame.image_data.shape
        )
        # Check that the averaging is correct
        self.assertTrue(np.array_equal(avg_frame.image_data, expected_array))

    def test_params_from_aps(self):
        """Check that the new naming scheme is decoded properly."""
        ref_filename = "ref_xanesocv_8250_0eV.xrm"
        result = decode_aps_params(ref_filename)
        expected = {
            'sample_name': 'ocv',
            'position_name': 'ref',
            'is_background': True,
            'energy': 8250.0,
        }
        self.assertEqual(result, expected)

    def test_params_from_ssrl(self):
        # First a reference frame
        ref_filename = "rep01_000001_ref_201511202114_NCA_INSITU_OCV_FOV01_Ni_08250.0_eV_001of010.xrm"
        result = decode_ssrl_params(ref_filename)
        expected = {
            'date_string': '',
            'sample_name': 'NCA_INSITU_OCV_FOV01_Ni',
            'position_name': '',
            'is_background': True,
            'energy': 8250.0,
        }
        self.assertEqual(result, expected)
        # Now a sample field of view
        sample_filename = "rep01_201511202114_NCA_INSITU_OCV_FOV01_Ni_08250.0_eV_001of010.xrm"
        result = decode_ssrl_params(sample_filename)
        expected = {
            'date_string': '',
            'sample_name': 'NCA_INSITU_OCV_FOV01_Ni',
            'position_name': '',
            'is_background': False,
            'energy': 8250.0,
        }
        self.assertEqual(result, expected)

    def test_timestamp_from_xrm(self):
        sample_filename = "rep01_201502221044_NAT1050_Insitu03_p01_OCV_08250.0_eV_001of002.xrm"
        xrm = XRMFile(os.path.join(ssrldir, sample_filename), flavor="ssrl")
        # Check start time
        start = dt.datetime(2015, 2, 22,
                            10, 47, 19,
                            tzinfo=pytz.timezone('US/Pacific'))
        self.assertEqual(xrm.starttime(), start)
        # Check end time (offset determined by exposure time)
        end = dt.datetime(2015, 2, 22,
                          10, 47, 19, 500000,
                          tzinfo=pytz.timezone('US/Pacific'))
        self.assertEqual(xrm.endtime(), end)
        xrm.close()

        # Test APS frame
        sample_filename = "20151111_UIC_XANES00_sam01_8313.xrm"
        xrm = XRMFile(os.path.join(apsdir, sample_filename), flavor="aps-old1")
        # Check start time
        start = dt.datetime(2015, 11, 11, 15, 42, 38, tzinfo=pytz.timezone('US/Central'))
        self.assertEqual(xrm.starttime(), start)
        # Check end time (offset determined by exposure time)
        end = dt.datetime(2015, 11, 11, 15, 43, 16, tzinfo=pytz.timezone('US/Central'))
        self.assertEqual(xrm.endtime(), end)
        xrm.close()

    def test_xy_to_pixel(self):
        extent = Extent(
            left=-1000, right=-900,
            top=300, bottom=250
        )
        result = xy_to_pixel(
            xy=xycoord(x=-950, y=250),
            extent=extent,
            shape=(10, 10)
        )
        self.assertEqual(result, Pixel(vertical=10, horizontal=5))

    def test_pixel_to_xy(self):
        extent = Extent(
            left=-1000, right=-900,
            top=300, bottom=250
        )
        result = pixel_to_xy(
            pixel=Pixel(vertical=10, horizontal=5),
            extent=extent,
            shape=(10, 10)
        )
        self.assertEqual(result, xycoord(x=-950, y=250))

    def test_shift_data(self):
        frame = TXMFrame()
        frame.image_data = self.hdf_file.create_dataset(
            name = 'shifting_data',
            data = np.array([
                [1, 2],
                [5, 7]
            ])
        )
        # Shift in x direction
        frame.shift_data(1, 0)
        expected_data = [
            [2, 1],
            [7, 5]
        ]
        self.assertTrue(np.array_equal(frame.image_data, expected_data))
        # Shift in negative y direction
        frame.shift_data(0, -1)
        expected_data = [
            [7, 5],
            [2, 1]
        ]
        self.assertTrue(np.array_equal(frame.image_data, expected_data))

    def test_rebinning(self):
        frame = TXMFrame()
        original_data = np.array([
            [1., 1., 3., 3.],
            [2, 2, 5, 5],
            [5, 6, 7, 9],
            [8, 12, 11, 10],
        ])
        # Check that binning to same shape return original array
        result_data = rebin_image(original_data, shape=(4, 4))
        self.assertTrue(
            result_data is original_data
        )
        # Check for rebinning by shape
        result_data = rebin_image(original_data, shape=(2, 2))
        expected_data = np.array([
            [6, 16],
            [31, 37]
        ])
        self.assertTrue(np.array_equal(result_data, expected_data))
        # Check for rebinning by factor
        frame.image_data = self.hdf_file.create_dataset(
            name = 'rebinning_data_factor',
            chunks = True,
            data = original_data
        )
        frame.rebin(factor=2)
        self.assertTrue(np.array_equal(frame.image_data, expected_data))
        # Check for error with no arguments
        with self.assertRaises(ValueError):
            frame.rebin()
        # Check for error if trying to rebin to larger shapes
        with self.assertRaisesRegex(ValueError, 'larger than original shape'):
            frame.rebin(factor=0.5)
        with self.assertRaisesRegex(ValueError, 'larger than original shape'):
            frame.rebin(shape=(6, 6))

    def test_subtract_background(self):
        data = np.array([
            [10, 1],
            [0.1, 50]
        ])
        background = np.array([
            [100, 100],
            [100, 100]
        ])
        expected = np.array([
            [1, 2],
            [3, math.log10(2)]
        ])
        result = apply_reference(data, background)
        self.assertTrue(
            np.array_equal(result, expected)
        )
        # Check that uneven samples are rebinned
        data = np.array([
            [3, 1, 0.32, 0],
            [2, 4, 0, 0.68],
            [0.03, -.1, 22, 21],
            [0.07, 0.1, 0, 7],
        ])
        result = apply_reference(data, background)
        self.assertTrue(
            np.array_equal(result, expected)
        )

frameset_testdata = [
    np.array([
        [12, 8, 2.4, 0],
        [9, 11, 0, 1.6],
        [0.12, 0.08, 48, 50],
        [0.09, 0.11, 52, 50],
    ])
]

class MockDataset():
    def __init__(self, value=None):
        self.value = value

    @property
    def shape(self):
        return self.value.shape

class MockFrame(TXMFrame):
    image_data = MockDataset()
    hdf_filename = None
    def __init__(self, *args, **kwargs):
        pass


class MockFrameset(XanesFrameset):
    hdf_filename = None
    parent_groupname = None
    active_particle_idx = None
    edge = k_edges['Ni']
    def __init__(self, *args, **kwargs):
        pass

    def normalizer(self):
        return Normalize(0, 1)

    def __len__(self):
        return len(frameset_testdata)

    def __iter__(self):
        for d in frameset_testdata:
            frame = MockFrame()
            frame.image_data.value = d
            yield frame

# class TXMGtkViewerTest(unittest.TestCase):
#     @unittest.expectedFailure
#     def test_background_frame(self):
#         from txm import gtk_viewer
#         fs = MockFrameset()
#         viewer = gtk_viewer.GtkTxmViewer(frameset=fs,
#                                          plotter=plotter.DummyGtkPlotter(frameset=fs))

if __name__ == '__main__':
    unittest.main()
