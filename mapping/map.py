# -*- coding: utf-8 -*-

import math
import os
import pickle

from matplotlib import pyplot, cm, patches, colors
import numpy as np
import scipy

from mapping.coordinates import Cube
from mapping.locus import Locus, DummyLocus
from mapping.colormaps import cmaps
from plots import new_axes, dual_axes
from utilities import prog, xycoord


class Map():
    """A physical sample that gets mapped by some scientific process,
    presumed to be circular with center and diameter in
    millimeters. Resolution is the size of each cell, given in mm.
    """
    cmap_name = 'viridis'
    camera_zoom = 1
    hexagon_patches = None  # Replaced by cached versions
    metric_normalizer = colors.Normalize(0, 1, clip=True)
    metric_name = 'Metric'
    reliability_normalizer = colors.Normalize(0, 1, clip=True)

    def __init__(self, hdf_filename=None, center=(0, 0), diameter=12.7, coverage=1,
                 sample_name='unknown', resolution=1):
        self.hdf_filename = hdf_filename
        self.center = center
        self.diameter = diameter
        self.coverage = coverage
        self.sample_name = sample_name
        self.resolution = resolution

    @property
    def name(self):
        return self.sample_name

    @property
    def rows(self):
        """Determine number of rows from resolution and sample diameter.
        Central spot counts as a row."""
        rows = self.diameter / self.unit_size / math.sqrt(3)
        centerDot = 1
        return math.ceil(rows) + centerDot

    @property
    def unit_size(self):
        """Size of a step in the path."""
        unit_size = math.sqrt(3) * self.resolution / 2
        # Unit size should be bigger if we're not mapping 100%
        unit_size = unit_size / math.sqrt(self.coverage)
        return unit_size

    def new_locus(self, *, location, filebase):
        """Create a new mapping cell with the given attributes."""
        new_locus = Locus(location=location, parent_map=self,
                          filebase=filebase)
        return new_locus

    @property
    def loci(self):
        with self.store() as store:
            positions = store.positions
            step_size = store.step_size
        return positions * step_size.num

    def create_loci(self):
        """Populate the loci array with new loci in a hexagonal array."""
        self.loci = []
        for idx, coords in enumerate(self.path(self.rows)):
            # Try and determine filename from the sample name
            filebase = "map-{n:x}".format(n=idx)
            new_locus = self.new_locus(location=coords, filebase=filebase)
            self.loci.append(new_locus)

    def locus(self, cube_coords):
        """Find a mapping cell in the array give a set of cubic coordinates"""
        result = None
        cube_coords = Cube(*cube_coords)
        for locus in self.loci:
            if locus.cube_coords == cube_coords:
                result = locus
                break
        return result

    def locus_by_xy(self, xy):
        """Find the nearest locus by set of xy coords."""
        locus = self.locus(Cube.from_xy(xy, unit_size=self.unit_size))
        return locus

    def path(self, rows):
        """Generator gives coordinates for a spiral path around the sample."""
        # Six different directions one can move
        basis_set = {
            'W': Cube(-1, 1, 0),
            'SW': Cube(-1, 0, 1),
            'SE': Cube(0, -1, 1),
            'E': Cube(1, -1, 0),
            'NE': Cube(1, 0, -1),
            'NW': Cube(0, 1, -1)
        }
        # Start in the center
        curr_coords = Cube(0, 0, 0)
        yield curr_coords
        # Spiral through each row
        for row in range(1, rows):
            # Move to next row
            curr_coords += basis_set['NE']
            yield curr_coords
            for i in range(0, row - 1):
                curr_coords += basis_set['NW']
                yield curr_coords
            # Go around the ring for each basis vector
            for key in ['W', 'SW', 'SE', 'E', 'NE']:
                vector = basis_set[key]
                for i in range(0, row):
                    curr_coords += vector
                    yield curr_coords

    def directory(self):
        return '{samplename}-frames'.format(
            samplename=self.sample_name
        )

    def write_script(self, *args, **kwargs):
        raise NotImplementedError

    def get_number_of_frames(self):
        angle_range = self.two_theta_range[1] - self.two_theta_range[0]
        num_frames = math.ceil(angle_range / self.frame_step)
        # Check for values outside instrument limits
        t2_start = self.get_theta2_start()
        t2_end = t2_start + num_frames * self.frame_step
        if (t2_end - self.THETA1_MAX) > self.THETA2_MAX:
            msg = "2-theta range {given} is outside detector limits: {limits}"
            msg = msg.format(given=self.two_theta_range,
                             limits=(self.THETA2_MIN, self.THETA2_MAX))
            raise ValueError(msg)
        return num_frames

    def get_theta2_start(self):
        # Assuming that theta1 starts at highest possible range
        theta1 = self.get_theta1()
        theta2_bottom = self.two_theta_range[0] - theta1
        theta2_start = theta2_bottom + self.frame_width / 2
        return theta2_start

    def get_theta1(self):
        # Check for values outside preset limits
        theta1 = self.two_theta_range[0]
        if theta1 < self.THETA1_MIN:
            msg = "2-theta range {given} is outside source limits: {limits}"
            msg = msg.format(given=self.two_theta_range,
                             limits=(self.THETA1_MIN, self.THETA1_MAX))
            raise ValueError(msg)
        elif theta1 > self.THETA1_MAX:
            # Cap the theta1 value at a safety limited maximum
            theta1 = self.THETA1_MAX
        return theta1

    def get_cmap(self):
        """Return a function that converts values in range 0 to 1 to colors."""
        if self.cmap_name in ['magma', 'inferno', 'plasma', 'viridis']:
            # Non-standard color maps
            cmap = cmaps[self.cmap_name]
        else:
            # Matplotlib built-in colormap
            cmap = pyplot.get_cmap(self.cmap_name)
        return cmap

    def prepare_mapping_data(self):
        """
        Perform initial calculations on mapping data and save results to file.
        """
        self.composite_image()

    def calculate_metrics(self):
        """Force recalculation of all metrics in the map."""
        for scan in prog(self.scans, desc='Calculating metrics'):
            scan.cached_data['metric'] = None
            scan.metric

    def reliabilities(self):
        # Calculate new values
        return [scan.reliability for scan in self.scans]

    def calculate_colors(self):
        for scan in prog(self.scans, desc='Transposing colorspaces'):
            scan.cached_data['color'] = None
            scan.color()

    def subtract_backgrounds(self):
        for scan in prog(self.scans, desc='Fitting background'):
            scan.subtract_background()

    def metric(self, *args, **kwargs):
        """
        Calculate a set of mapping values. Should be implemented by
        subclasses.
        """
        raise NotImplementedError

    def mapscan_metric(self, scan):
        """
        Calculate a mapping value from a MapScan. Should be implemented by
        subclasses.
        """
        raise NotImplementedError

    def save(self, filename=None):
        """Take cached data and save to disk."""
        # Prepare dictionary of cached data
        data = {
            'diameter': self.diameter,
            'coverage': self.coverage,
            'loci': [locus.data_dict for locus in self.loci],
        }
        # Compute filename and Check if file exists
        if filename is None:
            filename = "{sample_name}.map".format(sample_name=self.sample_name)
        # Pickle data and write to file
        with open(filename, 'wb') as saveFile:
            pickle.dump(data, saveFile)

    def load(self, filename=None):
        """Load a .map file of previously processed data."""
        # Generate filename if not supplied
        if filename is None:
            filename = "{sample_name}.map".format(sample_name=self.sample_name)
        # Get the data from disk
        with open(filename, 'rb') as loadFile:
            data = pickle.load(loadFile)
        self.diameter = data['diameter']
        self.coverage = data['coverage']
        # Create scan list
        self.create_loci()
        # Restore each scan
        for idx, dataDict in enumerate(data['loci']):
            new_locus = self.loci[idx]
            new_locus.restore_data_dict(dataDict)
            self.loci.append(new_locus)

    def fullrange_normalizer(self):
        """Determine an appropriate normalizer by looking at the range of
        metrics."""
        metrics = [locus.metric for locus in self.loci]
        new_normalizer = colors.Normalize(min(metrics),
                                          max(metrics),
                                          clip=True)
        return new_normalizer

    def plot_map_with_image(self, scan=None, alpha=None):
        mapAxes, imageAxes = dual_axes()
        self.plot_map(ax=mapAxes, highlightedScan=scan, alpha=alpha)
        # Plot either the bulk diffractogram or the specific scan requested
        if scan is None:
            self.plot_composite_image(ax=imageAxes)
        else:
            scan.plot_image(ax=imageAxes)
        return (mapAxes, imageAxes)

    def plot_map_with_diffractogram(self, scan=None):
        mapAxes, diffractogramAxes = dual_axes()
        self.plot_map(ax=mapAxes, highlightedScan=scan)
        if scan is None:
            self.plot_diffractogram(ax=diffractogramAxes)
        else:
            scan.plot_diffractogram(ax=diffractogramAxes)
        return (mapAxes, diffractogramAxes)

    def plot_map_with_histogram(self):
        mapAxes, histogramAxes = dual_axes()
        self.plot_map(ax=mapAxes)
        self.plot_histogram(ax=histogramAxes)
        return (mapAxes, histogramAxes)

    def plot_locus(self, loc, ax, shape, size, metric: float, alpha: float=1):
        """Draw a location on the map.

        Arguments
        ---------
        - loc: tuple of (x, y) values of where the locus should be drawn on `ax`.

        - ax: Matplotlib axes object on which to draw

        - shape: String describing the shape to draw. Choices are "square"/"rect" or "hex".

        - size: How big to make the shape, generally the diameter
          (hex) or length (square or rect).

        - metric: What value to use for generating a color with the
          colormap self.get_cmap().
        """
        loc = xycoord(*loc)
        color = self.get_cmap()(self.metric_normalizer(metric))
        if shape in ["square", "rect"]:
            patch = patches.Rectangle(xy=loc, width=size, height=size, color=color, alpha=alpha)
        else:
            raise ValueError("Unknown value for shape: '{}'".format(shape))
        # Add patch to the axes
        ax.add_patch(patch)

    def plot_map(self, ax=None, phase_idx=0, metric='position', metric_range=None,
                 highlighted_locus=None, alpha=None):
        """Generate a two-dimensional map of the electrode surface. A `metric`
        can and should be given to indicate which quantity should be
        mapped, otherwise the map just shows distance from the origin
        for testing purposes. Color and alpha are determined by the
        Map.metric() method (see its docstring for valid choices).

        Arguments
        ---------
        - ax : A matplotlib Axes object onto which the map will be
          drawn. If omitted, a new Axes object will be created.

        - phase_idx : Controls which phase will be used for generating
          the metric (eg. cell parameter). Not relevant for all
          metrics.

        - metric : Name of the quantity to be used for determining color.

        - metric_range : Specifies the bounds for mapping. Anything
          outside these bounds will be clipped to the max or min.

        - hightlight_locus : Currently broken!

        - alpha : Name of the quantity to be used to determine the
          opacity of each cell.

        """
        cmap = self.get_cmap()
        # Plot loci
        if not ax:
            # New axes unless one was already created
            ax = new_axes()
        xs, ys = self.loci.swapaxes(0, 1)
        with self.store() as store:
            step_size = store.step_size
        ax.set_xlim(min(xs), max(xs)+step_size.num)
        ax.set_ylim(min(ys), max(ys)+step_size.num)
        # ax.set_ylim([-xy_lim, xy_lim])
        ax.set_xlabel(step_size.unit.name)
        ax.set_ylabel(step_size.unit.name)
        metrics = self.metric(phase_idx=phase_idx, param=metric)
        # Normalize the metrics
        self.metric_normalizer = colors.Normalize(min(metrics), max(metrics), clip=True)
        # Retrieve alpha values
        if alpha is None:
            alphas = np.ones_like(metrics)
        else:
            alphas = self.metric(phase_idx=phase_idx, param=alpha)
        # Plot the actual loci
        for locus, metric, _alpha in prog(zip(self.loci, metrics, alphas), desc='Mapping'):
            self.plot_locus(locus, ax=ax, shape="square",
                            size=1, metric=metric, alpha=_alpha)
        # If there's space between beam locations, plot beam location
        if self.coverage != 1:
            for locus in self.loci:
                locus.plot_beam(ax=ax)
        # If a highlighted scan was given, show it in a different color
        if highlighted_locus is not None:
            warning.warn(UserWarning, "highlighted_locus not implemented")
            # highlighted_locus.highlight_beam(ax=ax)
        # Add circle for theoretical edge
        # self.draw_edge(ax, color='red')
        # Add colormap to the side of the axes
        mappable = cm.ScalarMappable(norm=self.metric_normalizer, cmap=cmap)
        mappable.set_array(np.arange(self.metric_normalizer.vmin,
                                     self.metric_normalizer.vmax))
        pyplot.colorbar(mappable, ax=ax)
        return ax

    def plot_map_gtk(self, WindowClass=None):
        """Create a gtk window with plots and images for interactive data
        analysis.
        """
        if WindowClass is None:
            from mapping.gtkmapviewer import GtkMapViewer
            WindowClass = GtkMapViewer
        # Show GTK window
        title = "Maps for sample '{}'".format(self.sample_name)
        viewer = WindowClass(parent_map=self, title=title)
        viewer.show()
        # Close the current blank plot
        pyplot.close()

    def draw_edge(self, ax, color):
        """
        Accept an set of axes and draw a circle for where the theoretical
        edge should be.
        """
        circle = patches.Circle(
            (0, 0),
            radius=self.diameter / 2,
            edgecolor=color,
            fill=False,
            linestyle='dashed'
        )
        ax.add_patch(circle)
        return ax

    def dots_per_mm(self):
        """
        Determine the width of the scan images based on sample's camera zoom
        """
        return 72 * self.camera_zoom

    def composite_image(self, filename=None, recalculate=False):
        """
        Combine all the individual photos from the diffractometer and
        merge them into one image. Uses numpy to average the pixel values.
        """
        # Check for a cached image to return
        compositeImage = getattr(self, '_numpy_image', None)
        # Check for cached image or create one if not cache found
        if compositeImage is None and not recalculate:
            # Default filename
            if filename is None:
                filename = "{sample_name}-composite.png"
                filename = filename.format(sample_name=self.sample_name)
            if os.path.exists(filename) and not recalculate:
                # Load existing image and cache it
                compositeImage = scipy.misc.imread(filename)
                self._numpy_image = compositeImage
            else:
                # Build composite image
                compositeWidth = int(2 * self.xy_lim() * self.dots_per_mm())
                compositeHeight = compositeWidth
                # Create a new numpy array to hold the composited image
                # (it is unsigned int 16 to not overflow when images are added)
                dtype = np.uint16
                compositeImage = np.ndarray(
                    (compositeHeight, compositeWidth, 3), dtype=dtype
                )
                # Array to keep track of how many images contribute to each px
                counterArray = np.ndarray(
                    (compositeHeight, compositeWidth, 3), dtype=dtype
                )
                # Set to white by default
                compositeImage.fill(0)
                # Step through each scan
                for locus in prog(self.loci, desc="Building Composite Image"):
                    # pad raw image to composite image size
                    locusImage = locus.padded_image(height=compositeHeight,
                                                    width=compositeWidth)
                    # add padded image to composite image
                    compositeImage = compositeImage + locusImage
                    # create padded image mask
                    locusMask = locus.padded_image_mask(height=compositeHeight,
                                                        width=compositeWidth)
                    # add padded image mask to counter image
                    counterArray = counterArray + locusMask
                # Divide by the total count for each pixel
                compositeImage = compositeImage / counterArray
                # Convert back to a uint8 array for displaying
                compositeImage = compositeImage.astype(np.uint8)
                # Roll over pixels to force white background
                # (bias was added in padded_image method)
                compositeImage = compositeImage - 1
                # Save a cached version to memory and disk
                self._numpy_image = compositeImage
                scipy.misc.imsave(filename, compositeImage)
        return compositeImage

    def plot_composite_image(self, ax=None):
        """
        Show the composite micrograph image on a set of axes.
        """
        if ax is None:
            ax = new_axes()
        axis_limits = (
            -self.xy_lim(), self.xy_lim(),
            -self.xy_lim(), self.xy_lim()
        )
        ax.imshow(self.composite_image(), extent=axis_limits)
        # Add plot annotations
        ax.set_title('Micrograph of Mapped Area')
        ax.set_xlabel('mm')
        ax.set_ylabel('mm')
        self.draw_edge(ax, color='red')
        return ax

    def plot_histogram(self, ax=None, bins=100):
        minimum = self.metric_normalizer.vmin
        maximum = self.metric_normalizer.vmax
        metrics = [locus.metric for locus in self.loci]
        metrics = np.clip(metrics, minimum, maximum)
        weights = [locus.reliability for locus in self.loci]
        if ax is None:
            ax = new_axes(height=4, width=7)
        # Generate the histogram
        n, bins, patches = ax.hist(metrics, bins=bins, weights=weights)
        # Set the colors based on the metric normalizer
        for patch in patches:
            x_position = patch.get_x()
            cmap = self.get_cmap()
            color = cmap(self.metric_normalizer(x_position))
            patch.set_color(color)
        ax.set_xlim(minimum, maximum)
        ax.set_xlabel(self.metric_name)
        ax.set_ylabel('Occurrences')
        return ax

    def __repr__(self):
        return '<{cls}: {name}>'.format(cls=self.__class__.__name__,
                                        name=self.sample_name)


