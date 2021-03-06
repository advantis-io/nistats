#!/usr/bin/env python
import os
import json
import numpy as np
import pandas as pd
from scipy.stats import norm
import scipy.linalg as spl
from numpy.testing import assert_almost_equal, assert_array_almost_equal
from nose.tools import assert_true, assert_equal, assert_raises
from nibabel import load, Nifti1Image
from nibabel.tmpdirs import InTemporaryDirectory
from nose import with_setup

from nistats.utils import (multiple_mahalanobis, z_score, multiple_fast_inverse,
                           positive_reciprocal, full_rank, _check_run_tables,
                           _check_and_load_tables, _check_list_length_match,
                           get_bids_files, parse_bids_filename,
                           get_design_from_fslmat)
from nilearn.datasets.tests import test_utils as tst


def test_full_rank():
    n, p = 10, 5
    X = np.random.randn(n, p)
    X_, _ = full_rank(X)
    assert_array_almost_equal(X, X_)
    X[:, -1] = X[:, :-1].sum(1)
    X_, cond = full_rank(X)
    assert_true(cond > 1.e10)
    assert_array_almost_equal(X, X_)


def test_z_score():
    p = np.random.rand(10)
    assert_array_almost_equal(norm.sf(z_score(p)), p)
    # check the numerical precision
    for p in [1.e-250, 1 - 1.e-16]:
        assert_array_almost_equal(z_score(p), norm.isf(p))
    assert_array_almost_equal(z_score(np.float32(1.e-100)), norm.isf(1.e-300))


def test_mahalanobis():
    n = 50
    x = np.random.rand(n) / n
    A = np.random.rand(n, n) / n
    A = np.dot(A.transpose(), A) + np.eye(n)
    mah = np.dot(x, np.dot(spl.inv(A), x))
    assert_almost_equal(mah, multiple_mahalanobis(x, A), decimal=1)


def test_mahalanobis2():
    n = 50
    x = np.random.randn(n, 3)
    Aa = np.zeros([n, n, 3])
    for i in range(3):
        A = np.random.randn(120, n)
        A = np.dot(A.T, A)
        Aa[:, :, i] = A
    i = np.random.randint(3)
    mah = np.dot(x[:, i], np.dot(spl.inv(Aa[:, :, i]), x[:, i]))
    f_mah = (multiple_mahalanobis(x, Aa))[i]
    assert_true(np.allclose(mah, f_mah))


def test_multiple_fast_inv():
    shape = (10, 20, 20)
    X = np.random.randn(shape[0], shape[1], shape[2])
    X_inv_ref = np.zeros(shape)
    for i in range(shape[0]):
        X[i] = np.dot(X[i], X[i].T)
        X_inv_ref[i] = spl.inv(X[i])
    X_inv = multiple_fast_inverse(X)
    assert_almost_equal(X_inv_ref, X_inv)


def test_pos_recipr():
    X = np.array([2, 1, -1, 0], dtype=np.int8)
    eX = np.array([0.5, 1, 0, 0])
    Y = positive_reciprocal(X)
    yield assert_array_almost_equal, Y, eX
    yield assert_equal, Y.dtype.type, np.float64
    X2 = X.reshape((2, 2))
    Y2 = positive_reciprocal(X2)
    yield assert_array_almost_equal, Y2, eX.reshape((2, 2))
    # check that lists have arrived
    XL = [0, 1, -1]
    yield assert_array_almost_equal, positive_reciprocal(XL), [0, 1, 0]
    # scalars
    yield assert_equal, positive_reciprocal(-1), 0
    yield assert_equal, positive_reciprocal(0), 0
    yield assert_equal, positive_reciprocal(2), 0.5


def test_img_table_checks():
    # check matching lengths
    assert_raises(ValueError, _check_list_length_match, [''] * 2, [''], "", "")
    # check tables type and that can be loaded
    assert_raises(ValueError, _check_and_load_tables, ['.csv', '.csv'], "")
    assert_raises(TypeError, _check_and_load_tables,
                  [np.array([0]), pd.DataFrame()], "")
    assert_raises(ValueError, _check_and_load_tables,
                  ['.csv', pd.DataFrame()], "")
    # check high level wrapper keeps behavior
    assert_raises(ValueError, _check_run_tables, [''] * 2, [''], "")
    assert_raises(ValueError, _check_run_tables, [''] * 2, ['.csv', '.csv'], "")
    assert_raises(TypeError, _check_run_tables, [''] * 2,
                  [np.array([0]), pd.DataFrame()], "")
    assert_raises(ValueError, _check_run_tables, [''] * 2,
                  ['.csv', pd.DataFrame()], "")


