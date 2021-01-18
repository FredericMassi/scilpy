# coding=utf-8

import glob
import logging
import os
import re
import struct

import nibabel as nib
import numpy as np


def load_fdf(file_path):
    """
    Load a Varian FDF file.

    Parameters
    ----------
    file_path: Path to the fdf file or directory

    Return
    ------
    data: FDF raw data
    header: Dictionary of the fdf header info.
    """
    if os.path.isdir(file_path):
        data, header = read_directory(file_path)
        dir_path = file_path
    else:
        data, header = read_file(file_path)
        dir_path = os.path.dirname(os.path.abspath(file_path))

    procpar_path = os.path.join(dir_path, 'procpar')

    if os.path.exists(procpar_path):
        add_gradient_info(procpar_path, header)
    else:
        logging.warning('Could not find the procpar file {0}. \n'
                        'If you have gradient information to be extracted, '
                        'you need to have a procpar file in the fdf directory.'
                        .format(procpar_path))
    return data, header


def add_gradient_info(procpar_path, header):
    """
    Extract gradients information from the procpar file
    and puts it in the header.
    The X values of the gradient are flipped due a LAS encoding.

    Parameters
    ----------
    procpar_path: Path to the procpar file.
    header: Header to add gradient info.

    Return
    ------
    None
    """
    bval = diff_x = diff_y = diff_z = None

    with open(procpar_path, 'r') as procpar:
        for line in procpar:
            if line.startswith('bvalue'):
                bval = next(procpar)
            if line.startswith('dpe '):
                diff_x = next(procpar)
            if line.startswith('dro '):
                diff_y = next(procpar)
            if line.startswith('dsl '):
                diff_z = next(procpar)

    if bval and diff_x and diff_y and diff_z:
        header['bvalue'] = [float(x) for x in bval.split()[1:]]
        header['diff_x'] = [-float(x) for x in diff_x.split()[1:]]
        header['diff_y'] = [float(x) for x in diff_y.split()[1:]]
        header['diff_z'] = [float(x) for x in diff_z.split()[1:]]


def read_file(file_path):
    """
    Read a single fdf file.

    Parameters
    ----------
    path: Path to the fdf file

    Return
    ------
    raw_header: Dictionary of header information
    data: Numpy array of the fdf data
    """
    raw_header = dict()

    raw_header['shape'] = [-1, -1, 1, 1]
    raw_header['endian'] = '>'

    # Extracts floating point numbers
    # ex: 'float  roi[] = {3.840000,3.840000,0.035000};'
    # returns ['3.840000', '3.840000', '0.035000']
    float_regex = '[-+]?[0-9]*\.?[0-9]+'

    # Extracts value of a line of the type:
    # 'int    slice_no = 1;' would return '1'
    named_value_regex = '= *\"*(.*[^\"])\"* *;'

    # (tag_in_file, tag_in_header)
    find_values = (('echos', 'nechoes'),
                   ('echo_no', 'echo_no'),
                   ('nslices', 'nslices'),
                   ('slice_no', 'sl'),
                   ('bigendian', 'endian'),
                   ('array_dim', 'array_dim'),
                   ('bigendian', 'endian'),
                   ('studyid', 'studyid'))

    with open(file_path, 'rb') as fp:
        # Read entire file
        while True:
            line = fp.readline()
            line = line.decode()

            if line[0] == chr(12):
                break

            # Check line for tag, extract value with the regex then put it in
            # the header with the associated tag.
            for file_key, head_key in find_values:
                if line.find(file_key) > 0:
                    raw_header[head_key] = \
                        re.findall(named_value_regex, line)[0]
                    break

            if type(raw_header['endian']) is int:
                raw_header['endian'] = \
                    '>' if int(raw_header['endian']) != 0 else '<'

            if line.find('abscissa') > 0:
                # Extracts units in quotes.
                # ex: 'char  *abscissa[] = {"cm", "cm"}' returns
                # ["cm", "cm"]
                m = re.findall('\"[a-z]{2}\"', line.rstrip())

                unit = m[0].strip('"')

                # We convert everything in mm
                # Nifti doesn't support 'cm' anyway...
                if unit == 'cm':
                    unit = 'mm'

                raw_header['xyz_units'] = unit
                raw_header['t_units'] = 'unknown'

            elif line.find('roi') > 0:
                m = re.findall(float_regex, line.rstrip())
                raw_header['real_voxel_dim'] = \
                    np.array([float(x)*10 for x in m])

            elif line.find('orientation') > 0:
                m = re.findall(float_regex, line.rstrip())
                raw_header['orientation'] = np.array([float(x) for x in m])

            elif line.find('origin') > 0:
                m = re.findall(float_regex, line.rstrip())
                raw_header['origin'] = \
                    np.array([float(x) for x in m])

            elif line.find('matrix') > 0:
                # Extracts digits.
                # ex: 'float  matrix[] = {128, 128};'
                # returns ['128', '128']
                m = re.findall('(\d+)', line.rstrip())
                raw_header['shape'] = np.array([int(x) for x in m])

        # Total number of data pixels
        # nb_voxels = reduce(operator.mul, raw_header['shape'])
        nb_voxels = np.prod(raw_header['shape'])

        # Set how data is packed
        raw_header['fmt'] = "{}f".format(nb_voxels)
        if '<' in raw_header['endian']:
            raw_header['fmt'] = '<'+raw_header['fmt']
        else:
            raw_header['fmt'] = '>'+raw_header['fmt']

        # Go to the beginning of the data segment
        fp.seek(-nb_voxels * 4, 2)
        data = struct.unpack(raw_header['fmt'], fp.read(nb_voxels*4))

    # Get correct voxel dimensions in mm
    raw_header['voxel_dim'] = \
        [j/i for i, j in zip(raw_header['shape'],
                             raw_header['real_voxel_dim'])]

    correct_shape = raw_header['shape'][::-1]

    # Reshape the data according to image dimensions
    data = np.array(data).reshape(correct_shape).squeeze()

    if len(raw_header['shape']) != 2:
        data = data.transpose(2, 1, 0)

    data = np.rot90(data, 3)[:, ::-1]

    return raw_header, data


