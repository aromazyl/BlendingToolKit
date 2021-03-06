"""Contains functions to perform detection, deblending and measurement
    on images.
"""
from btk import measure
import btk.create_blend_generator
import numpy as np
import astropy.table
import skimage.feature


class SEP_params(measure.Measurement_params):
    """Class to perform detection and deblending with SEP"""

    def get_centers(self, image):
        """Return centers detected when object detection and photometry
        is done on input image with SEP.
        Args:
            image: Image (single band) of galaxy to perform measurement on.
        Returns:
                centers: x and y coordinates of detected  centroids

        """
        sep = __import__('sep')
        bkg = sep.Background(image)
        self.catalog, self.segmentation = sep.extract(
            image, 1.5, err=bkg.globalrms, segmentation_map=True)
        centers = np.stack((self.catalog['x'], self.catalog['y']), axis=1)
        return centers

    def get_deblended_images(self, data, index):
        """Returns scarlet modeled blend  and centers for the given blend"""
        image = np.mean(data['blend_images'][index], axis=2)
        peaks = self.get_centers(image)
        return {'deblend_image': None, 'peaks': peaks}


class Stack_params(measure.Measurement_params):
    """Class with functions that describe how LSST science pipeline can
    perform measurements on the input data."""
    min_pix = 1  # Minimum size in pixels to be considered a source
    bkg_bin_size = 32  # Binning size of the local background
    thr_value = 5  # SNR threshold for the detection
    psf_stamp_size = 41  # size of pstamp to draw PSF on

    def get_psf_sky(self, obs_cond):
        """Returns postage stamp image of the PSF and mean background sky
        level value saved in the input obs_cond class
        Args:
            obs_cond:`descwl.survey.Survey` class describing observing
                      conditions.

        """
        mean_sky_level = obs_cond.mean_sky_level
        psf = obs_cond.psf_model
        psf_image = psf.drawImage(
            scale=obs_cond.pixel_scale,
            nx=self.psf_stamp_size,
            ny=self.psf_stamp_size).array
        return psf_image, mean_sky_level

    def make_measurement(self, data, index):
        """Perform detection, deblending and measurement on the i band image of
        the blend for input index entry in the batch.

        Args:
            data: Dictionary with blend images, isolated object images, blend
                  catalog, and observing conditions.
            index: Position of the blend to measure in the batch.

        Returns:
            astropy.Table of the measurement results.
         """
        image_array = data['blend_images'][index, :, :, 3].astype(np.float32)
        psf_image, mean_sky_level = self.get_psf_sky(
            data['obs_condition'][index][3])
        variance_array = image_array + mean_sky_level
        psf_array = psf_image.astype(np.float64)
        cat = run_stack(image_array, variance_array, psf_array,
                        min_pix=self.min_pix, bkg_bin_size=self.bkg_bin_size,
                        thr_value=self.thr_value)
        cat_chldrn = cat[cat['deblend_nChild'] == 0]
        cat_chldrn = cat_chldrn.copy(deep=True)
        return cat_chldrn.asAstropy()

    def get_deblended_images(self, data=None, index=None):
        return None


