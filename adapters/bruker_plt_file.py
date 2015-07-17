# -*- coding: utf-8 -*-

import pandas as pd

class BrukerPltFile():
    def __init__(self, filename):
        self.filename = filename

    @property
    def sample_name(self):
        return self.filename

    @property
    def dataframe(self):
        df = pd.read_csv(self.filename,
                         names=['2theta', 'counts'],
                         sep=' ', index_col=0, comment="!")
        return df