def read_directory(path):
    """
    Parameters
    ----------
    Read a directory containing multiple 2D ``.fdf`` files. The method
    should return ``None`` if the directory is empty.

    path: Path to the input directory

    Return
    ------
    data: Numpy array containing data
    final_header: Header information
    """
    files = glob.glob(os.path.join(path, '*.fdf'))
    files.sort()

    all_headers, all_data = zip(*[read_file(fl) for fl in files])

    if not all_headers:
        return None

    final_header = all_headers[0]

    # Fix data axis
    all_data = np.array(all_data)
    if len(all_data.shape) < 4:
        all_data = np.transpose(all_data, (1, 2, 0))

        # Set real shape
        final_header['shape'] = all_data.shape

        # Correct voxel dimensions to fit data shape
        if len(final_header['shape']) != len(final_header['voxel_dim']):
            final_header['voxel_dim'].extend(
                final_header['real_voxel_dim'][len(final_header['shape'])-1:])

        # Support for fourth dimension
        time = int(float(final_header['array_dim']))
        if time > 1:
            final_header['shape'] = (final_header['shape'][0],
                                     final_header['shape'][1],
                                     round(final_header['shape'][2] / time),
                                     time)

            final_header['voxel_dim'].append(1)

            all_data = all_data.reshape(final_header['shape'])
    else:
        all_data = np.transpose(all_data, (3, 2, 1, 0))
        all_data = np.transpose(all_data, (1, 0, 2, 3))
        final_header['shape'] = all_data.shape
        final_header['voxel_dim'].append(1.0)

    return all_data, final_header


def format_raw_header(header):
    """
    Format the header to a Nifti1Image format.

    Parameters
    ----------
    header: Raw dictionary of header information

    Return
    ------
    nifti1_header: Header to save in the nifti1 file.
    """
    if header is None:
        return header

    nifti1_header = nib.nifti1.Nifti1Header()
    nifti1_header.set_data_shape(header['shape'])
    nifti1_header.set_xyzt_units(header['xyz_units'], header['t_units'])
    nifti1_header.set_data_dtype('float32')

    return nifti1_header


def save_babel(out_path, data, raw_header, bval_path, bvec_path, affine=None):
    """
    Save a loaded fdf file to nifti.

    Parameters
    ----------
    out_path: Path of the nifti file to be saved
    data: Raw data to be saved
    raw_header: Raw header from fdf files
    bval_path: Path to the bval file to be saved
    bvec_path: Path to the bvec file to be saved
    affine: Affine transformation to save with the data

    Return
    ------
    None
    """
    nifti1_header = format_raw_header(raw_header)

    if 'orientation' in raw_header:
        orientation = np.identity(4)
        orientation[:3, :3] = raw_header['orientation'].reshape(3, 3)
        affine = np.linalg.inv(orientation)

    write_gradient_information(raw_header, bval_path, bvec_path)

    img = nib.nifti1.Nifti1Image(dataobj=data,
                                 header=nifti1_header,
                                 affine=affine)

    img.header.set_zooms(raw_header['voxel_dim'])

    qform = img.header.get_qform()

    qform[:2, :3] *= -1.

    if 'origin' in raw_header:
        qform[:len(raw_header['origin']), 3] = -raw_header['origin']

    img.get_header().set_qform(qform)

    img.update_header()

    img.to_filename(out_path)


def write_gradient_information(header, bval_path=None, bvec_path=None):
    """
    Write gradient information in present in the header.

    Parameters
    ----------
    header: The header with gradient info.
    bval_path: Path to the bval file to be saved.
    bvec_path: Path to the bvec path to be saved.

    Return
    ------
    None
    """
    if 'bvalue' in header and 'diff_x' in header and 'diff_y' in header and\
            'diff_z' in header:
        if bval_path:
            with open(bval_path, 'w') as bvals:
                bvals.write(' '.join(str(i) for i in header['bvalue']))

        if bvec_path:
            with open(bvec_path, 'w') as bvecs:
                bvecs.write(' '.join(str(i) for i in header['diff_x']) + '\n')
                bvecs.write(' '.join(str(i) for i in header['diff_y']) + '\n')
                bvecs.write(' '.join(str(i) for i in header['diff_z']))