def run_stack(image_array, variance_array, psf_array,
              min_pix=1, bkg_bin_size=32, thr_value=5):
    """
    Function to setup the DM stack and perform detection, deblending and
    measurement
    Args:
        image_array: Numpy array of image to run stack on
        variance_array: per pixel variance of the input image_array (must
                        have same dimensions as image_array)
        psf_array: Image of the PSF for image_array.
        min_pix: Minimum size in pixels of a source to be considered by the
                 stack (default=1).
        bkg_bin_size: Binning of the local background in pixels (default=32).
        thr_value: SNR threshold for the detected sources to be included in the
                   final catalog(default=5).
    Returns:
        catalog: AstroPy table of detected sources
    """
    # Convert to stack Image object
    import lsst.afw.table
    import lsst.afw.image
    import lsst.afw.math
    import lsst.meas.algorithms
    import lsst.meas.base
    import lsst.meas.deblender
    import lsst.meas.extensions.shapeHSM
    image = lsst.afw.image.ImageF(image_array)
    variance = lsst.afw.image.ImageF(variance_array)
    # Generate a masked image, i.e., an image+mask+variance image (mask=None)
    masked_image = lsst.afw.image.MaskedImageF(image, None, variance)
    # Create the kernel in the stack's format
    psf_im = lsst.afw.image.ImageD(psf_array)
    fkernel = lsst.afw.math.FixedKernel(psf_im)
    psf = lsst.meas.algorithms.KernelPsf(fkernel)
    # Passing the image to the stack
    exposure = lsst.afw.image.ExposureF(masked_image)
    # Assign the exposure the PSF that we created
    exposure.setPsf(psf)
    schema = lsst.afw.table.SourceTable.makeMinimalSchema()
    config1 = lsst.meas.algorithms.SourceDetectionConfig()
    # Tweaks in the configuration that can improve detection
    # Change carefully!
    #####
    config1.tempLocalBackground.binSize = bkg_bin_size
    config1.minPixels = min_pix
    config1.thresholdValue = thr_value
    #####
    detect = lsst.meas.algorithms.SourceDetectionTask(schema=schema,
                                                      config=config1)
    deblend = lsst.meas.deblender.SourceDeblendTask(schema=schema)
    config1 = lsst.meas.base.SingleFrameMeasurementConfig()
    config1.plugins.names.add('ext_shapeHSM_HsmShapeRegauss')
    config1.plugins.names.add('ext_shapeHSM_HsmSourceMoments')
    config1.plugins.names.add('ext_shapeHSM_HsmPsfMoments')
    measure = lsst.meas.base.SingleFrameMeasurementTask(schema=schema,
                                                        config=config1)
    table = lsst.afw.table.SourceTable.make(schema)
    detect_result = detect.run(table, exposure)  # run detection task
    catalog = detect_result.sources
    deblend.run(exposure, catalog)  # run the deblending task
    measure.run(catalog, exposure)  # run the measuring task
    catalog = catalog.copy(deep=True)
    return catalog


