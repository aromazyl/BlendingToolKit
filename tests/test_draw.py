import os
import numpy as np
import pytest
import btk
import btk.config


def get_draw_generator(batch_size=8, cpus=1,
                       multiprocessing=False, add_noise=True):
    catalog_name = 'sample_input_catalog.fits'
    param = btk.config.Simulation_params(catalog_name, batch_size=batch_size,
                                         add_noise=add_noise)
    np.random.seed(param.seed)
    catalog = btk.get_input_catalog.load_catalog(param)
    blend_generator = btk.create_blend_generator.generate(param, catalog)
    observing_generator = btk.create_observing_generator.generate(param)
    draw_generator = btk.draw_blends.generate(param, blend_generator,
                                              observing_generator,
                                              multiprocessing=multiprocessing,
                                              cpus=cpus)
    return draw_generator


def match_blend_images_default(blend_images):
    """Compares the max value of blend image for each of the band along with
    the mean and std values in the batch. This is compared to the values
    measured a proiri for the default input settings.
    """
    test_batch_max = np.array([262.25805664, 2211.23046875, 8004.875,
                               10741.5, 9397.46875, 5418.3125])
    test_batch_mean = 7.3488463451244215
    test_batch_std = 486.0310945531883
    batch_max = blend_images.max(axis=0).max(axis=0).max(axis=0)
    batch_mean = blend_images.mean()
    batch_std = blend_images.std()
    np.testing.assert_array_almost_equal(
        batch_max, test_batch_max,
        err_msg="Did not get desired maximum pixel values of blend images")
    np.testing.assert_almost_equal(
        test_batch_mean, batch_mean,
        err_msg="Did not get desired mean pixel values of blend images")
    np.testing.assert_almost_equal(
        test_batch_std, batch_std,
        err_msg="Did not get desired std of pixel values of blend images")


def match_isolated_images_default(isolated_images):
    """Compares the max value of isoalted image for each of the band along with
    the mean and std values in the batch. This is compared to the values
    measured a proiri for the default input settings.
    """
    test_batch_max = np.array([203.60154724, 2072.32250977, 7771.61767578,
                               10532.68652344, 8669.31933594, 4871.0546875])
    test_batch_mean = 3.7485726507963544
    test_batch_std = 92.11482419872036
    batch_max = isolated_images.max(axis=0).max(axis=0).max(axis=0).max(axis=0)
    batch_mean = isolated_images.mean()
    batch_std = isolated_images.std()
    np.testing.assert_array_almost_equal(
        batch_max, test_batch_max,
        err_msg="Did not get desired maximum pixel values of isolated images")
    np.testing.assert_almost_equal(
        test_batch_mean, batch_mean,
        err_msg="Did not get desired mean pixel values of isolated images")
    np.testing.assert_almost_equal(
        test_batch_std, batch_std,
        err_msg="Did not get desired std of pixel values of isolated images")


@pytest.mark.timeout(5)
def test_default():
    default_draw_generator = get_draw_generator()
    draw_output = next(default_draw_generator)
    assert len(draw_output['blend_list']) == 8, "Default batch should return 8"
    assert len(draw_output['blend_list'][3]) < 3, "Default max_number should \
        generate 2 or 1 galaxies per blend."
    assert draw_output['obs_condition'][5][0].survey_name == 'LSST', "Default \
        observing survey is LSST."
    match_blend_images_default(draw_output['blend_images'])
    match_isolated_images_default(draw_output['isolated_images'])
    pass


@pytest.mark.timeout(15)
def test_multi_processing():
    b_size = 16
    cpus = os.cpu_count()
    parallel_im_gen = get_draw_generator(b_size, cpus, multiprocessing=True,
                                         add_noise=False)
    parallel_im = next(parallel_im_gen)
    serial_im_gen = get_draw_generator(b_size, cpus, multiprocessing=False,
                                       add_noise=False)
    serial_im = next(serial_im_gen)
    print(parallel_im['blend_images'].shape, serial_im['blend_images'].shape)
    np.testing.assert_array_equal(parallel_im['blend_images'],
                                  serial_im['blend_images'])
    np.testing.assert_array_equal(parallel_im['isolated_images'],
                                  serial_im['isolated_images'])
    pass