class DummyMap(Map):
    """
    Sample that returns a dummy map for testing.
    """

    def composite_image(self):
        # Stub image to show for layout purposes
        directory = os.path.dirname(os.path.realpath(__file__))
        # Read a cached composite image from disk
        image = scipy.misc.imread(
            '{0}/../images/test-composite-image.png'.format(directory)
        )
        return image

    def mapscan_metric(self, scan):
        # Just return the distance from bottom left to top right
        p = scan.cube_coords[0]
        rows = scan.xrd_map.rows
        r = (p / 2 / rows) + 0.5
        return r

    def plot_map(self, *args, **kwargs):
        # Ensure that "diffractogram is loaded" for each scan
        for locus in self.loci:
            locus.diffractogram_is_loaded = True
            p = locus.cube_coords[0]
            rows = locus.parent_map.rows
            r = (p / 2 / rows) + 0.5
            locus.metric = r
        return super().plot_map(*args, **kwargs)

    def create_loci(self):
        """Populate the loci array with new scans in a hexagonal array."""
        self.loci = []
        for idx, coords in enumerate(self.path(self.rows)):
            # Try and determine filename from the sample name
            fileBase = "map-{n:x}".format(n=idx)
            new_locus = DummyLocus(location=coords, xrd_map=self,
                                   filebase=fileBase)
            self.loci.append(new_locus)


