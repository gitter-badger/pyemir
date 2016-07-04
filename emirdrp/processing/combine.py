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

"""Combination routines"""

from __future__ import division
#
import logging

import numpy
from astropy.io import fits

from numina.array import combine
from numina.array import combine_shape
#

from emirdrp.processing.wcs import offsets_from_wcs


_logger = logging.getLogger('numina.recipes.emir')


def basic_processing(rinput, flow):

    cdata = []

    _logger.info('processing input images')
    for frame in rinput.obresult.images:
        hdulist = frame.open()
        fname = hdulist.filename()
        if fname:
            _logger.info('input is %s', fname)
        else:
            _logger.info('input is %s', hdulist)

        final = flow(hdulist)
        _logger.debug('output is input: %s', final is hdulist)

        cdata.append(final)

    return cdata


def basic_processing_with_combination(rinput, flow,
                                      method=combine.mean,
                                      errors=True):
    odata = []
    cdata = []
    try:
        _logger.info('processing input images')
        for frame in rinput.obresult.images:
            hdulist = frame.open()
            fname = hdulist.filename()
            if fname:
                _logger.info('input is %s', fname)
            else:
                _logger.info('input is %s', hdulist)

            final = flow(hdulist)
            _logger.debug('output is input: %s', final is hdulist)

            cdata.append(final)

            # Files to be closed at the end
            odata.append(hdulist)
            if final is not hdulist:
                odata.append(final)

        base_header = cdata[0][0].header.copy()
        _logger.info("stacking %d images using '%s'", len(cdata), method.func_name)
        data = method([d[0].data for d in cdata], dtype='float32')
        hdu = fits.PrimaryHDU(data[0], header=base_header)
        _logger.debug('update result header')
        hdu.header['history'] = "Combined %d images using '%s'" % (len(cdata), method.func_name)
        if errors:
            varhdu = fits.ImageHDU(data[1], name='VARIANCE')
            num = fits.ImageHDU(data[2], name='MAP')
            result = fits.HDUList([hdu, varhdu, num])
        else:
            result = fits.HDUList([hdu])
    finally:
        _logger.debug('closing images')
        for hdulist in odata:
            hdulist.close()

    return result


def resize_hdul(hdul, newshape, region, extensions=None, window=None,
                    scale=1, fill=0.0, conserve=True):
    from numina.frame import resize_hdu

    if extensions is None:
        extensions = [0]

    nhdul = [None] * len(hdul)
    for ext, hdu in enumerate(hdul):
        if ext in extensions:
            nhdul[ext] = resize_hdu(hdu, newshape,
                                    region, fill=fill,
                                    window=window,
                                    scale=scale,
                                    conserve=conserve)
        else:
            nhdul[ext] = hdu
    return fits.HDUList(nhdul)


def resize(frames, shape, offsetsp, finalshape, window=None):
    from numina.array import subarray_match
    _logger.info('Resizing frames and masks')
    rframes = []
    regions = []
    for frame, rel_offset in zip(frames, offsetsp):
        region, _ = subarray_match(finalshape, rel_offset, shape)
        rframe = resize_hdul(frame, finalshape, region)
        rframes.append(rframe)
        regions.append(region)
    return rframes, regions


