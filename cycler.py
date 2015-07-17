from matplotlib import pyplot
import numpy as np
import pandas as pd
import re

def axis_label(key):
    axis_labels = {
        'Ewe/V': r'$E\ /V$',
        'capacity': r'$Capacity\ / mAhg^{-1}$',
    }
    # Look for label translation or return original key
    return axis_labels.get(key, key)

def new_axes():
    """Create a new set of matplotlib axes for plotting"""
    fig = pyplot.figure(figsize=(10, 6))
    ax = pyplot.gca()
    return ax

def mass_from_file(filename):
    """"""
    pass

class GalvanostatRun():
    """
    Electrochemical experiment cycling on one channel.
    Galvanostatic control potential limited (GPLC).
    """
    cycles = []

    def __init__(self, filename, mass=None, *args, **kwargs):
        self.filename = filename
        self.load_csv(filename)
        self.cycles = []
        # Remove the initial resting period
        restingIndexes = self._df.loc[self._df['mode']==3].index
        self._df.drop(restingIndexes, inplace=True)
        # Calculate capacity from charge and mass
        if mass:
            # User provided the mass
            self.mass = mass
        else:
            # Get mass from eclab file
            self.mass = self.mass_from_file()
        self._df.loc[:,'capacity'] = self._df.loc[:,'(Q-Qo)/mA.h']/self.mass
        # Split the data into cycles, except the initial resting phase
        if 'cycle number' in self._df.columns:
            cycles = list(self._df.groupby('cycle number'))
        else:
            cycles = [(0, all_cycles)]
        # Create Cycle objects for each cycle
        for cycle in cycles:
            new_cycle = Cycle(cycle[0], cycle[1])
            self.cycles.append(new_cycle)
        super(GalvanostatRun, self).__init__(*args, **kwargs)

    def load_csv(self, filename, *args, **kwargs):
        """Wrapper around pandas read_csv that filters out crappy data"""
        # Determine start of data
        with open(filename, encoding='latin-1') as dataFile:
            # The second line states how long the header is
            headerLength = int(dataFile.readlines()[1][18:20]) - 1
        # Skip all the initial metadata
        df = pd.read_csv(filename,
                         *args,
                         skiprows=headerLength,
                         na_values='XXX',
                         sep='\t',
                         **kwargs)
        self._df = df
        return df

    def mass_from_file(self):
        """Read the mpt file and extract the sample mass"""
        regexp = re.compile('^Mass of active material : ([0-9.]+) mg')
        mass = None
        with open(self.filename, encoding='latin-1') as f:
            for line in f:
                match = regexp.match(line)
                if match:
                    # We found the match, now save it
                    mass = float(match.groups()[0]) / 1000
                    break
        return mass

    def plot_cycles(self, xcolumn, ycolumn, ax=None):
        """Plot each electrochemical cycle"""
        if not ax:
            ax = new_axes()
        ax.set_xlabel(axis_label(xcolumn))
        ax.set_ylabel(axis_label(ycolumn))
        legend = []
        for cycle in self.cycles:
            ax = cycle.plot_cycle(xcolumn, ycolumn, ax)
            legend.append(cycle.number)
        ax.legend(legend)
        return ax

    def plot_discharge_capacity(self, ax=None, ax2=None):
        if not ax:
            ax = new_axes()
        if not ax2:
            ax2 = ax.twinx()
        cycle_numbers = []
        capacities = []
        efficiencies = []
        # Calculate relevant plotting values
        for cycle in self.cycles:
            cycle_numbers.append(cycle.number)
            capacities.append(cycle.discharge_capacity())
            efficiency = 100 * cycle.discharge_capacity() / cycle.charge_capacity()
            efficiencies.append(efficiency)
        ax.plot(cycle_numbers, capacities, marker='o', linestyle='--')
        ax2.plot(cycle_numbers, efficiencies)
        # Format axes
        ax.set_xticks(cycle_numbers)
        ax.set_xlim(0, 1 + max(cycle_numbers))
        ax.set_ylim(0, 1.1 * max(capacities))
        ax.set_xlabel('Cycle')
        ax.set_ylabel('Discharge capacity $/mAhg^{-1}$')
        ax2.set_ylim(0, 100)
        ax2.set_ylabel('Discharge efficiency (%)')
        return ax, ax2


class Cycle():
    """Data from one charge-discharge cycle."""
    def __init__(self, number, df):
        self.number = number
        self.df = df

    def charge_capacity(self):
        """Calculate difference between discharged and charged state"""
        max_capacity = np.max(self.df['capacity'])
        min_idx = self.df['capacity'].first_valid_index()
        min_capacity = self.df['capacity'][min_idx]
        return max_capacity - min_capacity

    def discharge_capacity(self):
        """Calculate the difference between charged and discharged state"""
        max_capacity = np.max(self.df['capacity'])
        min_idx = self.df['capacity'].last_valid_index()
        min_capacity = self.df['capacity'][min_idx]
        return max_capacity - min_capacity

    def plot_cycle(self, xcolumn, ycolumn, ax):
        # Drop missing data
        df = self.df.dropna(subset=[xcolumn, ycolumn])
        # Plot remaining values
        ax.plot(df[xcolumn], df[ycolumn])
        return ax