class PeakPositionMap(Map):
    """A map based on the two-theta position of the diagnostic reflection
    in the first phase.
    """

    def mapscan_metric(self, scan):
        """
        Return the 2θ difference of self.peak1 and self.peak2. Peak
        difference is used to overcome errors caused by shifter
        patterns.
        """
        main_phase = scan.phases[0]
        two_theta_range = main_phase.diagnostic_reflection.two_theta_range
        metric = scan.peak_position(two_theta_range)
        return metric


class PhaseRatioMap(Map):

    def mapscan_metric(self, scan):
        """Compare the ratio of two peaks, one for discharged and one for
        charged material.
        """
        # Query refinement for the contributions from each phase
        contributions = [phase.scale_factor for phase in scan.phases]
        total = sum(contributions)
        if total > 0:  # Avoid div by zero
            ratio = contributions[0] / sum(contributions)
        else:
            ratio = 0
        return ratio

    def mapscan_reliability(self, scan):
        """Determine the maximum total intensity of signal peaks."""
        scale_factors = [phase.scale_factor for phase in scan.phases]
        # area1 = self._phase_signal(scan=scan, phase=scan.phases[0])
        # area2 = self._phase_signal(scan=scan, phase=scan.phases[1])
        return sum(scale_factors)

    def _phase_signal(self, scan, phase):
        peak = phase.diagnostic_reflection.two_theta_range
        area = scan.peak_area(peak)
        return area

    def _peak_position(self, scan, phase):
        peak = phase.diagnostic_reflection.two_theta_range
        angle = scan.peak_position(peak)
        return angle

    def metric_details(self, scan):
        """
       Return a string with the measured areas of the two peaks.
       """
        area1 = self._phase_signal(scan=scan, phase=self.phase_list[0])
        angle1 = self._peak_position(scan=scan, phase=self.phase_list[0])
        area2 = self._phase_signal(scan=scan, phase=self.phase_list[1])
        angle2 = self._peak_position(scan=scan, phase=self.phase_list[1])
        template = "Area 1 ({angle1:.02f}°): {area1:.03f}\nArea 2 ({angle2:.02f}°): {area2:.03f}\nSum: {total:.03f}"  # noqa
        msg = template.format(area1=area1, angle1=angle1,
                              area2=area2, angle2=angle2,
                              total=area1 + area2)
        return msg


class FwhmMap(Map):
    def mapscan_metric(self, scan):
        """
        Return the full-width half-max of the diagnostic peak in the first
        phase.
        """
        angle = sum(scan.phases[0].diagnostic_reflection.two_theta_range) / 2
        fwhm = scan.refinement.fwhm(angle)
        return fwhm


class IORMap(Map):
    """One-off material for submitting an image of the Image of Research
    competition at UIC."""
    metric_normalizer = colors.Normalize(0, 1, clip=True)
    reliability_normalizer = colors.Normalize(2.3, 4.5, clip=True)
    charged_peak = '331'
    discharged_peak = '400'
    reliability_peak = '400'

    def mapscan_metric(self, scan):
        area = self.peak_area(scan, self.peak_list[self.charged_peak])
        return area
