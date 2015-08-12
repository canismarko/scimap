# -*- coding: utf-8 -*-

from subprocess import call
import re
import io
import os
import sys
import tempfile
from enum import Enum

import pandas as pd
import jinja2

import plots
import exceptions
from refinement.base import BaseRefinement

class ProfileMatch(BaseRefinement):
    bg_coeffs = [0, 0, 0, 0, 0, 0] # Sixth degree polynomial
    fullprof_path = '/home/mwolf/bin/fullprof'
    zero = 0 # Instrument non-centrality
    displacement = 0.00032 # cos (θ) dependence
    transparency = -0.00810 # sin (θ) dependence

    class Mode(Enum):
        """
        Refinement modes used by fullprof for the Jbt value.
        """
        rietveld = 0
        magnetic = 1
        constant_scale = 2
        constant_intensities = 3

    def calculated_diffractogram(self):
        """Read a pcf file and return the refinement as a dataframe."""
        df = pd.read_csv(self.filename, skiprows=3, sep='\t')
        return df

    def write_hkl_file(self, phase, filename):
        # Write separate hkl file for each phase
        hkl_string = " {h} {k} {l} {mult} {intensity}\n"
        with open(filename, 'w') as hklfile:
            # Write header
            hklfile.write('{}\n'.format(phase))
            hklfile.write('30 0 0.00 SPGr: {}\n'.format(phase.fullprof_spacegroup))
            # Write list of reflections
            for reflection in phase.reflection_list:
                hkl = reflection.hkl
                hklfile.write(hkl_string.format(h=hkl.h, k=hkl.k, l=hkl.l,
                                                mult=reflection.multiplicity,
                                                intensity=reflection.intensity))

    def run_fullprof(self, context, keep_temp_files):
        """Prepare a pcr file and execute the actual fullprof program."""
        # Make sure the context is sane
        if context['num_params'] == 0:
            msg = "context['num_params'] is zero"
            raise exceptions.EmptyRefinementError(msg)
        # Set environmental variables
        os.environ['FULLPROF'] = self.fullprof_path
        os.environ['PATH'] += os.pathsep + self.fullprof_path
        # Write hkl file if necessary
        hkl_filenames = []
        if context['refinement_mode'] == self.Mode.constant_scale:
            context['Irf'] = 0 # Reflections generated by FullProf
        else:
            context['Irf'] = 2 # Need to save codefile
            # Write an hkl file for each phase
            for idx, phase in enumerate(self.scan.phases):
                hklfilename = self.basename + str(idx+1) + '.hkl'
                hkl_filenames.append(hklfilename)
                self.write_hkl_file(phase, hklfilename)
        # Prepare pcr file
        env = jinja2.Environment(loader=jinja2.PackageLoader('electrolab', ''))
        template = env.get_template('refinement/fullprof-template.pcr')
        pcrfilename = self.basename + '.pcr'
        with open(pcrfilename, mode='w') as pcrfile:
            pcrfile.write(template.render(**context))
        # Write datafile
        datafilename = self.basename + '.dat'
        self.scan.save_diffractogram(datafilename)
        # Execute refinement
        logfilename = self.basename + '.log'
        with open(os.devnull, 'w') as devnull, open(logfilename, 'w') as logfile:
            stdout = None
            stdout = devnull
            call(['fp2k', pcrfilename], stdout=logfile)
        # Read refined values
        try:
            self.load_results()
        except exceptions.RefinementError as e:
            msg = "Check logfile {} for details.".format(logfilename)
            raise exceptions.RefinementError(msg)
        else:
            # If all went well, delete temporary files
            if not keep_temp_files:
                os.remove(logfilename)
                os.remove(self.basename + '.sum')
                os.remove(datafilename)
                [os.remove(f) for f in hkl_filenames]
                # os.remove(pcrfilename)

    def refine_background(self, keep_temp_files=False):
        """
        Refine the six background coefficients.
        """
        # Set codewords on background parameters
        context = self.pcrfile_context()
        context['bg_codewords'] = [11, 21, 31, 41, 51, 61]
        # context['bg_codewords'] = [0, 0, 0, 0, 0, 0]
        context['num_params'] = 6
        # Execute refinement
        self.run_fullprof(context=context, keep_temp_files=keep_temp_files)
        # Set status flag
        self.is_refined['background'] = True

    def refine_displacement(self, keep_temp_files=False):
        """
        Refine sample displacement cos θ dependendent correction.
        """
        context = self.pcrfile_context()
        context['displacement_codeword'] = 11
        context['num_params'] = 1
        # Execute refinement
        self.run_fullprof(context=context, keep_temp_files=keep_temp_files)
        # Set status flag
        self.is_refined['displacement'] = True

    def refine_scale_factors(self, keep_temp_files=False):
        context = self.pcrfile_context()
        # Must be in constant intensities mode to refine a scale factor
        context['num_params'] = 2
        for idx, phase in enumerate(context['phases']):
            phase['codewords']['scale'] = (idx+1)*10 + 1
        # Execute refinement
        self.run_fullprof(context=context, keep_temp_files=keep_temp_files)
        # Set status flag
        self.is_refined['scale_factors'] = True

    def load_results(self, filename=None):
        """
        After a refinement, load the result (.sum) file and restore parameters.
        """
        if filename is None:
            filename = self.basename + '.sum'
        with open(filename) as summaryfile:
            summary = summaryfile.read()
        # Check for successful refinement
        success_re = re.compile('==> RESULTS OF REFINEMENT:')
        success = success_re.search(summary)
        if not success:
            raise exceptions.RefinementError()
        # Search for final Χ² value
        chi_re = re.compile('Chi2:\s+([0-9.]+)')
        chi_squared = float(chi_re.search(summary).group(1))
        self.chi_squared = chi_squared
        # Search for the background coeffs
        bg_re = re.compile('Background Polynomial Parameters ==>((?:\s+[-0-9.]+)+)')
        bg_results = bg_re.search(summary).group(1).split()
        # (Even values are coeffs, odd values are standard deviations)
        bg_coeffs = [float(x) for x in bg_results[::2]]
        bg_stdevs = [float(x) for x in bg_results[1::2]]
        self.bg_coeffs = bg_coeffs
        # Search for sample displacement correction
        displacement_re = re.compile('Cos\( theta\)-shift parameter :\s+([-0-9.]+)')
        displacement = float(displacement_re.search(summary).group(1))
        self.displacement = displacement

    def pcrfile_context(self):
        """Generate a dict of values to put into a pcr input file."""
        context = {}
        num_phases = len(self.scan.phases)
        context['num_phases'] = num_phases
        # Determine refinement mode based on number of phases
        if num_phases == 1:
            mode = self.Mode.constant_scale
        elif num_phases > 1:
            mode = self.Mode.constant_intensities
        context['refinement_mode'] = mode
        # Prepare parameters for each phase
        phases = []
        for phase in self.scan.phases:
            unitcell = phase.unit_cell
            vals = {
                'a': unitcell.a, 'b': unitcell.b, 'c': unitcell.c,
                'alpha': unitcell.alpha, 'beta': unitcell.beta, 'gamma': unitcell.gamma,
                'u': phase.u, 'v': phase.v, 'w': phase.w, 'x': phase.x,
                'scale': phase.scale_factor, 'eta': phase.eta,
                'Bov': phase.isotropic_temp,
            }
            # Codewords control which parameters are refined and in what order 
            codewords = {
                'a': 0, 'b': 0, 'c': 0,
                'alpha': 0, 'beta': 0, 'gamma': 0,
                'u': 0, 'v': 0, 'w': 0,
                'scale': 0, 'eta': 0
            }
            phases.append({
                'name': str(phase),
                'spacegroup': phase.fullprof_spacegroup,
                'vals': vals,
                'codewords': codewords
            })
        context['phases'] = phases
        # Background corrections
        context['bg_coeffs'] = self.bg_coeffs
        context['bg_codewords'] = [0 for x in self.bg_coeffs]
        # Instrument corrections
        context['zero'] = self.zero
        context['zero_codeword'] = 0
        context['displacement'] = self.displacement
        context['displacement_codeword'] = 0
        context['transparency'] = self.transparency
        context['transparency_codeword'] = 0
        # Meta-data
        context['num_params'] = 0
        return context

    def plot(self, ax=None):
        if ax is None:
            ax = plots.new_axes()
        df = self.calculated_diffractogram()
        ax.plot(df[' 2Theta'], df['Yobs'])
        ax.plot(df[' 2Theta'], df['Ycal'])
        ax.plot(df[' 2Theta'], df['Yobs-Ycal'])
        ax.set_title('Profile refinement {filename}'.format(filename=self.filename))
        ax.set_xlim(
            right=df[' 2Theta'].max()
        )
        return ax