def basic_processing_with_segmentation(rinput, flow,
                                          method=combine.mean,
                                          errors=True, bpm=None):

    odata = []
    cdata = []
    try:
        _logger.info('processing input images')
        for frame in rinput.obresult.images:
            hdulist = frame.open()
            fname = hdulist.filename()
            if fname:
                _logger.info('input is %s', fname)
            else:
                _logger.info('input is %s', hdulist)

            final = flow(hdulist)
            _logger.debug('output is input: %s', final is hdulist)

            cdata.append(final)

            # Files to be closed at the end
            odata.append(hdulist)
            if final is not hdulist:
                odata.append(final)

        base_header = cdata[0][0].header.copy()

        baseshape = (2048, 2048)
        subpixshape = (2048, 2048)

        _logger.info('Computing offsets from WCS information')
        refpix = numpy.divide(numpy.array([baseshape], dtype='int'), 2).astype('float')
        offsets_xy = offsets_from_wcs(rinput.obresult.frames, refpix)
        _logger.debug("offsets_xy %s", offsets_xy)
        # Offsets in numpy order, swaping
        offsets_fc = offsets_xy[:, ::-1]
        offsets_fc_t = numpy.round(offsets_fc).astype('int')

        _logger.info('Computing relative offsets')
        finalshape, offsetsp = combine_shape(subpixshape, offsets_fc_t)
        _logger.debug("offsetsp %s", offsetsp)

        _logger.info('Shape of resized array is %s', finalshape)
        # Resizing target frames
        rframes, regions = resize(cdata, subpixshape, offsetsp, finalshape)

        _logger.info("stacking %d images, with offsets using '%s'", len(cdata), method.func_name)
        data1 = method([d[0].data for d in rframes], dtype='float32')

        segmap  = segmentation_combined(data1[0])
        # submasks
        if bpm is None:
            masks = [(segmap[region] > 0) for region in regions]
        else:
            masks = [((segmap[region] > 0) & bpm) for region in regions]

        _logger.info("stacking %d images, with objects mask using '%s'", len(cdata), method.func_name)
        data2 = method([d[0].data for d in cdata], masks=masks, dtype='float32')
        hdu = fits.PrimaryHDU(data2[0], header=base_header)
        points_no_data = (data2[2] == 0).sum()

        _logger.debug('update result header')
        hdu.header['history'] = "Combined %d images using '%s'" % (len(cdata), method.func_name)
        _logger.info("missing points, total: %d, fraction: %3.1f", points_no_data, points_no_data / data2[2].size)

        if errors:
            varhdu = fits.ImageHDU(data2[1], name='VARIANCE')
            num = fits.ImageHDU(data2[2], name='MAP')
            result = fits.HDUList([hdu, varhdu, num])
        else:
            result = fits.HDUList([hdu])
    finally:
        _logger.debug('closing images')
        for hdulist in odata:
            hdulist.close()

    return result


def segmentation_combined(data, snr_detect=5.0, fwhm=4.0, npixels=15):
    import sep
    from astropy.convolution import Gaussian2DKernel
    from astropy.stats import gaussian_fwhm_to_sigma

    box_shape = [64, 64]
    _logger.info('point source detection2')
    _logger.info('using internal mask to remove corners')
    # Corners
    mask = numpy.zeros_like(data, dtype='int32')
    mask[2000:, 0:80] = 1
    mask[2028:, 2000:] = 1
    mask[:50, 1950:] = 1
    mask[:100, :50] = 1
    # Remove corner regions

    _logger.info('compute background map, %s', box_shape)
    bkg = sep.Background(data)

    _logger.info('reference fwhm is %5.1f pixels', fwhm)
    _logger.info('detect threshold, %3.1f over background', snr_detect)
    _logger.info('convolve with gaussian kernel, FWHM %3.1f pixels', fwhm)
    sigma = fwhm * gaussian_fwhm_to_sigma
    #
    kernel = Gaussian2DKernel(sigma)
    kernel.normalize()

    thresh = snr_detect * bkg.globalrms
    data_s = data - bkg.back()
    objects, segmap = sep.extract(data_s, thresh, minarea=npixels,
                                  filter_kernel=kernel.array, segmentation_map=True,
                                  mask=mask)
    _logger.info('detected %d objects', len(objects))
    return segmap


def basic_processing_with_update(rinput, flow):

    # FIXME: this only works with local images
    # We don't know how to store temporary GCS frames
    _logger.info('processing input images')
    for frame in rinput.obresult.images:
        with fits.open(frame.label, mode='update') as hdul:
            flow(hdul)