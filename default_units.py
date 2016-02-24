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

"""
Define common units across the whole application. Dataframes
assume the following units and can convert after calculation.
"""

import units
import units.predefined

# Define default units
units.predefined.define_units()
# Grams
mass = units.unit('g')
# Time (hours)
time = units.unit('h')
# Milli-amp hours
capacity = units.unit('mA') * units.unit('h')
# Milli-amp hours per gram
specific_capacity = units.unit('mA') * units.unit('h') / units.unit('g')
# Volts
potential = units.unit('V')
# Electrode mass loading
cm = units.unit('cm')
electrode_loading = units.unit('mg') / (cm * cm)