class Scarlet_params(measure.Measurement_params):
    """Class with functions that describe how scarlet should deblend images in
    the input data"""
    iters = 200  # Maximum number of iterations for scarlet to run
    e_rel = .015  # Relative error for convergence
    detect_centers = True

    def make_measurement(self, data=None, index=None):
        return None

    def get_centers(self, image):
        """Returns centers from SEP detection on the band averaged mean of the
        input image.

        Args:
            image: Numpy array of multi-band image to run scarlet on
                    [Number of bands, height, width].

        Returns:
            Array of x and y coordinate of centroids of objects in the image.
        """
        sep = __import__('sep')
        detect = image.mean(axis=0)  # simple average for detection
        bkg = sep.Background(detect)
        catalog = sep.extract(detect, 1.5, err=bkg.globalrms)
        return np.stack((catalog['x'], catalog['y']), axis=1)

    def scarlet_initialize(self, images, peaks,
                           bg_rms, iters, e_rel):
        """ Initializes scarlet ExtendedSource at locations specified as
        peaks in the (multi-band) input images.
        Args:
            images: Numpy array of multi-band image to run scarlet on
                    [Number of bands, height, width].
            peaks: Array of x and y coordinate of centroids of objects in
                   the image [number of sources, 2].
            bg_rms: Background RMS value of the images [Number of bands]

        Returns:
            blend: scarlet.Blend object for the initialized sources
            rejected_sources: list of sources (if any) that scarlet was
                              unable to initialize the image with.
        """
        scarlet = __import__("scarlet")
        sources, rejected_sources = [], []
        for n, peak in enumerate(peaks):
            try:
                result = scarlet.ExtendedSource(
                    (peak[1], peak[0]),
                    images,
                    bg_rms)
                sources.append(result)
            except scarlet.source.SourceInitError:
                rejected_sources.append(n)
                print("No flux in peak {0} at {1}".format(n, peak))
        blend = scarlet.Blend(sources).set_data(images, bg_rms=bg_rms)
        blend.fit(iters, e_rel=e_rel)
        return blend, rejected_sources

    def get_deblended_images(self, data, index):
        """
        Deblend input images with scarlet
        Args:
            images: Numpy array of multi-band image to run scarlet on
                   [Number of bands, height, width].
            peaks: x and y coordinate of centroids of objects in the image.
                   [number of sources, 2]
            bg_rms: Background RMS value of the images [Number of bands]
            iters: Maximum number of iterations if scarlet doesn't converge
                   (Default: 200).
            e_rel: Relative error for convergence (Default: 0.015)

        Returns:
            blend: scarlet.Blend object for the initialized sources
            rejected_sources: list of sources (if any) that scarlet was
            unable to initialize the image with.
        """
        images = np.transpose(data['blend_images'][index], axes=(2, 0, 1))
        blend_cat = data['blend_list'][index]
        if self.detect_centers:
            peaks = self.get_centers(images)
        else:
            peaks = np.stack((blend_cat['dx'], blend_cat['dy']), axis=1)
        bg_rms = np.array(
            [data['obs_condition'][index][i].mean_sky_level**0.5 for i in range(len(images))])
        blend, rejected_sources = self.scarlet_initialize(images, peaks,
                                                          bg_rms, self.iters,
                                                          self.e_rel)
        im, selected_peaks = [], []
        for m in range(len(blend.sources)):
            im .append(np.transpose(blend.get_model(k=m), axes=(1, 2, 0)))
            selected_peaks.append(
                [blend.components[m].center[1], blend.components[m].center[0]])
        return {'deblend_image': np.array(im), 'peaks': selected_peaks}


def make_true_seg_map(image, threshold):
    """Returns a boolean segmentation map corresponding to pixels in
    image above a certain threshold value.threshold
    Args:
        image: Image to estimate segmentation map of
        threshold: Pixels above this threshold are marked as belonging to
                   segmentation map

    Returns:
        Boolean segmentation map of the image
    """
    seg_map = np.zeros_like(image)
    seg_map[image < threshold] = 0
    seg_map[image >= threshold] = 1
    return seg_map.astype(np.bool)


def basic_selection_function(catalog):
    """Apply selection cuts to the input catalog.

    Only galaxies that satisfy the below criteria are returned:
    1) i band magnitude less than 27
    2) Second moment size is less than 3 arcsec.
    Second moments size (r_sec) computed as described in A1 of Chang et.al 2012

    Args:
        catalog: CatSim-like catalog from which to sample galaxies.

    Returns:
        CatSim-like catalog after applying selection cuts.
    """
    f = catalog['fluxnorm_bulge']/(catalog['fluxnorm_disk']+catalog['fluxnorm_bulge'])
    r_sec = np.hypot(catalog['a_d']*(1-f)**0.5*4.66,
                     catalog['a_b']*f**0.5*1.46)
    q, = np.where((r_sec <= 4) & (catalog['i_ab'] <= 27))
    return catalog[q]


def basic_sampling_function(Args, catalog):
    """Randomly picks entries from input catalog that are brighter than 25.3
    mag in the i band. The centers are randomly distributed within 1/5 of the
    stamp size.
    At least one bright galaxy (i<=24) is always selected.
    """
    number_of_objects = np.random.randint(0, Args.max_number)
    a = np.hypot(catalog['a_d'], catalog['a_b'])
    cond = (a <= 2) & (a > 0.2)
    q_bright, = np.where(cond & (catalog['i_ab'] <= 24))
    if np.random.random() >= 0.9:
        q, = np.where(cond & (catalog['i_ab'] < 28))
    else:
        q, = np.where(cond & (catalog['i_ab'] <= 25.3))
    blend_catalog = astropy.table.vstack(
        [catalog[np.random.choice(q_bright, size=1)],
         catalog[np.random.choice(q, size=number_of_objects)]])
    blend_catalog['ra'], blend_catalog['dec'] = 0., 0.
    # keep number density of objects constant
    maxshift = Args.stamp_size/30.*number_of_objects**0.5
    dx, dy = btk.create_blend_generator.get_random_center_shift(
        Args, number_of_objects + 1, maxshift=maxshift)
    blend_catalog['ra'] += dx
    blend_catalog['dec'] += dy
    return blend_catalog