def write_fake_bold_img(file_path, shape, rk=3, affine=np.eye(4)):
    data = np.random.randn(*shape)
    data[1:-1, 1:-1, 1:-1] += 100
    Nifti1Image(data, affine).to_filename(file_path)
    return file_path


def basic_paradigm():
    conditions = ['c0', 'c0', 'c0', 'c1', 'c1', 'c1', 'c2', 'c2', 'c2']
    onsets = [30, 70, 100, 10, 30, 90, 30, 40, 60]
    events = pd.DataFrame({'trial_type': conditions,
                             'onset': onsets})
    return events


def basic_confounds(length):
    columns = ['RotX', 'RotY', 'RotZ', 'X', 'Y', 'Z']
    data = np.random.rand(length, 6)
    confounds = pd.DataFrame(data, columns=columns)
    return confounds


def create_fake_bids_dataset(base_dir='', n_sub=10, n_ses=2,
                             tasks=['localizer', 'main'],
                             n_runs=[1, 3], with_derivatives=True,
                             with_confounds=True, no_session=False):
    """Returns a fake bids dataset directory with dummy files

    In the case derivatives are included, they come with two spaces and
    variants. Spaces are 'MNI' and 'T1w'. Variants are 'some' and 'other'.
    Only space 'T1w' include both variants.

    Specifying no_sessions will only produce runs and files without the
    optional session field. In this case n_ses will be ignored.
    """
    bids_path = os.path.join(base_dir, 'bids_dataset')
    os.makedirs(bids_path)
    # Create surface bids dataset
    open(os.path.join(bids_path, 'README.txt'), 'w')
    vox = 4
    created_sessions = ['ses-%02d' % label for label in range(1, n_ses + 1)]
    if no_session:
        created_sessions = ['']
    for subject in ['sub-%02d' % label for label in range(1, n_sub + 1)]:
        for session in created_sessions:
            subses_dir = os.path.join(bids_path, subject, session)
            if session == 'ses-01' or session == '':
                anat_path = os.path.join(subses_dir, 'anat')
                os.makedirs(anat_path)
                anat_file = os.path.join(anat_path, subject + '_T1w.nii.gz')
                open(anat_file, 'w')
            func_path = os.path.join(subses_dir, 'func')
            os.makedirs(func_path)
            for task, n_run in zip(tasks, n_runs):
                for run in ['run-%02d' % label for label in range(1, n_run + 1)]:
                    fields = [subject, session, 'task-' + task]
                    if '' in fields:
                        fields.remove('')
                    file_id = '_'.join(fields)
                    if n_run > 1:
                        file_id += '_' + run
                    bold_path = os.path.join(func_path, file_id + '_bold.nii.gz')
                    write_fake_bold_img(bold_path, [vox, vox, vox, 100])
                    events_path = os.path.join(func_path, file_id +
                                               '_events.tsv')
                    basic_paradigm().to_csv(events_path, sep='\t', index=None)
                    param_path = os.path.join(func_path, file_id +
                                              '_bold.json')
                    with open(param_path, 'w') as param_file:
                        json.dump({'RepetitionTime': 1.5}, param_file)

    # Create derivatives files
    if with_derivatives:
        bids_path = os.path.join(base_dir, 'bids_dataset', 'derivatives')
        os.makedirs(bids_path)
        for subject in ['sub-%02d' % label for label in range(1, 11)]:
            for session in created_sessions:
                subses_dir = os.path.join(bids_path, subject, session)
                func_path = os.path.join(subses_dir, 'func')
                os.makedirs(func_path)
                for task, n_run in zip(tasks, n_runs):
                    for run in ['run-%02d' % label for label in range(1, n_run + 1)]:
                        fields = [subject, session, 'task-' + task]
                        if '' in fields:
                            fields.remove('')
                        file_id = '_'.join(fields)
                        if n_run > 1:
                            file_id += '_' + run
                        preproc = file_id + '_bold_space-MNI_variant-some_preproc.nii.gz'
                        preproc_path = os.path.join(func_path, preproc)
                        write_fake_bold_img(preproc_path, [vox, vox, vox, 100])
                        preproc = file_id + '_bold_space-T1w_variant-some_preproc.nii.gz'
                        preproc_path = os.path.join(func_path, preproc)
                        write_fake_bold_img(preproc_path, [vox, vox, vox, 100])
                        preproc = file_id + '_bold_space-T1w_variant-other_preproc.nii.gz'
                        preproc_path = os.path.join(func_path, preproc)
                        write_fake_bold_img(preproc_path, [vox, vox, vox, 100])
                        if with_confounds:
                            confounds_path = os.path.join(func_path, file_id +
                                                          '_confounds.tsv')
                            basic_confounds(100).to_csv(confounds_path,
                                                        sep='\t', index=None)
    return 'bids_dataset'


