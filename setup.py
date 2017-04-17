#!/usr/bin/env python

from distutils.core import setup

setup(name="scimap",
      version="0.1",
      description="Tools for analyzing X-ray diffraction mapping data",
      author="Mark Wolf",
      author_email="mark.wolf.music@gmail.com",
      url="https://github.com/m3wolf/scimap",
      keywords="XANES X-ray diffraction operando",
      # install_requires=['pytz>=2013b', 'h5py', 'pandas', 'olefile',
      #                   'matplotlib', 'scikit-image', 'scikit-learn'],
      packages=['scimap',],
      # package_data={
      #     'xanespy': ['qt_map_window.ui', 'qt_frame_window.ui']
      # },
      classifiers=[
          'Development Status :: 3 - Alpha',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)',
          'Natural Language :: English',
          'Operating System :: POSIX :: Linux',
          'Programming Language :: Python :: 3.5',
          'Topic :: Scientific/Engineering :: Chemistry',
          'Topic :: Scientific/Engineering :: Physics',
          'Topic :: Scientific/Engineering :: Visualization',
      ]
)