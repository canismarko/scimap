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
# along with Foobar.  If not, see <http://www.gnu.org/licenses/>.

"""Helper functions for setting up and displaying plots using matplotlib."""

from matplotlib import pyplot
from matplotlib.ticker import ScalarFormatter


class ElectronVoltFormatter(ScalarFormatter):
    """Matplotlib formatter for showing energies as electon-volts."""
    def __call__(self, *args, **kwargs):
        formatted_value = super().__call__(*args, **kwargs)
        formatted_value = "{value} eV".format(value=formatted_value)
        return formatted_value


class DegreeFormatter(ScalarFormatter):
    """Matplotlib formatter for showing angles with the degree symbol."""
    def __call__(self, *args, **kwargs):
        formatted_value = super().__call__(*args, **kwargs)
        formatted_value = "{value}°".format(value=formatted_value)
        return formatted_value


def remove_extra_spines(ax):
    """Removes the right and top borders from the axes."""
    ax.spines['right'].set_visible(False)
    ax.spines['top'].set_visible(False)
    ax.yaxis.set_ticks_position('left')
    ax.xaxis.set_ticks_position('bottom')
    return ax


def set_outside_ticks(ax):
    """Convert all the axes so that the ticks are on the outside and don't
    obscure data."""
    ax.get_yaxis().set_tick_params(which='both', direction='out')
    ax.get_xaxis().set_tick_params(which='both', direction='out')
    return ax


def new_axes(height=5, width=None):
    """Create a new set of matplotlib axes for plotting. Height in inches."""
    # Adjust width to accomodate colorbar
    if width is None:
        width = height / 0.8
    fig = pyplot.figure(figsize=(width, height))
    # Set background to be transparent
    fig.patch.set_alpha(0)
    # Create axes
    ax = pyplot.gca()
    # Remove borders
    remove_extra_spines(ax)
    return ax


def new_image_axes(height=5, width=5):
    """Square axes with ticks on the outside."""
    ax = new_axes(height, width)
    return set_outside_ticks(ax)


def big_axes():
    """Return a new Axes object, but larger than the default."""
    return new_axes(height=9, width=16)


def xrd_axes():
    """Return a new Axes object, with a size appropriate for display x-ray
    diffraction data."""
    return new_axes(width=8)


def dual_axes(orientation='horizontal'):
    """Two new axes for mapping, side-by-side."""
    if orientation == 'vertical':
        fig, (ax1, ax2) = pyplot.subplots(2, 1)
        fig.set_figwidth(6.9)
        fig.set_figheight(13.8)
    else:
        fig, (ax1, ax2) = pyplot.subplots(1, 2)
        fig.set_figwidth(13.8)
        fig.set_figheight(5)
    # Remove redundant borders
    remove_extra_spines(ax1)
    remove_extra_spines(ax2)
    return (ax1, ax2)


def plot_scans(scan_list, step_size=0, ax=None, names=[]):
    """Plot a series of XRDScans as a waterfall. step_size controls the
    spacing between the waterfall stacking. Optional keyword arg 'ax'
    plots on a specific Axes.
    """
    if ax is None:
        ax = big_axes()
    scannames = []
    lines = []
    for idx, scan in enumerate(scan_list):
        df = scan.diffractogram.copy()
        df.counts = df.counts + step_size * idx
        lines.append(ax.plot(df.index, df.counts)[0])
        # Try and determine a name for this scan
        try:
            scannames.append(names[idx])
        except IndexError:
            scannames.append(getattr(scan, 'name', "Pattern {}".format(idx)))
    ax.legend(reversed(lines), reversed(scannames))
    # Set axes limits
    df = scan_list[0].diffractogram
    xMax = max([scan.diffractogram.index.max() for scan in scan_list])
    xMin = min([scan.diffractogram.index.min() for scan in scan_list])
    ax.set_xlim(left=xMin, right=xMax)
    # Decorate
    ax.set_xlabel(r'$2\theta$')
    ax.set_ylabel('counts')
    return ax


def plot_txm_intermediates(images):
    """Accept a dictionary of images and plots them each on its own
    axes. This is a complement to routines that operate on a
    microscopy frame and optionally return all the intermediate
    calculated frames.
    """
    for key in images.keys():
        ax1, ax2 = dual_axes()
        ax1.imshow(images[key], cmap='gray')
        ax1.set_title(key)
        ax2.hist(images[key].flat, bins=100)