def test_get_bids_files():
    with InTemporaryDirectory():
        bids_path = create_fake_bids_dataset(n_sub=10, n_ses=2,
                                             tasks=['localizer', 'main'],
                                             n_runs=[1, 3])
        # For each possible possible option of file selection we check
        # that we recover the appropriate amount of files, as included
        # in the fake bids dataset.

        # 250 files in total related to subject images. Top level files like
        # README not included
        selection = get_bids_files(bids_path)
        assert_true(len(selection) == 250)
        # 160 bold files expected. .nii and .json files
        selection = get_bids_files(bids_path, file_tag='bold')
        assert_true(len(selection) == 160)
        # Only 90 files are nii.gz. Bold and T1w files.
        selection = get_bids_files(bids_path, file_type='nii.gz')
        assert_true(len(selection) == 90)
        # Only 25 files correspond to subject 01
        selection = get_bids_files(bids_path, sub_label='01')
        assert_true(len(selection) == 25)
        # There are only 10 files in anat folders. One T1w per subject.
        selection = get_bids_files(bids_path, modality_folder='anat')
        assert_true(len(selection) == 10)
        # 20 files corresponding to run 1 of session 2 of main task.
        # 10 bold.nii.gz and 10 bold.json files. (10 subjects)
        filters = [('task', 'main'), ('run', '01'), ('ses', '02')]
        selection = get_bids_files(bids_path, file_tag='bold', filters=filters)
        assert_true(len(selection) == 20)
        # Get Top level folder files. Only 1 in this case, the README file.
        selection = get_bids_files(bids_path, sub_folder=False)
        assert_true(len(selection) == 1)


def test_parse_bids_filename():
    fields = ['sub', 'ses', 'task', 'lolo']
    labels = ['01', '01', 'langloc', 'lala']
    file_name = 'sub-01_ses-01_task-langloc_lolo-lala_bold.nii.gz'
    file_path = os.path.join('dataset', 'sub-01', 'ses-01', 'func', file_name)
    file_dict = parse_bids_filename(file_path)
    for fidx, field in enumerate(fields):
        assert_true(file_dict[field] == labels[fidx])
    assert_true(file_dict['file_type'] == 'nii.gz')
    assert_true(file_dict['file_tag'] == 'bold')
    assert_true(file_dict['file_path'] == file_path)
    assert_true(file_dict['file_basename'] == file_name)
    assert_true(file_dict['file_fields'] == fields)


@with_setup(tst.setup_tmpdata, tst.teardown_tmpdata)
def test_get_design_from_fslmat():
    fsl_mat_path = os.path.join(tst.tmpdir, 'fsl_mat.txt')
    matrix = np.ones((5, 5))
    with open(fsl_mat_path, 'w') as fsl_mat:
        fsl_mat.write('/Matrix\n')
        for row in matrix:
            for val in row:
                fsl_mat.write(str(val) + '\t')
            fsl_mat.write('\n')
    design_matrix = get_design_from_fslmat(fsl_mat_path)
    assert_true(design_matrix.shape == matrix.shape)
