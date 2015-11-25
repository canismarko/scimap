import os
from collections import defaultdict
import re

import pandas as pd
from matplotlib import pyplot
import h5py

from .xradia import XRMFile
from utilities import display_progress
from .frame import TXMFrame, average_frames
import exceptions

def find_average_scans(filename, file_list, flavor='ssrl'):
    """Scan the filenames in `file_list` and see if there are multiple
    subframes per frame."""
    basename = os.path.basename(filename)
    dirname = os.path.dirname(filename)
    if flavor == 'ssrl':
        avg_regex = re.compile("(\d+)of(\d+)")
        serial_string = "_\d{6}_ref_"
        serial_regex = re.compile(serial_string)
        # Look for average scans
        re_result = avg_regex.search(basename)
        if re_result:
            # Use regular expressions to determine the other files
            total = int(re_result.group(2))
            current_files = []
            for current in range(1, total+1):
                new_regex = "0*{current}of0*{total}".format(
                    current=current, total=total)
                filename_restring = avg_regex.sub(new_regex, basename)
                # Replace serial number if necessary (reference frames only)
                filename_restring = serial_regex.sub(serial_string, filename_restring)
                # Find the matching filenames in the list
                filepath_regex = re.compile(os.path.join(dirname, filename_restring))
                for filepath in file_list:
                    if filepath_regex.match(filepath):
                        current_files.append(filepath)
                        break
            if not len(current_files) == total:
                msg = "Could not find all all files to average, only found {}"
                raise exceptions.FrameFileNotFound(msg.format(current_files))
        else:
            current_files = [current_file]
    return current_files

def import_from_directory(dirname, hdf_filename=None, flavor='ssrl'):
    """Import all files in the given directory and process into framesets."""
    format_classes = {
        '.xrm': XRMFile
    }
    # Prepare list of dataframes to be imported
    file_list = []
    for filename in os.listdir(dirname):
        # Make sure it's a file
        fullpath = os.path.join(dirname, filename)
        if os.path.isfile(fullpath):
            # Import the file if the extension is known
            name, extension = os.path.splitext(filename)
            if extension in format_classes.keys():
                file_list.append(fullpath)
    # Prepare some global data for the import process
    if hdf_filename is None:
        real_name = os.path.abspath(dirname)
        hdf_filename = os.path.join(
            dirname,
            "{basename}.hdf".format(basename='processed_frameset')
        )
    if os.path.exists(hdf_filename):
        msg = "Refusing to overwrite file {}"
        raise exceptions.FileExistsError(msg.format(hdf_filename))
    full_frameset = XanesFrameset(filename=hdf_filename)
    total_files = len(file_list)
    # Prepare a temporary background_frames group
    full_frameset.hdf_file().create_group('background_frames')
    # Now do the importing
    while(len(file_list) > 0):
        current_file = file_list[0]
        name, extension = os.path.splitext(current_file)
        # Average multiple frames together if necessary
        files_to_average = find_average_scans(current_file, file_list, flavor=flavor)
        frames_to_average = []
        # Convert to Frame() objects
        for filepath in files_to_average:
            Importer = format_classes[extension]
            with Importer(filepath) as txm_file:
                frame = TXMFrame(file=txm_file)
                frames_to_average.append(frame)
        # Average scans
        averaged_frame = average_frames(*frames_to_average)
        # Remove from queue and add to frameset
        for filepath in files_to_average:
            file_list.remove(filepath)
        if averaged_frame.is_background:
            group = 'background_frames'
        else:
            group = 'frames'
        full_frameset.add_frame(averaged_frame, group=group)
        # Display current progress
        template = 'Averaging frames: {curr}/{total} ({percent:.0f}%)'
        status = template.format(
            curr=total_files - len(file_list),
            total=total_files,
            percent=(1 - (len(file_list)/total_files))*100
        )
        print(status, end='\r')
        # Subtract background
        for frame in frameset:
            print(frame.energy)

    # # Sort the frames into framesets by location
    # framesets = defaultdict(list)
    # for frame in frame_list:
    #     framesets[frame.approximate_position].append(frame)
    # framesets = list(framesets.values())
    # # Turn each frameset into a XanesFrameset
    # number_of_framesets = len(framesets)
    # if hdf_filename is None:
    #     hdf_basename = os.path.basename(directory)
    # xanes_list = []
    # for idx, frames in enumerate(framesets):
    #     xanes_frameset = XanesFrameset(frames)
    #     xanes_frameset.create_hdf()
    #     xanes_list.append(xanes_frameset)
    # return {'samples': xanes_list,
    #         'background': background_list}
    return full_frameset

def build_dataframe(frames):
    index = [frame.energy for frame in frames]
    images = [pd.DataFrame(frame.image_data) for frame in frames]
    series = pd.Series(images, index=index)
    return series

class XanesFrameset():

    def __init__(self, filename, group='frames'):
        self.hdf_filename = filename
        self.group_name = group

    def __iter__(self):
        """Get each frame from the HDF5 file"""
        hdf_file = self.hdf_file()
        for dataset_name in hdf_file['frames'].keys():
            yield TXMFrame.load_from_dataset(hdf_file['frames'][dataset_name])

    def __getitem__(self, index):
        hdf_file = self.hdf_file()
        dataset_name = list(hdf_file['frames'].keys())[index]
        return TXMFrame.load_from_dataset(hdf_file['frames'][dataset_name])

    def subtract_background(self, dataset_name='background_frames'):
        bg_group = self.hdf_file()[dataset_name]
        for energy in display_progress(self.hdf_group().keys(), "Subtracting background:"):
            sample_dataset = self.hdf_group()[energy]
            bg_dataset = bg_group[energy]
            sample_dataset.write_direct(sample_dataset.value - bg_dataset.value)

    def plot_full_image(self):
        return pyplot.imshow(self.df.mean())

    def xanes_spectrum(self):
        """Collapse the dataset down to a two-d spectrum."""
        intensities = []
        for energy in self.df.index:
            # Sum all the pixel intensities for the image frame
            intensities.append(self.df.ix[energy].sum().sum())
        # Combine into a series
        series = pd.Series(intensities, index=self.df.index)
        return series

    def hdf_file(self):
        # Determine filename
        try:
            file = h5py.File(self.hdf_filename, 'r+')
        except OSError as e:
            # HDF File does not exist, make a new one
            print('Creating new HDF5 file: {}'.format(self.hdf_filename))
            file = h5py.File(self.hdf_filename, 'w-')
            file.create_group('frames')
        return file

    def hdf_group(self):
        return self.hdf_file()[self.group_name]

    def add_frame(self, frame, group='frames'):
        setname_template = "{energy}eV{serial}"
        with self.hdf_file() as file:
            frames_group = file[group]
            # Find a unique frame dataset
            setname = setname_template.format(
                energy=frame.energy,
                serial=""
            )
            counter = 0
            while setname in frames_group.keys():
                counter += 1
                setname = setname_template.format(
                    energy=frame.energy,
                    serial="-" + str(counter)
                )
            # Name found, so create the actual dataset
            new_dataset = frame.create_dataset(setname=setname,
                                               hdf_group=frames_group)
            return setname