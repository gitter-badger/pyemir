#
# Copyright 2014-2017 Universidad Complutense de Madrid
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

"""Dither Image recipe for EMIR"""

from __future__ import division, print_function


import datetime
import uuid

import numpy
from astropy.io import fits
from numina.core import Product, RecipeError, Requirement
from numina.core.requirements import ObservationResultRequirement
from numina.array import combine
from numina.array import combine_shape, combine_shapes
from numina.array import resize_arrays, resize_arrays_alt
from numina.array.utils import coor_to_pix, image_box2d
from numina.core import ObservationResult
from numina.flow.processing import SkyCorrector
import numina.ext.gtc
from numina.core.query import Result

from emirdrp.processing.wcs import offsets_from_wcs_imgs, reference_pix_from_wcs_imgs
from emirdrp.processing.corr import offsets_from_crosscor, offsets_from_crosscor_regions
from emirdrp.core import EmirRecipe
from emirdrp.products import DataFrameType
from emirdrp.processing.combine import segmentation_combined
import emirdrp.decorators


class JoinDitheredImagesRecipe(EmirRecipe):
    """Combine single exposures obtained in dithered mode"""

    obresult = ObservationResultRequirement()
    accum_in = Requirement(DataFrameType,
                           description='Accumulated result',
                           optional=True,
                           destination='accum',
                           query_opts=Result('accum', node='prev')
                           )
    frame = Product(DataFrameType)
    sky = Product(DataFrameType, optional=True)
    #
    # Accumulate Frame results
    accum = Product(DataFrameType, optional=True)

    def build_recipe_input(self, obsres, dal, pipeline='default'):
        if numina.ext.gtc.check_gtc():
            self.logger.debug('Using GTC version of build_recipe_input in DitheredImages')
            return self.build_recipe_input_gtc(obsres, dal, pipeline=pipeline)
        else:
            return super(JoinDitheredImagesRecipe, self).build_recipe_input(obsres, dal)

    def build_recipe_input_gtc(self, obsres, dal, pipeline='default'):
        newOR = ObservationResult()
        # FIXME: this method will work only in GTC
        # stareImagesIds = obsres['stareImagesIds']._v
        stareImagesIds = obsres.stareImagesIds
        obsres.children = stareImagesIds
        self.logger.info('Submode result IDs: %s', obsres.children)
        stareImages = []
        # Field to query the results
        key_field = 'frame'
        for subresId in obsres.children:
            subres = dal.getRecipeResult(subresId)
            # This 'frame' is the name of the product in RecipeResult
            # there is also a 'sky' field
            elements = subres['elements']
            stareImages.append(elements[key_field])
        newOR.frames = stareImages

        naccum = obsres.naccum
        self.logger.info('naccum: %d', naccum)
        mode_field = "DITHERED_IMAGE"
        key_field = 'accum'
        if naccum != 1:  # if it is not the first dithering loop
            self.logger.info("SEARCHING LATEST RESULT of %s", mode_field)
            latest_result = dal.getLastRecipeResult("EMIR", "EMIR", mode_field)
            elements = latest_result['elements']
            accum_dither = elements[key_field]
            self.logger.info("FOUND")
        else:
            self.logger.info("NO ACCUMULATION")
            accum_dither = stareImages[0]

        newOR.naccum = naccum
        newOR.accum = accum_dither

        # obsres['obresult'] = newOR
        # print('Adding RI parameters ', obsres)
        # newRI = DitheredImageARecipeInput(**obsres)
        newRI = self.create_input(obresult=newOR)
        return newRI

    #@emirdrp.decorators.aggregate
    @emirdrp.decorators.loginfo
    def run(self, rinput):
        partial_result = self.run_single(rinput)
        new_result = self.aggregate_result(partial_result, rinput)
        return new_result

    def aggregate_result(self, partial_result, rinput):

        obresult = rinput.obresult
        # Check if this is our first run
        naccum = getattr(obresult, 'naccum', 0)
        accum = getattr(obresult, 'accum', None)

        frame = partial_result.frame

        if naccum == 0:
            self.logger.debug('naccum is not set, do not accumulate')
            return partial_result
        elif naccum == 1:
            self.logger.debug('round %d initialize accumulator', naccum)
            newaccum = frame
        elif naccum > 1:
            self.logger.debug('round %d of accumulation', naccum)
            newaccum = self.aggregate_frames(accum, frame, naccum)
        else:
            msg = 'naccum set to %d, invalid' % (naccum, )
            self.logger.error(msg)
            raise RecipeError(msg)

        # Update partial result
        partial_result.accum = newaccum

        return partial_result

    def aggregate_frames(self, accum, frame, naccum):
        return self.aggregate2(accum, frame, naccum)

    def run_single(self, rinput):

        # Open all images
        obresult = rinput.obresult

        data_hdul = []
        for f in obresult.frames:
            img = f.open()
            data_hdul.append(img)

        use_errors = True
        # Initial checks
        baseimg = data_hdul[0]
        has_num_ext = 'NUM' in baseimg
        has_bpm_ext = 'BPM' in baseimg
        baseshape = baseimg[0].shape
        subpixshape = baseshape
        base_header = baseimg[0].header
        compute_sky = 'NUM-SK' not in base_header
        compute_sky_advanced = False

        self.logger.debug('base image is: %s', self.datamodel.get_imgid(baseimg))
        self.logger.debug('images have NUM extension: %s', has_num_ext)
        self.logger.debug('images have BPM extension: %s', has_bpm_ext)
        self.logger.debug('compute sky is needed: %s', compute_sky)

        if compute_sky:
            sky_result = self.compute_sky_simple(data_hdul, use_errors=False)
            sky_data = sky_result[0].data
            self.logger.debug('sky image has shape %s', sky_data.shape)

            self.logger.info('sky correction in individual images')
            corrector = SkyCorrector(
                sky_data,
                self.datamodel,
                calibid=self.datamodel.get_imgid(sky_result)
            )
            # If we do not update keyword SKYADD
            # there is no sky subtraction
            for m in data_hdul:
                m[0].header['SKYADD'] = True
            # this is a little hackish
            data_hdul_s = [corrector(m) for m in data_hdul]
            # data_arr_s = [m[0].data - sky_data for m in data_hdul]
            base_header = data_hdul_s[0][0].header
        else:
            sky_result = None
            data_hdul_s = data_hdul

        self.logger.info('Computing offsets from WCS information')

        finalshape, offsetsp, refpix, offset_xy0 = self.compute_offset_wcs_imgs(
            data_hdul_s,
            baseshape,
            subpixshape
        )

        self.logger.debug("Relative offsetsp %s", offsetsp)
        self.logger.info('Shape of resized array is %s', finalshape)

        # Resizing target imgs
        data_arr_sr, regions = resize_arrays(
            [m[0].data for m in data_hdul_s],
            subpixshape,
            offsetsp,
            finalshape,
            fill=1
        )

        if self.intermediate_results:
            self.logger.debug('save resized intermediate img')
            for idx, arr_r in enumerate(data_arr_sr):
                self.save_intermediate_array(arr_r, 'interm_%s.fits' % idx)

        compute_cross_offsets = True
        if compute_cross_offsets:
            try:
                self.logger.debug("Compute cross-correlation of images")
                regions = self.compute_regions(finalshape, box=200, corners=True)

                offsets_xy_c = self.compute_offset_xy_crosscor_regions(
                    data_arr_sr, regions, refine=True, tol=1
                )
    #
                # Combined offsets
                # Offsets in numpy order, swaping
                offsets_xy_t = offset_xy0 - offsets_xy_c
                offsets_fc = offsets_xy_t[:, ::-1]
                offsets_fc_t = numpy.round(offsets_fc).astype('int')
                self.logger.debug('Total offsets: %s', offsets_xy_t)
                self.logger.info('Computing relative offsets from cross-corr')
                finalshape, offsetsp = combine_shape(subpixshape, offsets_fc_t)
    #
                self.logger.debug("Relative offsetsp (crosscorr) %s", offsetsp)
                self.logger.info('Shape of resized array (crosscorr) is %s', finalshape)

                # Resizing target imgs
                self.logger.debug("Resize to final offsets")
                data_arr_sr, regions = resize_arrays(
                    [m[0].data for m in data_hdul_s],
                    subpixshape,
                    offsetsp,
                    finalshape,
                    fill=1
                )

                if self.intermediate_results:
                    self.logger.debug('save resized intermediate2 img')
                    for idx, arr_r in enumerate(data_arr_sr):
                        self.save_intermediate_array(arr_r, 'interm2_%s.fits' % idx)

            except Exception as error:
                self.logger.warning('Error during cross-correlation, %s', error)

        if has_num_ext:
            self.logger.debug('Using NUM extension')
            masks = [numpy.where(m['NUM'].data, 0, 1).astype('int16') for m in data_hdul]
        elif has_bpm_ext:
            self.logger.debug('Using BPM extension')
            #
            masks = [numpy.where(m['BPM'].data, 1, 0).astype('int16') for m in data_hdul]
        else:
            self.logger.warning('BPM missing, use zeros instead')
            false_mask = numpy.zeros(baseshape, dtype='int16')
            masks = [false_mask for _ in data_arr_sr]

        self.logger.debug('resize bad pixel masks')
        mask_arr_r, _ = resize_arrays(masks, subpixshape, offsetsp, finalshape, fill=1)

        # Position of refpixel in final image
        refpix_final = refpix + offsetsp[0]
        self.logger.info('Position of refpixel in final image %s', refpix_final)

        self.logger.info('Combine target images (final)')
        method = combine.median
        out = method(data_arr_sr, masks=mask_arr_r, dtype='float32')

        self.logger.debug('create result image')
        hdu = fits.PrimaryHDU(out[0], header=base_header)
        self.logger.debug('update result header')
        hdr = hdu.header
        self.set_base_headers(hdr)

        hdr['TSUTC2'] = data_hdul[-1][0].header['TSUTC2']
        # Update obsmode in header

        hdu.header['history'] = "Combined %d images using '%s'" % (
            len(data_hdul),
            method.__name__
        )
        hdu.header['history'] = 'Combination time {}'.format(
            datetime.datetime.utcnow().isoformat()
        )
        # Update NUM-NCOM, sum of individual imagess
        ncom = 0
        for img in data_hdul:
            hdu.header['history'] = "Image {}".format(self.datamodel.get_imgid(img))
            ncom += img[0].header.get('NUM-NCOM', 1)
        hdr['NUM-NCOM'] = ncom
        # Update WCS, approximate solution
        hdr['CRPIX1'] += offsetsp[0][0]
        hdr['CRPIX2'] += offsetsp[0][1]

        #
        if use_errors:
            varhdu = fits.ImageHDU(out[1], name='VARIANCE')
            num = fits.ImageHDU(out[2], name='MAP')
            hdulist = fits.HDUList([hdu, varhdu, num])
        else:
            hdulist = fits.HDUList([hdu])

        result = self.create_result(frame=hdulist, sky=sky_result)
        self.logger.info('end of dither recipe')
        return result

    def set_base_headers(self, hdr):
        """Set metadata in FITS headers."""
        hdr = super(JoinDitheredImagesRecipe, self).set_base_headers(hdr)
        hdr['IMGOBBL'] = 0
        hdr['OBSMODE'] = 'DITHERED_IMAGE'
        return hdr

    def compute_offset_wcs_imgs(self, imgs, baseshape, subpixshape):

        refpix = numpy.divide(numpy.array([baseshape], dtype='int'), 2).astype('float')
        offsets_xy = offsets_from_wcs_imgs(imgs, refpix)
        self.logger.debug("offsets_xy %s", offsets_xy)
        # Offsets in numpy order, swaping
        offsets_fc = offsets_xy[:, ::-1]
        offsets_fc_t = numpy.round(offsets_fc).astype('int')

        self.logger.info('Computing relative offsets')
        finalshape, offsetsp = combine_shape(subpixshape, offsets_fc_t)

        return finalshape, offsetsp, refpix, offsets_xy

    def compute_offset_crosscor(self, arrs, region, subpixshape, refine=False):
        offsets_xy = offsets_from_crosscor(arrs, region, refine=refine, order='xy')
        self.logger.debug("offsets_xy cross-corr %s", offsets_xy)
        # Offsets in numpy order, swaping
        offsets_fc = offsets_xy[:, ::-1]
        offsets_fc_t = numpy.round(offsets_fc).astype('int')

        self.logger.info('Computing relative offsets from cross-corr')
        finalshape, offsetsp = combine_shape(subpixshape, offsets_fc_t)

        return finalshape, offsetsp, offsets_xy

    def compute_offset_xy_crosscor_regions(self, arrs, regions, refine=False, tol=0.5):
        offsets_xy = offsets_from_crosscor_regions(
            arrs, regions,
            refine=refine, order='xy', tol=tol
        )
        self.logger.debug("offsets_xy cross-corr %s", offsets_xy)
        # Offsets in numpy order, swaping
        return offsets_xy

    def compute_offset_crosscor_regions(self, arrs, regions, subpixshape, refine=False, tol=0.5):
        offsets_xy = offsets_from_crosscor_regions(
            arrs, regions,
            refine=refine, order='xy', tol=tol
        )
        self.logger.debug("offsets_xy cross-corr %s", offsets_xy)
        # Offsets in numpy order, swaping
        offsets_fc = offsets_xy[:, ::-1]
        offsets_fc_t = numpy.round(offsets_fc).astype('int')

        self.logger.info('Computing relative offsets from cross-corr')
        finalshape, offsetsp = combine_shape(subpixshape, offsets_fc_t)

        return finalshape, offsetsp, offsets_xy


    def compute_shapes_wcs(self, imgs):

        # Better near the center...
        shapes = [img[0].shape for img in imgs]
        ref_pix_xy_0 = (shapes[0][1] // 2, shapes[0][0] // 2)
        #
        ref_coor_xy = reference_pix_from_wcs_imgs(imgs, ref_pix_xy_0)
        # offsets_xy = offsets_from_wcs_imgs(imgs, numpy.asarray([ref_pix_xy_0]))
        # ll = [(-a[0]+ref_coor_xy[0][0], -a[1]+ref_coor_xy[0][1]) for a in ref_coor_xy]

        self.logger.debug("ref_coor_xy %s", ref_coor_xy)
        # Transform to pixels, integers
        ref_pix_xy = [coor_to_pix(c, order='xy') for c in ref_coor_xy]

        self.logger.info('Computing relative shapes')
        finalshape, partialshapes, finalpix_xy = combine_shapes(shapes, ref_pix_xy, order='xy')

        return finalshape, partialshapes, ref_pix_xy_0, finalpix_xy

    def compute_object_masks(self, data_arr_r, mask_arr_r, has_bpm_ext, regions, masks):

        method = combine.mean

        self.logger.info(
            "initial stacking, %d images, with offsets using '%s'",
            len(data_arr_r),
            method.__name__
        )
        data1 = method(data_arr_r, masks=mask_arr_r, dtype='float32')

        self.logger.info('obtain segmentation mask')
        segmap = segmentation_combined(data1[0])
        # submasks
        if not has_bpm_ext:
            omasks = [(segmap[region] > 0) for region in regions]
        else:
            omasks = [((segmap[region] > 0) & bpm) for region, bpm in zip(regions, masks)]

        return omasks

    def compute_sky_simple(self, data_hdul, use_errors=False):
        method = combine.median

        refimg = data_hdul[0]
        base_header = refimg[0].header
        self.logger.info('combine images with median')
        sky_data = method([m[0].data for m in data_hdul], dtype='float32')

        hdu = fits.PrimaryHDU(sky_data[0], header=base_header)

        self.logger.debug('update created sky image result header')
        skyid = str(uuid.uuid1())
        hdu.header['UUID'] = skyid
        hdu.header['history'] = "Combined {} images using '{}'".format(
            len(data_hdul),
            method.__name__
        )
        hdu.header['history'] = 'Combination time {}'.format(
            datetime.datetime.utcnow().isoformat()
        )
        for img in data_hdul:
            hdu.header['history'] = "Image {}".format(self.datamodel.get_imgid(img))

        if use_errors:
            varhdu = fits.ImageHDU(sky_data[1], name='VARIANCE')
            num = fits.ImageHDU(sky_data[2], name='MAP')
            sky_result = fits.HDUList([hdu, varhdu, num])
        else:
            sky_result = fits.HDUList([hdu])

        return sky_result

    def compute_sky_advanced(self, data_hdul, omasks, base_header, use_errors):
        method = combine.mean

        self.logger.info('recombine images with segmentation mask')
        sky_data = method([m[0].data for m in data_hdul], masks=omasks, dtype='float32')

        hdu = fits.PrimaryHDU(sky_data[0], header=base_header)
        points_no_data = (sky_data[2] == 0).sum()

        self.logger.debug('update created sky image result header')
        skyid = str(uuid.uuid1())
        hdu.header['UUID'] = skyid
        hdu.header['history'] = "Combined {} images using '{}'".format(
            len(data_hdul),
            method.__name__
        )
        hdu.header['history'] = 'Combination time {}'.format(
            datetime.datetime.utcnow().isoformat()
        )
        for img in data_hdul:
            hdu.header['history'] = "Image {}".format(self.datamodel.get_imgid(img))

        msg = "missing pixels, total: {}, fraction: {:3.1f}".format(
            points_no_data,
            points_no_data / sky_data[2].size
        )
        hdu.header['history'] = msg
        self.logger.debug(msg)

        if use_errors:
            varhdu = fits.ImageHDU(sky_data[1], name='VARIANCE')
            num = fits.ImageHDU(sky_data[2], name='MAP')
            sky_result = fits.HDUList([hdu, varhdu, num])
        else:
            sky_result = fits.HDUList([hdu])

        return sky_result

    def aggregate2(self, frame1, frame2, naccum):
        # FIXME, this is almost identical to run_single
        frames = [frame1, frame2]
        use_errors = True
        # Initial checks
        fframe = frames[0]
        img = fframe.open()
        base_header = img[0].header

        imgs = []
        for f in frames:
            img = f.open()
            imgs.append(img)

        self.logger.info('Computing offsets from WCS information')

        finalshape, partial_shapes, refpix_xy_0, refpix_final_xy = self.compute_shapes_wcs(imgs)

        self.logger.info('Shape of resized array is %s', finalshape)
        self.logger.debug("partial shapes %s", partial_shapes)

        masks = []
        self.logger.debug('Obtains masks')
        for img in imgs:
            if 'NUM' in img:
                self.logger.debug('Using NUM extension as mask')
                mask = numpy.where(img['NUM'].data, 0, 1).astype('int16')
            elif 'BPM' in img:
                self.logger.debug('Using BPM extension as mask')
                mask = numpy.where(img['BPM'].data, 1, 0).astype('int16')
            else:
                self.logger.warning('BPM missing, use zeros instead')
                mask = numpy.zeros_like(img[0].data)
            masks.append(mask)

        # Resizing target frames
        data_arr_r = resize_arrays_alt(
            [img[0].data for img in imgs],
            partial_shapes,
            finalshape,
            fill=1
        )

        self.logger.debug('resize bad pixel masks')
        mask_arr_r = resize_arrays_alt(masks, partial_shapes, finalshape, fill=1)

        self.logger.debug("not computing sky")
        data_arr_sr = data_arr_r

        self.logger.info('Combine target images (final, aggregate)')
        self.logger.debug("weights for 'accum' and 'frame'")

        weight_accum = 2 * (1 - 1.0 / naccum)
        weight_frame = 2.0 / naccum
        scales = [1.0 / weight_accum, 1.0 / weight_frame]
        self.logger.debug("weights for 'accum' and 'frame', %s", scales)
        method = combine.mean

        out = method(data_arr_sr, masks=mask_arr_r, scales=scales, dtype='float32')

        self.logger.debug('create result image')
        hdu = fits.PrimaryHDU(out[0], header=base_header)
        self.logger.debug('update result header')
        hdr = hdu.header
        self.set_base_headers(hdr)
        hdr['IMGOBBL'] = 0
        hdr['TSUTC2'] = imgs[-1][0].header['TSUTC2']
        # Update obsmode in header
        hdr['OBSMODE'] = 'DITHERED_IMAGE'
        hdu.header['history'] = "Combined %d images using '%s'" % (
            len(imgs),
            method.__name__
        )
        hdu.header['history'] = 'Combination time {}'.format(
            datetime.datetime.utcnow().isoformat()
        )
        # Update NUM-NCOM, sum of individual frames
        ncom = 0
        for img in imgs:
            hdu.header['history'] = "Image {}".format(self.datamodel.get_imgid(img))
            ncom += img[0].header['NUM-NCOM']

        hdr['NUM-NCOM'] = ncom
        # Update WCS, approximate solution
        hdr['CRPIX1'] += (refpix_final_xy[0] - refpix_xy_0[0])
        hdr['CRPIX2'] += (refpix_final_xy[1] - refpix_xy_0[1])

        #
        if use_errors:
            varhdu = fits.ImageHDU(out[1], name='VARIANCE')
            num = fits.ImageHDU(out[2], name='MAP')
            hdulist = fits.HDUList([hdu, varhdu, num])
        else:
            hdulist = fits.HDUList([hdu])

        return hdulist

    def compute_regions(self, finalshape, box=200, corners=True):
        regions = []
        # A square of 100x100 in the center of the image
        xref_cross = finalshape[1] // 2
        yref_cross = finalshape[0] // 2
        #
        self.logger.debug("Reference position is (x,y) %d  %d", xref_cross + 1, yref_cross + 1)
        self.logger.debug("Reference regions size is %d", 2 * box + 1)
        region = image_box2d(xref_cross, yref_cross, finalshape, (box, box))
        regions.append(region)
        # corners
        if corners:
            xref_c = finalshape[1] // 4
            yref_c = finalshape[0] // 4

            for xi in [xref_c, 3 * xref_c]:
                for yi in [yref_c, 3 * yref_c]:
                    self.logger.debug("Reference position is (x,y) %d  %d", xi + 1, yi + 1)
                    self.logger.debug("Reference regions size is %d", 2 * box + 1)
                    region = image_box2d(xi, yi, finalshape, (box, box))
                    regions.append(region)

        return regions