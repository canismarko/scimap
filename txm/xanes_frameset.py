import os
from collections import defaultdict
import re
import math
import multiprocessing
import queue

import pandas as pd
from matplotlib import pyplot
from matplotlib.colors import Normalize
from mapping.colormaps import cmaps
import h5py
import numpy as np
from skimage import morphology, filters, feature

from utilities import display_progress, xycoord
from .frame import TXMFrame, average_frames, calculate_particle_labels
from .gtk_viewer import GtkTxmViewer
from plots import new_axes
import exceptions
from hdf import HDFAttribute


def build_dataframe(frames):
    index = [frame.energy for frame in frames]
    images = [pd.DataFrame(frame.image_data) for frame in frames]
    series = pd.Series(images, index=index)
    return series

class XanesFrameset():
    _attrs = {}
    active_groupname = None # HDFAttribute('active_groupname')
    latest_groupname = HDFAttribute('latest_groupname')
    background_groupname = HDFAttribute('background_groupname')
    active_particle_idx = HDFAttribute('active_particle_idx', default=None,
                                       group_func='active_group')
    latest_labels = HDFAttribute('latest_labels', default='particle_labels')
    cmap = 'plasma'
    def __init__(self, filename, groupname, edge=None):
        self.hdf_filename = filename
        self.parent_groupname = groupname
        self.edge = edge
        # Check to make sure a valid group is given
        with self.hdf_file() as hdf_file:
            if not groupname in hdf_file.keys():
                msg = "Created new frameset group: {}"
                print(msg.format(groupname))
                hdf_file.create_group(groupname)
        self.active_groupname = self.latest_groupname

    def __iter__(self):
        """Get each frame from the HDF5 file"""
        hdf_file = self.hdf_file()
        for dataset_name in self.active_group().keys():
            yield TXMFrame.load_from_dataset(self.active_group()[dataset_name])

    def __len__(self):
        return len(self.active_group().keys())

    def __getitem__(self, index):
        hdf_file = self.hdf_file()
        dataset_name = list(self.active_group().keys())[index]
        return TXMFrame.load_from_dataset(self.active_group()[dataset_name])

    @property
    def active_labels_groupname(self):
        """The group name for the latest frameset of detected particle labels."""
        # Save as an HDF5 attribute
        group = self.active_group()
        return group.attrs.get('active_labels', None)

    @active_labels_groupname.setter
    def active_labels_groupname(self, value):
        group = self.active_group()
        group.attrs['active_labels'] = value

    def particle(self, particle_idx=0):
        """Prepare a particle frameset for the given particle index."""
        fs = ParticleFrameset(parent=self, particle_idx=particle_idx)
        return fs

    def switch_group(self, name):
        self.active_groupname = name

    def fork_group(self, name):
        """Create a new, copy of the current active group inside the HDF
        parent with name: `name`.
        """
        # Create an empty group
        try:
            del self.hdf_group()[name]
        except KeyError:
            pass
        # Copy the old data
        self.hdf_group().copy(self.active_groupname, name)
        # Update the group name
        self.latest_groupname = name
        self.switch_group(name)

    def fork_labels(self, name):
        # Create a new group
        if name in self.hdf_group().keys():
            del self.hdf_group()[name]
        self.hdf_group().copy(self.active_labels_groupname, name)
        labels_group = self.hdf_group()[name]
        # Update label paths for frame datasets
        for frame in self:
            key = frame.image_data.name.split('/')[-1]
            new_label_name = labels_group[key].name
            frame.particle_labels_path = new_label_name
        self.latest_labels = name
        self.active_labels_groupname = name
        return labels_group

    def subtract_background(self, bg_groupname):
        self.background_groupname = bg_groupname
        self.fork_group('absorbance_frames')
        bg_group = self.hdf_file()[bg_groupname]
        for energy in display_progress(self.active_group().keys(), "Applying reference corrections"):
            sample_dataset = self.active_group()[energy]
            bg_dataset = bg_group[energy]
            new_data = np.log10(bg_dataset.value/sample_dataset.value)
            sample_dataset.write_direct(new_data)

    def align_frames(self, particle_idx=0, reference_frame=0):
        """Use phase correlation algorithm to line up the frames."""
        # Create new data groups to hold shifted image data
        self.fork_group('aligned_particle_{}'.format(particle_idx))
        self.fork_labels('aligned_labels_{}'.format(particle_idx))
        # pixel precision first
        reference_image = self[reference_frame].image_data.value
        for frame in display_progress(self, 'Aligning frames'):
            results = feature.register_translation(reference_image,
                                                   frame.image_data.value)
            shift, error, diffphase = results
            # shift the frame image to be aligned with the reference frame
            shift = xycoord(shift[1], shift[0])
            frame.shift_data(x_offset=shift.x, y_offset=shift.y)
            frame.shift_data(x_offset=shift.x, y_offset=shift.y,
                             dataset=frame.particle_labels())

    def align_to_particle_centroid(self, particle_idx=0):
        """Use the centroid position of given particle to align all the
        frames."""
        self.fork_group('aligned_particle_{}'.format(particle_idx))
        self.fork_labels('aligned_labels_{}'.format(particle_idx))
        self.active_particle_idx = particle_idx
        # Determine average positions
        total_x = 0; total_y = 0; n=0
        for frame in display_progress(self, 'Computing true center'):
            particle = frame.particles()[particle_idx]
            n+=1
            total_x += particle.centroid().x
            total_y += particle.centroid().y
        global_center = xycoord(x=total_x/n, y=total_y/n)
        # Align all frames to average position
        for frame in display_progress(self, 'Aligning frames'):
            particle = frame.particles()[particle_idx]
            offset_x = int(round(global_center.x - particle.centroid().x))
            offset_y = int(round(global_center.y - particle.centroid().y))
            # Shift image data and particle labels
            frame.shift_data(x_offset=offset_x,
                             y_offset=offset_y)
            frame.shift_data(x_offset=offset_x,
                             y_offset=offset_y,
                             dataset=frame.particle_labels())

    def crop_to_particle(self, particle_idx):
        """Reduce the image size to just show the particle in question."""
        # Verify that frameset has been aligned to a particle
        # if self.active_particle_idx is None:
        #     msg =  "Frameset has not been associated with a particle."
        #     msg += "Please run `align_to_particle` first."
        #     raise exceptions.NoParticleError(msg)
        # else:
        #     particle_idx = self.active_particle_idx
        self.active_particle_idx = particle_idx
        # Create new HDF5 groups
        self.fork_group('cropped_particle_{}'.format(particle_idx))
        self.fork_labels('cropped_labels_{}'.format(particle_idx))
        # Determine largest bounding box based on all energies
        boxes = [frame.particles()[particle_idx].bbox()
                  for frame in self]
        left = min([box.left for box in boxes])
        top = min([box.top for box in boxes])
        bottom = max([box.bottom for box in boxes])
        right = max([box.right for box in boxes])
        # Roll each image to have the particle top left
        for frame in display_progress(self, 'Cropping frames'):
            frame.crop(top=top, left=left, bottom=bottom, right=right)
            # Determine new main particle index
            new_idx = np.argmax([p.convex_area() for p in frame.particles()])
            frame.active_particle_idx = new_idx

    def align_frame_positions(self):
        """Correct for inaccurate motion in the sample motors."""
        self.fork_group('aligned_frames')
        self.fork_labels('aligned_labels')
        # Determine average positions
        total_x = 0; total_y = 0; n=0
        for frame in display_progress(self, 'Computing true center'):
            n+=1
            total_x += frame.sample_position.x
            total_y += frame.sample_position.y
        global_x = total_x / n
        global_y = total_y / n
        for frame in display_progress(self, 'Aligning frames'):
            um_per_pixel_x = 40/frame.image_data.shape[1]
            um_per_pixel_y = 40/frame.image_data.shape[0]
            offset_x = int(round((global_x - frame.sample_position.x)/um_per_pixel_x))
            offset_y = int(round((global_y - frame.sample_position.y)/um_per_pixel_y))
            frame.shift_data(x_offset=offset_x, y_offset=offset_y)
            # Store updated position info
            new_position = (
                frame.sample_position.x + offset_x * um_per_pixel_x,
                frame.sample_position.y + offset_y * um_per_pixel_y,
                frame.sample_position.z
            )
            frame.sample_position = new_position

    def label_particles(self):
        labels_groupname = 'particle_labels'
        if labels_groupname in self.hdf_group().keys():
            del self.hdf_group()[labels_groupname]
        self.active_labels_groupname = labels_groupname
        # Create a new group
        labels_group = self.hdf_group().create_group(labels_groupname)
        # Callables for determining particle labels
        def worker(payload):
            key, data = payload
            new_data = calculate_particle_labels(data)
            return (key, new_data)

        def process_result(payload):
            # Save the calculated data
            key, data = payload
            labels = self.hdf_group()[labels_groupname]
            dataset = labels.create_dataset(key, data=data, compression='gzip')
            status = 'Identifying particles {curr}/{total} ({percent:.0f}%)'.format(
                curr=total_results - results_left, total=total_results,
                percent=(1-results_left/total_results)*100
            )
            print(status, end='\r')
        # Prepare multirprocessing objects
        num_consumers = multiprocessing.cpu_count() * 2
        frame_queue = multiprocessing.JoinableQueue(maxsize=num_consumers* 2)
        result_queue = multiprocessing.Queue()
        consumers = [FrameConsumer(target=worker, task_queue=frame_queue, result_queue=result_queue)
                     for i in range(num_consumers)]
        for consumer in consumers:
            consumer.start()
        # Load frames into the queue to be processed
        total_results = len(self)
        results_left = len(self) # Counter for ending routine
        for frame in self:
            data = frame.image_data.value
            key = frame.image_data.name.split('/')[-1]
            frame_queue.put((key, data))
            # Write path to saved particle labels
            frame.particle_labels_path = labels_group.name + "/" + key
            # Check for a saved result
            try:
                result = result_queue.get(block=False)
            except queue.Empty:
                pass
            else:
                process_result(result)
                results_left -= 1
        # Send poison pill to stop workers
        for i in range(num_consumers):
            frame_queue.put(None)
        # Wait for all processing to finish
        frame_queue.join()
        # Finish processing results
        while results_left > 0:
            result = result_queue.get()
            process_result(result)
            results_left -= 1
        print('Identifying particles: {total}/{total} [done]'.format(
            total=total_results))
        # Detect particles and update links in frame datasets
        # for frame in display_progress(self, 'Identifying particles'):
        #     key = frame.image_data.name.split('/')[-1]
        #     data = frame.calculate_particle_labels()
        #     dataset = labels_group.create_dataset(name=key, data=data)
        #     frame.particle_labels_path = dataset.name

    def rebin(self, shape=None, factor=None):
        """Resample all images into new shape. Arguments `shape` and `factor`
        passed to txm.frame.TXMFrame.rebin().
        """
        self.fork_group('rebinned')
        for frame in display_progress(self, "Rebinning"):
            frame.rebin(shape=shape, factor=factor)

    def plot_full_image(self):
        return pyplot.imshow(self.df.mean())

    def xanes_spectrum(self):
        """Collapse the dataset down to a two-d spectrum."""
        energies = []
        intensities = []
        for frame in self:
            data = frame.image_data.value
            if self.active_particle_idx:
                # Apply mask to the image data
                if frame.active_particle_idx is not None:
                    particle = frame.particles()[frame.active_particle_idx]
                else:
                    particle = frame.particles()[self.active_particle_idx]
                # Create mask that's the same size as the image
                bbox = particle.bbox()
                mask = np.zeros_like(data)
                mask[bbox.top:bbox.bottom, bbox.left:bbox.right] = particle.mask()
                mask = np.logical_not(mask)
                data[mask] = 0
            # Sum absorbances for datasets
            intensity = np.sum(data)/np.prod(data.shape)
            # Add to cumulative arrays
            intensities.append(intensity)
            energies.append(frame.energy)
        # Combine into a series
        series = pd.Series(intensities, index=energies)
        return series

    def plot_xanes_spectrum(self, ax=None):
        spectrum = self.xanes_spectrum()
        if ax is None:
            ax = new_axes()
        ax.plot(spectrum, marker='o', linestyle="None")
        ax.set_xlabel('Energy /eV')
        ax.set_ylabel('Overall absorbance')
        return ax

    def edge_jump_filter(self):
        """Calculate an image mask filter that represents the difference in
        signal across the X-ray edge."""
        # Sort frames into pre-edge and post-edge
        pre_edge = self.edge.pre_edge
        post_edge = self.edge.post_edge
        pre_images = []
        post_images = []
        for frame in self:
            if pre_edge[0] <= frame.energy <= pre_edge[1]:
                pre_images.append(frame.image_data)
            elif post_edge[0] <= frame.energy <= post_edge[0]:
                post_images.append(frame.image_data)
        # Convert lists to numpy arrays
        pre_images = np.array(pre_images)
        post_images = np.array(post_images)
        # Find average frames pre-edge/post-edge values
        pre_average = np.mean(pre_images, axis=0)
        post_average = np.mean(post_images, axis=0)
        # Subtract pre-edge from post-edge
        filtered_img = post_average - pre_average
        # Apply normalizer? (maybe later)
        return filtered_img

    def masked_map(self):
        """Generate a map based on pixel-wise Xanes spectra and apply an
        edge-jump filter mask."""
        # Calculate the whiteline position for each pixel
        map_data = self.whiteline_map()
        # Apply edge jump mask
        edge_jump = self.edge_jump_filter()
        threshold = filters.threshold_otsu(edge_jump)
        mask = edge_jump > threshold
        mask = morphology.dilation(mask)
        mask = np.logical_not(mask)
        masked_map = np.ma.array(map_data, mask=mask)
        return masked_map

    def plot_map(self, ax=None, norm_range=(None, None)):
        if ax is None:
            ax = new_axes()
        norm = Normalize(vmin=norm_range[0], vmax=norm_range[1])
        # Plot average absorbance
        # self[-1].plot_image(ax=ax)
        ax.imshow(self.masked_map(), cmap=self.cmap, norm=norm)
        return ax

    def plot_histogram(self, ax=None, norm_range=(None, None)):
        if ax is None:
            ax = new_axes()
        norm = Normalize(norm_range[0], norm_range[1])
        mapped_image = self.masked_map()
        # Set colors on histogram
        n, bins, patches = ax.hist(mapped_image.flat(), bins=self.edge.energies())
        # Set normalizer
        for patch in patches:
            x_position = patch.get_x()
            cmap = cmaps[self.cmap]
            color = cmap(norm(x_position))
            patch.set_color(color)
        return ax

    def whiteline_map(self):
        # Determine indices of max frame per pixel
        imagestack = np.array([frame.image_data for frame in self])
        indices = np.argmax(imagestack, axis=0)
        # Map indices to energies
        print('Calculating whiteline map...', end='')
        map_energy = np.vectorize(lambda idx: self[idx].energy,
                                  otypes=[np.float])
        energies = map_energy(indices)
        print('done')
        return energies

    def hdf_file(self):
        # Determine filename
        try:
            file = h5py.File(self.hdf_filename, 'r+')
        except OSError as e:
            # HDF File does not exist, make a new one
            print('Creating new HDF5 file: {}'.format(self.hdf_filename))
            file = h5py.File(self.hdf_filename, 'w-')
            file.create_group(self.parent_groupname)
        return file

    def hdf_group(self):
        return self.hdf_file()[self.parent_groupname]

    def background_group(self):
        return self.hdf_file()[self.background_groupname]

    def hdf_node(self):
        """For use with HDFAttribute descriptor."""
        return self.hdf_group()

    def active_group(self):
        parent_group = self.hdf_group()
        if self.active_groupname is None:
            group = parent_group
        else:
            if not self.active_groupname in parent_group.keys():
                # Create group if necessary
                parent_group.create_group(self.active_groupname)
            group = parent_group[self.active_groupname]
        return group

    def add_frame(self, frame):
        setname_template = "{energy}_eV{serial}"
        frames_group = self.active_group()
        # Find a unique frame dataset name
        setname = setname_template.format(
            energy=frame.approximate_energy,
            serial=""
        )
        counter = 0
        while setname in frames_group.keys():
            counter += 1
            setname = setname_template.format(
                energy=frame.approximate_energy,
                serial="-" + str(counter)
            )
        # Name found, so create the actual dataset
        new_dataset = frame.create_dataset(setname=setname,
                                           hdf_group=frames_group)
        return setname

    def normalizer(self):
        # Find global limits
        global_min = 0
        global_max = 99999999999
        for frame in self:
            local_min = np.min(frame.image_data)
            if local_min < global_min:
                global_min = local_min
            local_max = np.max(frame.image_data)
            if local_max < global_max:
                global_max = local_max
        return Normalize(global_min, global_max)

    def gtk_viewer(self):
        viewer = GtkTxmViewer(frameset=self)
        viewer.show()


## Multiprocessing modules
class FrameConsumer(multiprocessing.Process):
    def __init__(self, target, task_queue, result_queue, **kwargs):
        ret = super().__init__(target=target, **kwargs)
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.target = target
        return ret

    def run(self):
        """Retrieve and process a frame from the queue or exit if poison pill
        None is passed."""
        while True:
            payload = self.task_queue.get()
            if payload is None:
                # Poison pill, so exit
                self.task_queue.task_done()
                break
            result = self.target(payload)
            self.result_queue.put(result)
            self.task_queue.task_done()