def group_sampling_function(Args, catalog):
    """Blends are defined from *groups* of galaxies from the CatSim
    catalog previously analyzed with WLD.

    The group is centered on the middle of the postage stamp.
    Function only draws galaxies that lie within the postage stamp size
    determined in Args.

    Note: the pre-run WLD images are not used here. We only use the pre-run
    catalog (in i band) to identify galaxies that belong to a group.
    """
    if not hasattr(Args, 'wld_catalog_name'):
        raise Exception("A pre-run WLD catalog  name should be input as "
                        "Args.wld_catalog_name")
    else:
        wld_catalog = astropy.table.Table.read(Args.wld_catalog_name,
                                               format='fits')
    # randomly sample a group.
    group_ids = np.unique(wld_catalog['grp_id'][wld_catalog['grp_size'] >= 2])
    group_id = np.random.choice(group_ids, replace=False)
    # get all galaxies belonging to the group.
    ids = wld_catalog['db_id'][wld_catalog['grp_id'] == group_id]
    blend_catalog = astropy.table.vstack(
        [catalog[catalog['galtileid'] == i] for i in ids])
    # Set mean x and y coordinates of the group galaxies to the center of the
    # postage stamp.
    blend_catalog['ra'] -= np.mean(blend_catalog['ra'])
    blend_catalog['dec'] -= np.mean(blend_catalog['dec'])
    # convert ra dec from degrees to arcsec
    blend_catalog['ra'] *= 3600
    blend_catalog['dec'] *= 3600
    # Add small random shift so that center does not perfectly align with
    # the stamp center
    dx, dy = btk.create_blend_generator.get_random_center_shift(
        Args, 1, maxshift=3 * Args.pixel_scale)
    blend_catalog['ra'] += dx
    blend_catalog['dec'] += dy
    # make sure galaxy centers don't lie too close to edge
    cond1 = np.abs(blend_catalog['ra']) < Args.stamp_size / 2. - 3
    cond2 = np.abs(blend_catalog['dec']) < Args.stamp_size / 2. - 3
    no_boundary = blend_catalog[cond1 & cond2]
    if len(no_boundary) == 0:
        return no_boundary
    # make sure number of galaxies in blend is less than Args.max_number
    # randomly select max_number of objects if larger.
    num = min([len(no_boundary), Args.max_number])
    select = np.random.choice(range(len(no_boundary)), num, replace=False)
    return no_boundary[select]


class Basic_measure_params(measure.Measurement_params):
    """Class to perform detection and deblending with SEP"""

    def get_centers(self, image):
        """Return centers detected when object detection and photometry
        is done on input image with SEP.
        Args:
            image: Image (single band) of galaxy to perform measurement on.

        Returns:
                centers: x and y coordinates of detected  centroids
        """
        # set detection threshold to 5 times std of image
        threshold = 5*np.std(image)
        coordinates = skimage.feature.peak_local_max(image, min_distance=2,
                                                     threshold_abs=threshold)
        return np.stack((coordinates[:, 1], coordinates[:, 0]), axis=1)

    def get_deblended_images(self, data, index):
        """Returns scarlet modeled blend  and centers for the given blend"""
        image = np.mean(data['blend_images'][index], axis=2)
        peaks = self.get_centers(image)
        return {'deblend_image': None, 'peaks': peaks}


