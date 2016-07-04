#
# Copyright 2013-2016 Universidad Complutense de Madrid
#
# This file is part of PyEmir
#
# PyEmir is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PyEmir is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PyEmir.  If not, see <http://www.gnu.org/licenses/>.
#


"""Corrector to remove flat field"""

from __future__ import division
#
import logging

import numpy
from numina.flow.processing import Corrector

_logger = logging.getLogger('numina.recipes.emir')


class FlatFieldCorrector(Corrector):
    """A Node that corrects a frame from flat-field."""

    def __init__(self, flatdata, datamodel=None, dtype='float32'):

        self.update_variance = False

        super(FlatFieldCorrector, self).__init__(
            datamodel=datamodel,
            dtype=dtype)

        self.flatdata = flatdata
        self.flatdata[flatdata <= 0] = 1.0 # To avoid NaN
        self.flat_stats = flatdata.mean()
        self.calibid = 'ID-of-calib-image'

    def _run(self, img):
        import datetime
        imgid = self.get_imgid(img)

        _logger.debug('correcting flat in %s', imgid)
        _logger.debug('flat mean is %f', self.flat_stats)

        data = self.datamodel.get_data(img)
        # data = array.correct_flatfield(data, self.flatdata, dtype=self.dtype)
        # Check if flatdata as 0
        mask1 = self.flatdata < 0
        if numpy.any(mask1):
            _logger.warning('flat has %d zeros', mask1.sum())
        mask2 = ~numpy.isfinite(data)
        if numpy.any(mask2):
            _logger.warning('image has %d NaN', mask2.sum())

        result = data / self.flatdata
        result = result.astype(self.dtype)

        # FIXME: not using datamodel
        img['primary'].data = result
        hdr = img['primary'].header
        hdr['NUM-FF'] = self.calibid
        hdr['history'] = 'Flat-field correction with {}'.format(self.calibid)
        hdr['history'] = 'Flat-field correction time {}'.format(datetime.datetime.utcnow().isoformat())
        hdr['history'] = 'Flat-field correction mean {}'.format(self.flat_stats)
        return img