class Basic_metric_params(btk.compute_metrics.Metrics_params):
    def __init__(self, *args, **kwargs):
        super(Basic_metric_params, self).__init__(*args, **kwargs)
        """Class describing functions to return results of
         detection/deblending/measurement algorithm in meas_generator. Each
         blend results yielded by the meas_generator for a batch.
    """

    def get_detections(self):
        """Returns blend catalog and detection catalog for detction performed

        Returns:
            Results of the detection algorithm are returned as:
                true_tables: List of astropy Table of the blend catalogs of the
                    batch. Length of tables must be the batch size. x and y
                    coordinate values must be under columns named 'dx' and 'dy'
                    respectively, in pixels from bottom left corner as (0, 0).
                detected_tables: List of astropy Table of output from detection
                    algorithm. Length of tables must be the batch size. x and y
                    coordinate values must be under columns named 'dx' and 'dy'
                    respectively, in pixels from bottom left corner as (0, 0).
        """
        # Astropy table with entries corresponding to true sources
        blend_op, deblend_op, _ = next(self.meas_generator)
        true_tables = blend_op['blend_list']
        detected_tables = []
        for i in range(len(true_tables)):
            detected_centers = deblend_op[i]['peaks']
            detected_table = astropy.table.Table(detected_centers,
                                                 names=['dx', 'dy'])
            detected_tables.append(detected_table)
        return true_tables, detected_tables


class Stack_metric_params(btk.compute_metrics.Metrics_params):
    def __init__(self, *args, **kwargs):
        super(Stack_metric_params, self).__init__(*args, **kwargs)
        """Class describing functions to return results of
         detection/deblending/measurement algorithm in meas_generator. Each
         blend results yielded by the meas_generator for a batch.
    """

    def get_detections(self):
        """Returns blend catalog and detection catalog for detection performed

        Returns:
            Results of the detection algorithm are returned as:
                true_tables: List of astropy Table of the blend catalogs of the
                    batch. Length of tables must be the batch size. x and y
                    coordinate values must be under columns named 'dx' and 'dy'
                    respectively, in pixels from bottom left corner as (0, 0).
                detected_tables: List of astropy Table of output from detection
                    algorithm. Length of tables must be the batch size. x and y
                    coordinate values must be under columns named 'dx' and 'dy'
                    respectively, in pixels from bottom left corner as (0, 0).
        """
        # Astropy table with entries corresponding to true sources
        blend_op, _, cat = next(self.meas_generator)
        true_tables = blend_op['blend_list']
        detected_tables = []
        for i in range(len(true_tables)):
            detected_centers = np.stack(
                [cat[i]['base_NaiveCentroid_x'],
                 cat[i]['base_NaiveCentroid_y']],
                axis=1)
            detected_table = astropy.table.Table(detected_centers,
                                                 names=['dx', 'dy'])
            detected_tables.append(detected_table)
        return true_tables, detected_tables


def get_detection_eff_matrix(summary_table, num):
    """Computes the detection efficiency table for input detection summary
    table.

    Input argument num sets the maximum number of true detections for which the
    detection efficiency matrix is to be created for. Detection efficiency is
    computed for number of true objects in the range (1-num).

    Args:
        summary(`numpy.array`) : Detection summary as a table [N, 5].
        num(int): Maximum number of true objects to create matrix for. Number
            of columns in matrix will be num-1.

    Returns:
        numpy.ndarray of size[num+2, num-1] that shows detection efficiency.
    """
    eff_matrix = np.zeros((num + 2, num + 1))
    for i in range(0, num + 1):
        q_true, = np.where(summary_table[:, 0] == i)
        for j in range(0, num + 2):
            if len(q_true) > 0:
                q_det, = np.where(summary_table[q_true, 1] == j)
                eff_matrix[j, i] = len(q_det)
    norm = np.sum(eff_matrix, axis=0)
    # If not detections along a column, set sum to 1 to avoid dividing by zero.
    norm[norm == 0.] = 1
    # normalize over columns.
    eff_matrix = eff_matrix / norm[np.newaxis, :] * 100.
    return eff_matrix
