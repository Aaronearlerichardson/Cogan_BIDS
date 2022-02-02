#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import argparse
import datetime
import gc
import gzip
import json
import os.path as op
import subprocess
import sys
from typing import Union, List, Dict, Tuple, Any, Optional

import nibabel as nib
import pydicom as dicom
import pyedflib
from bids import layout
from matgrab import mat2df
from pyedflib import highlevel

from utils import *


def get_parser():  # parses flags at onset of command
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description="""
        Data2bids is a script based on the SIMEXP lab script to convert nifti
        MRI files into BIDS format. This script has been modified to
        also parse README data as well as include conversion of DICOM files to
        nifti. The script utilizes Chris Rorden's Dcm2niix program for
        actual conversion.

        This script takes one of two formats for conversion. The first is a se
        ries of DICOM files in sequence with an optional \"medata\" folder whi
        ch contains any number of single or multi-echo uncompressed nifti file
        s (.nii). Note that nifti files in this case must also have a correspo
        nding DICOM scan run, but not necessarily scan echo (for example, one
        DICOM scan for run 5 but three nifti files which are echoes 1, 2, and
        3 of run 5). The other format is a series of nifti files and a README.
        txt file formatted the same way as it is in the example. Both formats
        are shown in the examples folder.

        Both formats use a .json config file that maps either DICOM tags or te
        xt within the nifti file name to BIDS metadata. The syntax and formatt
        ing of this .json file can be found here
        https://github.com/SIMEXP/Data2Bids#heuristic.

        The only thing this script does not account for is event files. If you
        have the 1D files that's taken care of, but chances are you have some
        other format. If this is the case, I recommend
        https://bids-specification.readthedocs.io/en/stable/04-modality-specif
        ic-files/05-task-events.html so that you can make BIDS compliant event
        files.

        Data2bids documentation at https://github.com/SIMEXP/Data2Bids
        Dcm2niix documentation at https://github.com/rordenlab/dcm2niix""",
        epilog="""
        Made by Aaron Earle-Richardson (ae166@duke.edu)
        """)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-i", "--input_dir", required=False, default=None,
                       help="""
        Input data directory(ies), must include a readme.txt file formatted lik
        e example under examples folder. Mutually exclusive with DICOM director
        y option. Default: current directory
        """, )

    parser.add_argument("-c", "--config", required=False, default=None,
                        help="JSON configuration file (see https://github.com/"
                             "SIMEXP/Data2Bids/blob/master/example/config.json"
                             ")")

    parser.add_argument("-o", "--output_dir", required=False, default=None,
                        help="Output BIDS directory, Default: Inside current "
                             "directory ")

    group.add_argument("-d", "--DICOM_path", default=None, required=False,
                       help="Optional DICOM directory, Mutually exclusive with"
                            " input directory option")

    parser.add_argument("-m", "--multi_echo", nargs='*', type=int,
                        required=False, help="""
        indicator of multi-echo dataset. Only necessary if NOT converting DICOM
        s. For example, if runs 3-6 were all multi-echo then the flag
        should look like: -m 3 4 5 6 . Additionally, the -m flag may be called
        by itself if you wish to let data2bids auto-detect multi echo data,
        but it will not be able to tell you if there is a mistake.""")

    parser.add_argument("-ow", "--overwrite", required=False,
                        action='store_true',
                        help="overwrite preexisting BIDS file structures in de"
                             "stination location")

    parser.add_argument("-ch", "--channels", nargs='*', required=False,
                        help="""
                        Indicator of channels to keep from edf files.
                        """)

    parser.add_argument("-s", "--stim_dir", required=False, default=None,
                        help="directory containing stimuli files", )

    parser.add_argument("-v", "--verbose", required=False, action='store_true',
                        help="verbosity", )

    return parser


def get_trigger(part_match: str, headers_dict: dict) -> str:
    if part_match in headers_dict.keys():
        trig_lab = headers_dict[part_match]
    else:
        trig_lab = headers_dict["default"]
    if ".xlsx" in trig_lab:
        trig_lab = trigger_from_excel(str(trig_lab), part_match)
    return trig_lab


class Data2Bids:  # main conversion and file organization program

    def __init__(self, input_dir=None, config=None, output_dir=None,
                 DICOM_path=None, multi_echo=None, overwrite=False,
                 stim_dir=None, channels=None, verbose=False):
        # sets the .self globalization for self variables
        self._input_dir = None
        self._config_path = None
        self._config = None
        self._bids_dir = None
        self._bids_version = "1.6.0"
        self._dataset_name = None
        self._data_types = {"anat": False, "func": False, "ieeg": False}
        self._ignore = []

        self.set_overwrite(overwrite)
        self.set_data_dir(input_dir, DICOM_path)
        self.set_config_path(config)
        self.set_bids_dir(output_dir)
        self.set_DICOM(DICOM_path)
        self.set_multi_echo(multi_echo)
        self.set_verbosity(verbose)
        self.set_stim_dir(stim_dir)
        self.set_channels(channels)

    def check_ignore(self, file: PathLike):

        if not op.exists(file):
            raise FileNotFoundError(file + " does not exist")

        ans = False
        for item in self._ignore:
            if op.isfile(item) and Path(file).resolve() == Path(
                    item).resolve():
                ans = True
            elif op.isdir(item):
                for root, dirs, files in os.walk(item):
                    if op.basename(file) in files and Path(root).resolve(
                    ) == Path(op.dirname(file)).resolve():
                        ans = True
        return ans

    def set_stim_dir(self, dir: PathLike):
        if dir is None:
            if "stimuli" in os.listdir(self._data_dir):
                dir = op.join(self._data_dir, "stimuli")
            elif "stimuli" in os.listdir(op.dirname(self._data_dir)):
                dir = op.join(op.dirname(self._data_dir), "stimuli")
            else:
                self.stim_dir = None
                return
        if not op.isdir(op.join(self._bids_dir, "stimuli")):
            os.mkdir(op.join(self._bids_dir, "stimuli"))
        for item in os.listdir(dir):
            shutil.copyfile(op.join(dir, item), op.join(
                self._bids_dir, "stimuli", item))
        self.stim_dir = dir
        self._ignore.append(dir)

    def set_channels(self, channels: list): #TODO: modularize
        headers: dict = self._config["ieeg"]["headerData"]
        self.channels = {}
        self.sample_rate = {}
        self.trigger = {}
        self._channels_file = {}
        for root, _, files in os.walk(self._data_dir):
            files[:] = [f for f in files if not self.check_ignore(
                op.join(root, f))]
            if not files:
                continue
            # ignore BIDS directories and stimuli
            task_label_match = self.find_a_match(files, "task")
            part_match = self.find_a_match(files, "partLabel")
            self.trigger[part_match] = get_trigger(part_match, headers)
            self.channels[part_match] = [self.trigger[part_match]]
            for i, file in enumerate(files):
                src = op.join(root, file)
                if any(f in file for f in
                       self._config["ieeg"]["channels"].keys()):
                    self._channels_file[part_match] = src

                for name, var in headers.items():
                    if re.match(".*?" + part_match + ".*?" + name, src):
                        # some sort of checking for .mat or txt files?
                        if name.endswith(".mat"):
                            self.channels[part_match] = \
                                self.channels[part_match] + \
                                mat2df(src, var).tolist()
                            self.sample_rate[part_match] = int(mat2df(
                                src, self._config['ieeg']['sampleRate']).iloc[
                                                                   0])
                            self._ignore.append(src)
                        elif name.endswith((".txt", ".csv", ".tsv")):
                            f = open(name, 'r')
                            content = f.read()
                            f.close()
                            self.channels[part_match] = \
                                self.channels[part_match] + content.split()
                        elif name.endswith(tuple(self._config['dataFormat'])):
                            raise NotImplementedError(
                                src + "\nthis file format does not yet support"
                                      " {ext} files for channel labels"
                                      "".format(ext=op.splitext(src)[1]))
            if isinstance(channels, str):
                channels = list(channels)
            if channels is not None:
                self.channels[part_match] = self.channels[part_match] + [
                    c for c in channels if c not in self.channels[part_match]]

    def set_overwrite(self, overwrite: bool):
        self._is_overwrite = overwrite

    def set_verbosity(self, verbose):
        self._is_verbose = verbose

    def set_multi_echo(self, multi_echo):  # if -m flag is called
        if multi_echo is None:
            self.is_multi_echo = False
        else:
            self.is_multi_echo = True
            if not multi_echo:
                self._multi_echo = 0
            else:
                self._multi_echo = multi_echo

    def set_DICOM(self, ddir):  # triggers only if dicom flag is called and
        # therefore _data_dir is None
        if self._data_dir is None:
            self._data_dir = op.dirname(self._bids_dir)
            subdirs = [x[0] for x in os.walk(ddir)]
            files = [x[2] for x in os.walk(ddir)]
            sub_num = str(dicom.read_file(op.join(subdirs[1], files[1][0]))[
                              0x10, 0x20].value).split("_", 1)[1]
            sub_dir = op.join(op.dirname(self._bids_dir),
                              "sub-{SUB_NUM}".format(SUB_NUM=sub_num))
            # destination subdirectory
            if op.isdir(sub_dir):
                proc = subprocess.Popen("rm -rf {file}".format(file=sub_dir),
                                        shell=True, stdout=subprocess.PIPE)
                proc.communicate()
            os.mkdir(sub_dir)

            if any("medata" in x for x in subdirs):  # copy over + list me data
                melist = [x[2] for x in os.walk(op.join(ddir, "medata"))][0]
                runlist = []
                for me in melist:
                    if me.startswith("."):
                        continue
                    runmatch = re.match(r".*run(\d{2}).*", me).group(1)
                    if str(int(runmatch)) not in runlist:
                        runlist.append(str(int(runmatch)))
                    shutil.copyfile(op.join(ddir, "medata", me), op.join(
                        sub_dir, me))
                self.is_multi_echo = True
                # will trigger even if single echo data is in medata folder.
                # Should still be okay
            for subdir in subdirs[1:]:
                # not including parent folder or /medata, run dcm2niix on non
                # me data
                try:
                    fobj = dicom.read_file(op.join(subdir, list(os.walk(
                        subdir))[0][2][0]), force=True)
                    # first dicom file of the scan
                    scan_num = str(int(op.basename(subdir))).zfill(2)
                except ValueError:
                    continue
                firstfile = [x[2] for x in os.walk(subdir)][0][0]
                # print(str(fobj[0x20, 0x11].value), runlist)
                # running dcm2niix
                if str(fobj[0x20, 0x11].value) in runlist:
                    proc = subprocess.Popen(
                        "dcm2niix -z y -f run{SCAN_NUM}_%p_%t_sub{SUB_NUM} -o "
                        "{OUTPUT_DIR} -s y -b y {DATA_DIR}".format(
                            OUTPUT_DIR=sub_dir, SUB_NUM=sub_num,
                            DATA_DIR=op.join(subdir, firstfile),
                            SCAN_NUM=scan_num), shell=True,
                        stdout=subprocess.PIPE)
                    outs, errs = proc.communicate()
                    prefix = re.match(".*/sub-{SUB_NUM}/(run{SCAN_NUM}".format(
                        SUB_NUM=sub_num, SCAN_NUM=scan_num
                    ) + r"[^ \(\"\\n\.]*).*", str(outs)).group(1)
                    for file in os.listdir(sub_dir):
                        mefile = re.match(r"run{SCAN_NUM}(\.e\d\d)\.nii"
                                          r"".format(SCAN_NUM=scan_num), file)
                        if re.match(r"run{SCAN_NUM}\.e\d\d.nii".format(
                                SCAN_NUM=scan_num), file):
                            shutil.move(op.join(sub_dir, file),
                                        op.join(sub_dir, prefix + mefile.group(
                                            1) + ".nii"))
                            shutil.copy(op.join(sub_dir, prefix + ".json"),
                                        op.join(sub_dir, prefix + mefile.group(
                                            1) + ".json"))
                    os.remove(op.join(sub_dir, prefix + ".nii.gz"))
                    os.remove(op.join(sub_dir, prefix + ".json"))
                else:
                    proc = subprocess.Popen(
                        "dcm2niix -z y -f run{SCAN_NUM}_%p_%t_sub{SUB_NUM} -o "
                        "{OUTPUT_DIR} -b y {DATA_DIR}".format(
                            OUTPUT_DIR=sub_dir, SUB_NUM=sub_num,
                            DATA_DIR=subdir, SCAN_NUM=scan_num), shell=True,
                        stdout=subprocess.PIPE)
                    outs, errs = proc.communicate()
                sys.stdout.write(outs.decode("utf-8"))

            self._multi_echo = runlist
            self._data_dir = op.join(op.dirname(
                self._bids_dir), "sub-{SUB_NUM}".format(SUB_NUM=sub_num))
        self._DICOM_path = ddir

    def get_data_dir(self):
        return self._data_dir

    def set_data_dir(self, data_dir, DICOM):  # check if input dir is listed
        if DICOM is None:
            if data_dir is None:
                self._data_dir = os.getcwd()
            else:
                self._data_dir = data_dir
            self._dataset_name = op.basename(self._data_dir)
        else:
            self._data_dir = None

    def get_config(self):
        return self._config

    def get_config_path(self):
        return self._config_path

    def _set_config(self):
        with open(self._config_path, 'r') as fst:
            self._config = json.load(fst)

    def set_config(self, config):
        self._config = config

    def set_config_path(self, config_path: PathLike):
        if config_path is None:
            # Checking if a config.json is present
            if op.isfile(op.join(os.getcwd(), "config.json")):
                self._config_path = op.join(os.getcwd(), "config.json")
            # Otherwise taking the default config
            else:
                self._config_path = op.join(op.dirname(__file__),
                                            "config.json")
        else:
            self._config_path = config_path

        self._set_config()

    def get_bids_dir(self):
        return self._bids_dir

    def set_bids_dir(self, bids_dir: PathLike):
        if bids_dir is None:
            # Creating a new directory for BIDS
            try:
                newdir = op.join(self._data_dir, "BIDS")
            except TypeError:
                print("Error: Please provide input data directory if no "
                      "BIDS directory...")

        # deleting old BIDS to make room for new
        elif not op.basename(bids_dir) == "BIDS":
            newdir = op.join(bids_dir, "BIDS")
        else:
            newdir = bids_dir
        if not op.isdir(newdir):
            os.mkdir(newdir)
        elif self._is_overwrite:
            force_remove(newdir)
            os.mkdir(newdir)
        self._bids_dir = newdir
        self._ignore.append(newdir)
        # as of BIDS ver 1.6.0, CT is not a part of BIDS, so check for CT files
        # and add to .bidsignore
        self.bidsignore("*_CT.*")

    def get_bids_version(self):
        return self._bids_version

    def bids_validator(self):
        assert self._bids_dir is not None, "Cannot launch bids-validator wit" \
                                           "hout specifying bids directory !"
        subprocess.check_call(['bids-validator',
                               self._bids_dir])

    def find_a_match(self, files: Union[List[str], str],
                     config_key: str) -> str:
        subtype = isinstance(self._config[config_key]["content"][0], list)
        if isinstance(files, str):
            files: List[str] = list(files)
        e = None
        for file in files:
            try:
                return match_regexp(self._config[config_key], file, subtype)
            except AssertionError as e:
                continue
        raise FileNotFoundError("There was no file matching the config key {}"
                                "\n".format(config_key), e)

    def generate_names(self, src_file_path: PathLike, filename: str = None,
                       part_match: str = None, sess_match: str = None,
                       ce_match: str = None, acq_match: str = None,
                       echo_match: str = None, data_type_match: str = None,
                       task_label_match: str = None, run_match: str = None,
                       verbose: bool = None, debug: bool = False) -> Tuple[
        Union[str, Any], Union[str, bytes], Any, Optional[str], str, Union[
            str, Any], Optional[str], str, Any, str, Optional[str]]:
        """function to run through name text and generate metadata

        :param src_file_path:
        :type src_file_path:
        :param filename:
        :type filename:
        :param part_match:
        :type part_match:
        :param sess_match:
        :type sess_match:
        :param ce_match:
        :type ce_match:
        :param acq_match:
        :type acq_match:
        :param echo_match:
        :type echo_match:
        :param data_type_match:
        :type data_type_match:
        :param task_label_match:
        :type task_label_match:
        :param run_match:
        :type run_match:
        :param verbose:
        :type verbose:
        :param debug:
        :type debug:
        :return:
        :rtype:
        """
        if filename is None:
            filename = op.basename(src_file_path)
        if part_match is None:
            part_match, part_match_z = self.part_check(filename=filename)
        else:
            part_match_z = self.part_check(part_match)[1]
        if verbose is None:
            verbose = self._is_verbose
        dst_file_path = op.join(self._bids_dir, "sub-" + part_match_z)
        new_name = "sub-" + part_match_z
        SeqType = None
        # Matching the session
        sess_match, _ = self.check_label(
            sess_match, filename, new_name, "sessLabel",
            "No session found for %s" % src_file_path, verbose)
        if sess_match is not None:
            dst_file_path = op.join(dst_file_path, "ses-" + sess_match)

        # Matching the run number
        run_match, _ = self.check_label(
            run_match, filename, new_name, "runIndex", verbose=verbose)

        # Matching the data type
        try:
            data_type_match, dst_file_path = self.assess_data_type(
                filename, dst_file_path)
        except AssertionError:
            if verbose:
                print("No data type found")

        # Matching the task
        task_label_match, new_name = self.check_label(
            task_label_match, filename, new_name, "task",
            "no task found for " + src_file_path, verbose, debug)

        # if is an MRI
        if dst_file_path.endswith("func") or dst_file_path.endswith("anat"):
            try:
                SeqType = str(match_regexp(self._config["pulseSequenceType"],
                                           filename, subtype=True))
            except AssertionError:
                if verbose:
                    print("No pulse sequence found for %s" % src_file_path)
            except KeyError:
                if verbose:
                    print("pulse sequence not listed for %s, will look for in"
                          " file header" % src_file_path)
            echo_match, new_name = self.check_label(
                echo_match, filename, new_name, "echo",
                "No echo found for %s" % src_file_path, verbose)

        # check for optional labels
        acq_match, new_name = self.check_label(
            acq_match, filename, new_name, "acq",
            "no optional labels for " + src_file_path, verbose)

        ce_match, new_name = self.check_label(
            ce_match, filename, new_name, "ce",
            "no special contrast labels for " + src_file_path, verbose)

        if run_match is not None:
            new_name = new_name + "_run-" + run_match

        # Adding the modality to the new filename
        new_name = new_name + "_" + data_type_match

        return (new_name, dst_file_path, part_match, run_match, acq_match,
                echo_match, sess_match, ce_match,
                data_type_match, task_label_match, SeqType)

    def check_label(self, match: str, filename: str, new_name: str, tag: str,
                    message: str = None, verbose: bool = False,
                    debug: bool = False) -> Tuple[str, str]:
        """Workhorse function for generate_names to find config tag matches

        :param match:
        :type match:
        :param filename:
        :type filename:
        :param new_name:
        :type new_name:
        :param tag:
        :type tag:
        :param message:
        :type message:
        :param verbose:
        :type verbose:
        :param debug:
        :type debug:
        :return:
        :rtype:
        """
        try:
            try:
                subtype = isinstance(self._config[tag]["content"][0], list)
            except KeyError as e:
                debug = True
                raise e
            if match is None:
                match = match_regexp(self._config[tag], filename, subtype)
            if "fill" in self._config[tag].keys():
                if re.match(r"^[^\d]{1,3}", match):
                    matches = re.split(r"([^\d]{1,3})", match, 1)
                    match = matches[1] + str(int(matches[2])).zfill(
                        self._config[tag]["fill"])
                else:
                    match = str(int(match)).zfill(self._config[tag]["fill"])
            new_name = "{}_{}-{}".format(new_name, tag, match)
        except (AssertionError, KeyError) as e:
            if verbose and message is not None:
                print(message)
            if debug:
                raise e
        return match, new_name

    def assess_data_type(self, filename: str, dst: str):
        """checks data type and creates corresponding directory

        :param filename:
        :type filename:
        :param dst:
        :type dst:
        :return:
        :rtype:
        """
        for data_type in self._data_types.keys():
            try:
                data_subtype = match_regexp(self._config[data_type], filename,
                                            subtype=True)
                dst_file_path = op.join(dst, data_type)
                self._data_types[data_type] = True
                return data_subtype, dst_file_path
            except (AssertionError, KeyError):
                continue
        raise AssertionError("No matching data types could be found")

    def multi_echo_check(self, runnum, src_file=""):
        """check to see if run is multi echo based on input

        :param runnum:
        :type runnum:
        :param src_file:
        :type src_file:
        :return:
        :rtype:
        """
        if self.is_multi_echo:
            if int(runnum) in self._multi_echo:
                return True
            else:
                if self._multi_echo == 0:
                    try:
                        match_regexp(self._config["echo"], src_file)
                    except AssertionError:
                        return False
                    return True
                else:
                    return False
        else:
            return False

    def get_params(self, folder, echo_num, run_num):  # function to run through
        # DICOMs and get metadata
        # threading?
        if self.is_multi_echo and run_num in self._multi_echo:
            vols_per_time = len(self._config['delayTimeInSec']) - 1
            echo = self._config['delayTimeInSec'][echo_num]
        else:
            vols_per_time = 1
            echo = None

        for root, _, dfiles in os.walk(folder, topdown=True):
            dfiles.sort()
            for dfile in dfiles:
                dcm_file_path = op.join(root, dfile)
                fobj = dicom.read_file(str(dcm_file_path))
                if echo is None:
                    try:
                        echo = float(fobj[0x18, 0x81].value) / 1000
                    except KeyError:
                        echo = self._config['delayTimeInSec'][0]
                ImagesInAcquisition = int(fobj[0x20, 0x1002].value)
                seqlist = []
                for i in list(range(5)):
                    try:
                        seqlist.append(fobj[0x18, (32 + i)].value)
                        if seqlist[i] == 'NONE':
                            seqlist[i] = None
                        if isinstance(seqlist[i], dicom.multival.MultiValue):
                            seqlist[i] = list(seqlist[i])
                        if isinstance(seqlist[i], list):
                            seqlist[i] = ", ".join(seqlist[i])
                    except KeyError:
                        seqlist.append(None)
                [ScanningSequence, SequenceVariant, SequenceOptions,
                 AquisitionType, SequenceName] = seqlist
                try:
                    timings = []
                except NameError:
                    timings = [None] * int(ImagesInAcquisition / vols_per_time)

                RepetitionTime = (
                    (float(fobj[0x18, 0x80].value) / 1000))  # TR value extract
                # ed in milliseconds, converted to seconds
                try:
                    acquisition_series = self._config['series']
                except KeyError:
                    print("default")
                    acquisition_series = "non-interleaved"
                if acquisition_series == "even-interleaved":
                    InstackPositionNumber = 2
                else:
                    InStackPositionNumber = 1
                InstanceNumber = 0
                while None in timings:
                    if timings[InStackPositionNumber - 1] is None:
                        timings[InStackPositionNumber - 1] = slice_time_calc(
                            RepetitionTime, InstanceNumber, int(
                                ImagesInAcquisition / vols_per_time), echo)
                    if acquisition_series == "odd-interleaved" or \
                            acquisition_series == "even-interleaved":
                        InStackPositionNumber += 2
                        if InStackPositionNumber > ImagesInAcquisition / \
                                vols_per_time and acquisition_series == \
                                "odd-interleaved":
                            InStackPositionNumber = 2
                        elif InStackPositionNumber > ImagesInAcquisition / \
                                vols_per_time and acquisition_series == \
                                "even-interleaved":
                            InStackPositionNumber = 1
                    else:
                        InStackPositionNumber += 1
                    InstanceNumber += 1
                return (timings, echo, ScanningSequence, SequenceVariant,
                        SequenceOptions, SequenceName)

    def read_edf(self, file_name: PathLike,
                 channels: List[Union[int, str]] = None,
                 extra_arrays=None, extra_signal_headers=None):
        [edfname, dst_path, part_match] = self.generate_names(
            file_name, verbose=False)[0:3]
        header = highlevel.make_header(patientname=part_match,
                                       startdate=datetime.datetime(1, 1, 1))
        edf_name = op.join(dst_path, edfname + ".edf")
        d = {str: [], int: []}
        for i in channels:
            d[type(i)].append(i)

        f = EdfReader(file_name)
        chn_nums = d[int] + [i for i, x in enumerate(f.getSignalLabels())
                             if x in channels]
        f.close()
        chn_nums.sort()

        try:
            check_sep = self._config["eventFormat"]["Sep"]
        except (KeyError, AssertionError) as e:
            check_sep = None

        gc.collect()  # helps with memory

        if check_sep:
            # read edf
            print("Reading " + file_name + "...")
            [array, signal_headers, _] = highlevel.read_edf(
                file_name, ch_nrs=chn_nums,
                digital=self._config["ieeg"]["digital"], verbose=True)
            print("read it")
            if extra_arrays:
                array = array + extra_arrays
            if extra_signal_headers:
                signal_headers = signal_headers + extra_signal_headers

            for i, signal in enumerate(signal_headers):
                if (signal["label"] or i) == self.trigger[part_match]:
                    signal_headers[i]["label"] = "Trigger"

            return dict(name=file_name, bids_name=edf_name,
                        nsamples=array.shape[1], signal_headers=signal_headers,
                        file_header=header, data=array, reader=f)
        elif channels:
            highlevel.drop_channels(file_name, edf_name, channels,
                                    verbose=self._is_verbose)
            return None
        else:
            shutil.copy(file_name, edf_name)
            return None

    def part_check(self, part_match: str = None, filename: str = None) -> \
            Tuple[str, str]:
        # Matching the participant label to determine if
        # there exists therein delete previously created BIDS subject files
        assert part_match or filename
        if filename:
            try:
                part_match = match_regexp(self._config["partLabel"], filename)
            except AssertionError:
                print("No participant found for %s" % filename)
            except KeyError as e:
                print("Participant label pattern must be defined")
                raise e

        try:
            c = self._config["partLabel"]["fill"]
        except KeyError:
            return part_match, part_match

        if re.match(r"^[^\d]{1,3}", part_match):
            part_matches = re.split(r"([^\d]{1,3})", part_match, 1)
            part_match_z = part_matches[1] + str(int(part_matches[2])).zfill(c)
        else:
            part_match_z = str(int(part_match)).zfill(c)

        return part_match, part_match_z

    def bidsignore(self, string: str):
        bi_file = op.join(self._bids_dir, ".bidsignore")
        if not op.isfile(bi_file):
            with open(bi_file, 'w') as f:
                f.write(string + "\n")
        else:
            with open(bi_file, "r+") as f:
                if string not in f.read():
                    f.write(string + "\n")

    def check_for_mat_channels(self, fobj: pyedflib.EdfReader, root: PathLike,
                               all_files: List[PathLike],
                               mat_files: List[PathLike]
                               ) -> Tuple[List[np.ndarray], List[dict]]:
        extra_arrays = []
        extra_signal_headers = []
        file = fobj.file_name
        part_match = self.part_check(filename=file)[0]
        mats = [i for i in all_files + mat_files if i.endswith(".mat")]
        f_length = [len(mat2df(op.join(root, fname))) for fname in mats]
        if fobj.samples_in_file(0) in f_length:
            for fname in mats:
                sig_len = fobj.samples_in_file(0)
                if not op.isfile(fname):
                    fname = op.join(root, fname)
                if mat2df(fname) is None:
                    continue
                elif len(mat2df(fname)) == sig_len:
                    if fname in all_files:
                        all_files.remove(fname)
                    if fname in mat_files:
                        mat_files.remove(fname)
                    df = pd.DataFrame(mat2df(fname))
                    for cols in df.columns:
                        extra_arrays = np.vstack([extra_arrays, df[cols]])
                        extra_signal_headers.append(
                            highlevel.make_signal_header(
                                op.splitext(op.basename(fname))[0],
                                sample_rate=self.sample_rate[part_match]))
                elif sig_len * 0.99 <= len(mat2df(fname)) <= sig_len * 1.01:
                    raise BufferError(file + "of size" + sig_len +
                                      "is not the same size as" + fname +
                                      "of size" + len(mat2df(fname)))
        return extra_arrays, extra_signal_headers

    def mri_file_transfer(self, source: PathLike, destination: PathLike,
                          new_name: str):
        file = op.basename(source)
        compress = self._config["compress"]
        final_file = op.join(destination, new_name + ".nii")
        # we convert it using nibabel
        if not any(file.endswith(ext) for ext in [".nii", ".nii.gz"]):
            # check if .nii listed in config file, not if file ends with .nii
            # loading the original image
            nib_img = nib.load(source)
            nib_affine = np.array(nib_img.affine)
            nib_data = np.array(nib_img.dataobj)

            # create the nifti1 image
            # if minc format, invert the data and change the affine
            # transformation there is also an issue on minc headers
            if file.endswith(".mnc"):
                if len(nib_img.shape) > 3:
                    nib_affine[0:3, 0:3] = nib_affine[0:3, 0:3]
                    rot_z(np.pi / 2)
                    rot_y(np.pi)
                    rot_x(np.pi / 2)
                    nib_data = nib_data.T
                    nib_data = np.swapaxes(nib_data, 0, 1)

                    nifti_img = nib.Nifti1Image(nib_data, nib_affine,
                                                nib_img.header)
                    nifti_img.header.set_xyzt_units(xyz="mm", t="sec")
                    zooms = np.array(nifti_img.header.get_zooms())
                    zooms[3] = self._config["repetitionTimeInSec"]
                    nifti_img.header.set_zooms(zooms)
                elif len(nib_img.shape) == 3:
                    nifti_img = nib.Nifti1Image(nib_data, nib_affine,
                                                nib_img.header)
                    nifti_img.header.set_xyzt_units(xyz="mm")
            else:
                nifti_img = nib.Nifti1Image(nib_data, nib_affine,
                                            nib_img.header)

            # saving the image
            nib.save(nifti_img, final_file + ".gz")

        # if it is already a nifti file, no need to convert it so we just copy
        # rename
        if file.endswith(".nii.gz"):
            shutil.copy(source, final_file + ".gz")
        elif file.endswith(".nii"):
            shutil.copy(source, final_file)
            # compression just if .nii files
            if compress is True:
                print("zipping " + file)
                with open(final_file, 'rb') as f_in:
                    with gzip.open(final_file + ".gz", 'wb',
                                   self._config["compressLevel"]) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(final_file)

    def force_to_edf(self, source: PathLike, files: List[PathLike]) -> bool:
        remove_src_edf = True
        file = op.basename(source)
        part_match = self.part_check(filename=file)[0]
        headers_dict = self.channels[part_match]
        if file.endswith(".edf"):
            remove_src_edf = False
        elif file.endswith(".edf.gz"):
            with gzip.open(source, 'rb',
                           self._config["compressLevel"]) as f_in:
                with open(source.rsplit(".gz", 1)[0], 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
        elif not self._config["ieeg"]["binary?"]:
            raise NotImplementedError(
                "{file} file format not yet supported. If file is binary forma"
                "t, please indicate so and what encoding in the config.json "
                "file".format(file=file))
        elif headers_dict and any(".mat" in i for i in files) and \
                self.sample_rate[part_match] is not None:
            # assume has binary encoding
            try:  # open binary file and write decoded numbers as array where
                # rows = channels
                # check if zipped
                if file.endswith(".gz"):
                    with gzip.open(source, 'rb',
                                   self._config["compressLevel"]) as f:
                        data = np.frombuffer(f.read(), dtype=np.dtype(
                            self._config["ieeg"]["binaryEncoding"]))
                else:
                    with open(source, mode='rb') as f:
                        data = np.fromfile(f, dtype=self._config["ieeg"][
                            "binaryEncoding"])
                array = np.reshape(data, [len(headers_dict), -1], order='F')
                # byte order is Fortran encoding, dont know why
                signal_headers = highlevel.make_signal_headers(
                    headers_dict, sample_rate=self.sample_rate[part_match],
                    physical_max=np.amax(array), physical_min=(np.amin(array)))
                print("converting binary" + source + " to edf" +
                      op.splitext(source)[0] + ".edf")
                highlevel.write_edf(
                    op.splitext(source)[0] + ".edf", array,
                    signal_headers,
                    digital=self._config["ieeg"]["digital"])
            except OSError as e:
                print("eeg file is either not detailed well enough in config"
                      " file or file type not yet supported")
                raise e
        else:
            raise FileNotFoundError(
                "{file} header could not be found".format(file=file))
        return remove_src_edf

    def tsv_all_eeg(self, fname: PathLike, df: pd.DataFrame):
        """Writes tsv files into each folder containing eeg data

        :param fname:
        :type fname:
        :param df:
        :type df:
        :return:
        :rtype:
        """
        for d_type in self._data_types.keys():
            if not ("eeg" in d_type and self._data_types[d_type]):
                continue
            file_name = op.join(op.dirname(fname), d_type, op.basename(fname))
            df.to_csv(file_name, sep="\t", index=False)

    def make_coordsystem(self,
                         txt_df_dict: Dict[str, Union[pd.DataFrame, str]],
                         part_match_z: str):
        if txt_df_dict["error"] is not None:
            raise txt_df_dict["error"]
        df: pd.DataFrame = txt_df_dict["data"]
        df.columns = ["name1", "name2", "x", "y", "z", "hemisphere", "del"]
        df["name"] = df["name1"] + df["name2"].astype(str).str.zfill(2)
        df["hemisphere"] = df["hemisphere"] + df["del"]
        df = df.drop(columns=["name1", "name2", "del"])
        df["size"] = self._config["ieeg"]["size"]
        df = pd.concat(
            [df["name"], df["x"], df["y"], df["z"], df["size"],
             df["hemisphere"]], axis=1)
        filename = op.join(self._bids_dir, "sub-" + part_match_z,
                           "sub-{}_space-Talairach_electrodes.tsv"
                           "".format(part_match_z))
        self.tsv_all_eeg(filename, df)

    def make_channels_tsv(self, file_path: PathLike, task: str):
        part_match, part_match_z = self.part_check(filename=file_path)
        df = None
        for name, var in self._config["ieeg"]["channels"].items():
            if name in file_path:
                df = mat2df(file_path, var)
                if "highpass_cutoff" in df.columns.to_list():
                    df = df.rename(columns={"highpass_cutoff": "high_cutoff"})
                if "lowpass_cutoff" in df.columns.to_list():
                    df = df.rename(columns={"lowpass_cutoff": "low_cutoff"})
        df["type"] = self._config["ieeg"]["type"]
        df["units"] = self._config["ieeg"]["units"]
        df = df.append(
            {"name": "Trigger", "high_cutoff": 1, "low_cutoff": 1000,
             "type": "TRIG", "units": "uV"}, ignore_index=True)
        df = pd.concat([df["name"], df["type"], df["units"], df["low_cutoff"],
                        df["high_cutoff"]], axis=1)
        filename = op.join(self._bids_dir, "sub-{}".format(part_match_z),
                           "sub-" + part_match_z + "_task-{}".format(
                               task) + "_channels.tsv")
        self.tsv_all_eeg(filename, df)

    def write_edf(self, array: np.ndarray, signal_headers: List[dict],
                  header: dict, old_name: PathLike, correct):
        """checks for .tsv files in eeg folders then writes matching .edf files

        :param array:
        :type array:
        :param signal_headers:
        :type signal_headers:
        :param header:
        :type header:
        :param old_name:
        :type old_name:
        :param correct:
        :type correct:
        :return:
        :rtype:
        """
        start_nums = []
        matches = []
        new_name, file_path, part_match = self.generate_names(
            old_name, verbose=False)[0:3]
        for signal_header in signal_headers:
            signal_header["sample_rate"] = self.sample_rate[part_match]
            signal_header["sample_frequency"] = self.sample_rate[part_match]
        for file in sorted(os.listdir(file_path)):
            full_file = op.join(file_path, file)
            match_tsv = re.match(
                new_name.split("_ieeg", 1)[0] + "(?:_acq-" +
                self._config["acq"]["content"][0] + ")?_run-(" +
                self._config["runIndex"]["content"][0] + ")_events.tsv", file)
            if match_tsv:
                num_list = self.get_timing_from_tsv(full_file, signal_headers)
                start_nums.append(tuple(num_list))
                matches.append(match_tsv)
        for i in range(len(start_nums)):
            if i == 0:
                start = 0
                practice = op.join(file_path, "practice", new_name.split(
                    "_ieeg", 1)[0] + "_ieeg.edf")
                if not op.isfile(practice) and self._config["split"]["practice"
                ]:
                    os.makedirs(op.join(file_path, "practice"),
                                exist_ok=True)
                    highlevel.write_edf(practice, np.split(array, [
                        0, start_nums[0][0]], axis=1)[1], signal_headers,
                                        header, digital=self._config["ieeg"][
                            "digital"])
                    self.bidsignore("*practice*")
            else:
                start = start_nums[i - 1][1]

            if i == len(start_nums) - 1:
                end = array.shape[1]
            else:
                end = start_nums[i + 1][0]
            new_array = np.split(array, [start, end], axis=1)[1]
            tsv_name: str = op.join(file_path, matches[i].string)
            edf_name: str = tsv_name.split("_events.tsv", 1)[0] + "_ieeg.edf"
            full_name = op.join(file_path, new_name + ".edf")
            if self._is_verbose:
                print(full_name + "(Samples[" + str(start) + ":" + str(
                    end) + "]) ---> " + edf_name)
            highlevel.write_edf(edf_name, new_array, signal_headers, header,
                                digital=self._config["ieeg"]["digital"])
            df = pd.read_csv(tsv_name, sep="\t", header=0)
            os.remove(tsv_name)
            df.replace("[]", np.NaN, inplace=True)
            # all column manipulation and math in frame2bids
            df_new = self.frame2bids(df, self._config["eventFormat"]["Events"],
                                     self.sample_rate[part_match], correct,
                                     start)
            df_new.to_csv(tsv_name, sep="\t", index=False, na_rep="n/a")
            # dont forget .json files!
            self.write_sidecar(edf_name, part_match)
            self.write_sidecar(tsv_name, part_match)

    def get_timing_from_tsv(self, file_path: PathLike,
                            signal_headers: List[dict]) -> List[int]:
        """reads tsv files to determine sectioning of eeg files

        :param file_path:
        :type file_path:
        :param signal_headers:
        :type signal_headers:
        :return:
        :rtype:
        """
        df = pd.read_csv(file_path, sep="\t", header=0)
        # converting signal start and end to correct sample rate for data
        eval_col = eval_df(df, self._config["eventFormat"]["Timing"]["end"],
                           self.stim_dir)
        end_num = str2num(eval_col.iloc[-1])
        i = -1
        while not is_number(end_num):
            i -= 1
            end_num = str2num(eval_col.iloc[i])

        eval_col = eval_df(df, self._config["eventFormat"]["Timing"]["start"],
                           self.stim_dir)
        start_num = eval_col.iloc[0]
        i = 0
        while not is_number(start_num):
            i += 1
            start_num = str2num(eval_col.iloc[i])

        num_list = [round(
            (float(x) / float(self._config["eventFormat"]["SampleRate"])) *
            signal_headers[0][
                "sample_rate"]) for x in (start_num, end_num)]
        return num_list

    def write_sidecar(self, full_file: PathLike, part_match: str):
        if full_file.endswith(".tsv"):
            # need to search BIDS specs for list of possible known BIDS columns
            data = dict()
            df = pd.read_csv(full_file, sep="\t")
            return
        elif op.dirname(full_file).endswith("ieeg"):
            if not full_file.endswith(".edf"):
                full_file = full_file + ".edf"
            entities = layout.parse_file_entities(full_file)
            f = EdfReader(full_file)
            if f.annotations_in_file == 0:
                description = "n/a"
            elif f.getPatientAdditional():
                description = f.getPatientAdditional()
            elif f.getRecordingAdditional():
                description = f.getRecordingAdditional()
            elif any((not i.size == 0) for i in f.readAnnotations()):
                description = [i for i in f.readAnnotations()]
                print("description:", description)
            else:
                raise SyntaxError(full_file + "was not annotated correctly")
            signals = [s for s in f.getSignalLabels() if not s == "Trigger"]
            data = dict(TaskName=entities['task'],
                        InstitutionName=self._config["institution"],
                        iEEGReference=description,
                        SamplingFrequency=self.sample_rate[part_match],
                        PowerLineFrequency=60,
                        SoftwareFilters="n/a",
                        SEEGChannelCount=len(signals),
                        TriggerChannelCount=1,
                        RecordingDuration=f.file_duration)

        elif op.dirname(full_file).endswith("anat"):
            entities = layout.parse_file_entities(full_file + ".nii.gz")
            if entities["suffix"] == "CT":
                data = {}
            elif entities["suffix"] == "T1w":
                data = {}
            else:
                raise NotImplementedError(
                    full_file + "is not yet accounted for")
        else:
            data = {}
        if not op.isfile(op.splitext(full_file)[0] + ".json"):
            with open(op.splitext(full_file)[0] + ".json", "w") as fst:
                json.dump(data, fst)

    def frame2bids(self, df: pd.DataFrame, events: Union[dict, List[dict]],
                   data_sample_rate=None, audio_correction=None,
                   start_at=0) -> pd.DataFrame: # TODO: modularize
        new_df = None
        if isinstance(events, dict):
            events = list(events)
        event_order = 0
        for event in events:
            event_order += 1
            temp_df = pd.DataFrame()
            for key, value in event.items():
                if key == "stim_file":
                    temp_df["stim_file"] = df[value]
                    temp_df["duration"] = eval_df(df, value, self.stim_dir)
                else:
                    temp_df[key] = eval_df(df, value)
            if "trial_num" not in temp_df.columns:
                temp_df["trial_num"] = [1 + i for i in
                                        list(range(temp_df.shape[0]))]
                # TODO: make code below work for non correction case
                if "duration" not in temp_df.columns:
                    if "stim_file" in temp_df.columns:
                        temp = []
                        t_correct = []
                        for _, fname in temp_df["stim_file"].iteritems():
                            if fname.endswith(".wav"):
                                if self.stim_dir is not None:
                                    fname = op.join(self.stim_dir, fname)
                                    dir = self.stim_dir
                                else:
                                    dir = self._data_dir
                                try:
                                    frames, data = wavfile.read(fname)
                                except FileNotFoundError as e:
                                    print(fname + " not found in current directory or in " + dir)
                                    raise e
                                if audio_correction is not None:
                                    correct = audio_correction.set_index(0).squeeze()[op.basename(
                                        op.splitext(fname)[0])] * self._config["eventFormat"]["SampleRate"]
                                else:
                                    correct = 0
                                duration = (data.size / frames) * self._config["eventFormat"]["SampleRate"]
                            else:
                                raise NotImplementedError("current build only "
                                                          "supports .wav stim "
                                                          "files")
                            temp.append(duration)
                            t_correct.append(correct)
                        temp_df["duration"] = temp
                        # audio correction
                        if t_correct:
                            temp_df["correct"] = t_correct
                            temp_df["duration"] = temp_df.eval("duration - correct")
                            temp_df["onset"] = temp_df.eval("onset + correct")
                            temp_df = temp_df.drop(columns=["correct"])
                else:
                    raise LookupError("duration of event or copy of audio file"
                                      " required but not found in ".format(
                        self._config_path))
            temp_df["event_order"] = event_order
            if new_df is None:
                new_df = temp_df
            else:
                new_df = new_df.append(temp_df, ignore_index=True, sort=False)

        for name in ["onset", "duration"]:
            if not (pd.api.types.is_float_dtype(
                    new_df[name]) or pd.api.types.is_integer_dtype(new_df[name])):
                new_df[name] = pd.to_numeric(new_df[name], errors="coerce")
        if data_sample_rate is None:
            # onset is timing of even onset (in seconds)
            new_df["onset"] = new_df["onset"] / self._config["eventFormat"][
                "SampleRate"]
        else:
            # sample is in exact sample of event onset (at eeg sample rate)
            new_df["sample"] = (new_df["onset"] / self._config["eventFormat"][
                "SampleRate"] * data_sample_rate) - start_at
            # onset is timing of even onset (in seconds)
            new_df["onset"] = new_df["sample"] / data_sample_rate
            # round sample to nearest frame
            new_df["sample"] = pd.to_numeric(new_df["sample"].round(),
                                             errors="coerce",
                                             downcast="integer")
        # duration is duration of event (in seconds)
        new_df["duration"] = new_df["duration"] / self._config["eventFormat"][
            "SampleRate"]

        if self._is_verbose:
            print(new_df.sort_values(["trial_num", "event_order"]).drop(
                columns="event_order"))

        return new_df.sort_values(["trial_num", "event_order"]).drop(
            columns="event_order")

    def mat2tsv(self, mat_files: List[PathLike]): # TODO: modularize
        part_match = None
        written = True
        is_separate = None
        nindex = None
        for mat_file in mat_files:
            # initialize dataframe if new participant
            if not match_regexp(self._config["partLabel"], mat_file
                                ) == part_match:
                if written:
                    df = pd.DataFrame()
                elif not part_match == match_regexp(self._config["partLabel"],
                                                    mat_file):
                    b_index = [j not in df.columns.values.tolist() for j in
                               self._config["eventFormat"]["Sep"]].index(True)
                    raise FileNotFoundError(
                        "{config} variable was not found in {part}'s event "
                        "files".format(config=list(
                            self._config["eventFormat"]["Sep"].values())[
                            b_index], part=part_match))
            try:
                part_match = match_regexp(self._config["partLabel"], mat_file)
            except AssertionError:
                raise SyntaxError(
                    "file: {filename} has no matching {config}\n".format(
                        filename=mat_file, config=
                        self._config["content"][:][0]))
            df_new = mat2df(mat_file, self._config["eventFormat"]["Labels"])
            # check to see if new data is introduced. If not then keep
            # searching
            if isinstance(df_new, pd.Series):
                df_new = pd.DataFrame(df_new)
            if df_new.shape[0] > df.shape[0]:
                df = pd.concat(
                    [df[df.columns.difference(df_new.columns)], df_new],
                    axis=1)  # auto filters duplicates
            elif df_new.shape[0] == df.shape[0]:
                df = pd.concat(
                    [df, df_new[df_new.columns.difference(df.columns)]],
                    axis=1)  # auto filters duplicates
            else:
                continue
            written = False

            if self._is_verbose:
                print(df)
            try:
                if self._config["eventFormat"]["IDcol"] in df.columns.values.tolist():  # test if edfs should be
                    # separated by block or not
                    if len(df[self._config["eventFormat"]["IDcol"]].unique()) == 1 and self._config["split"]["Sep"] not in ["all", True] or not self._config["split"]["Sep"]:
                        # CHANGE this in case literal name doesn't change
                        is_separate = False
                        print("Warning: data may have been lost if file ID d"
                              "idn't change but the recording session did")
                        # construct fake orig data name to run through name generator
                        # TODO: fix this as well so it works for all data types
                        match_name = \
                            mat_file.split(op.basename(mat_file))[0] + \
                            df[self._config["eventFormat"]["IDcol"]][0] + \
                            self._config["ieeg"]["content"][0][1]
                    else:  # this means there was more than one recording
                        # session. In this case we will separate each
                        # trial block into a separate "run"
                        is_separate = True
                else:
                    continue
            except KeyError:
                match_name = mat_file

            # write the tsv from the dataframe
            # if not changed: check to see if there is anything new to write
            if not is_separate:
                self.write_events(match_name, df, mat_file)
                written = True
                continue

            if not all(j in df.columns.values.tolist() for j in
                       self._config["eventFormat"]["Sep"].values()):
                if self._is_verbose:
                    print(mat_file)
                continue

            # make sure numbers do not repeat when not wanted
            df_unique = df.filter(self._config["eventFormat"][
                                      "Sep"].values()).drop_duplicates()
            for i in range(df_unique.shape[0])[1:]:
                for j in self._config["eventFormat"]["Sep"].keys():
                    jval = self._config["eventFormat"]["Sep"][j]
                    try:
                        if self._config[j]["repeat"] is False:
                            try:  # making sure actual key errors get caught
                                if df_unique[jval].iat[i] in \
                                        df_unique[jval].tolist()[:i]:
                                    df_unique[jval].iat[i] = str(int(max(
                                        df_unique[jval].tolist())) + 1)
                            except KeyError as e:
                                raise ValueError(e)
                        else:
                            continue
                    except KeyError:
                        continue

            tupelist = list(df.filter(self._config[
                                          "eventFormat"][
                                          "Sep"].values()).drop_duplicates(
            ).itertuples(index=False))
            for i in range(len(tupelist)):  # iterate through every block
                nindex = (df.where(
                    df.filter(self._config["eventFormat"]["Sep"].values()) ==
                    tupelist[i]) == df).filter(
                    self._config["eventFormat"]["Sep"].values()).all(axis=1)
                match_name = mat_file.split(op.basename(mat_file))[0] + str(
                    df[self._config["eventFormat"]["IDcol"]][nindex].iloc[0])
                for k in self._config["eventFormat"]["Sep"].keys():
                    if k in self._config.keys():
                        data = str(df_unique[self._config["eventFormat"][
                            "Sep"][k]].iloc[i])
                        match_name = match_name + gen_match_regexp(
                            self._config[k], data)

                # fix this to check for data type
                match_name = match_name + self._config["ieeg"]["content"][0][1]
                self.write_events(match_name, df, mat_file, nindex)
            written = True

    def write_events(self, match: str, df: pd.DataFrame,
                     mat_file: str, nindex: int = None):
        """Workhorse for writing the events.tsv sidecar file

        :param mat_file:
        :type mat_file:
        :param match:
        :type match:
        :param df:
        :type df:
        :param nindex:
        :type nindex:
        :return:
        :rtype:
        """
        item = self.generate_names(match, verbose=False)
        if item is not None:
            (new_name, dst_file_path) = item[0:2]
        else:
            self.generate_names(match, verbose=True, debug=True)
            raise
        if nindex is not None:
            df = df.loc[nindex]
        file_name = op.join(dst_file_path, new_name.split(
            "ieeg")[0] + "events.tsv")
        if self._is_verbose:
            print(mat_file, "--->", file_name)
        df.to_csv(file_name, sep="\t", index=False)

    def make_subdirs(self, files: List[str], debug: bool = False):
        """Makes all subdirectories for file list

        :param debug:
        :type debug:
        :param files:
        :type files:
        :return:
        :rtype:
        """
        dst_file_path = self._bids_dir
        for file in files:
            try:
                item = self.generate_names(file, verbose=False, debug=debug)
                if item is not None:
                    dst_file_path = item[1]
            except TypeError as e:
                if debug:
                    raise e
                continue
            if not op.exists(dst_file_path):
                os.makedirs(dst_file_path)

    def announce_channels(self, part_match: str):
        """Prints read in channels if verbosity is set

        :param part_match:
        :type part_match:
        :return:
        :rtype:
        """
        if self._is_verbose and self.channels[part_match] is not None:
            print("Channels for {} are".format(part_match))
            print(self.channels[part_match])
            for i in self._ignore:
                if part_match in i:
                    print("From " + i)

    def run(self):  # main function

        # First we check that every parameters are configured
        if (self._data_dir is None or
                self._config_path is None or
                self._config is None or
                self._bids_dir is None):
            raise FileNotFoundError("Not all parameters are defined !")

        print("---- data2bids starting ----")
        print(self._data_dir)
        print("\n BIDS version:")
        print(self._bids_version)
        print("\n Config from file :")
        print(self._config_path)
        print("\n Ouptut BIDS directory:")
        print(self._bids_dir)
        print("\n")

        # Create the output BIDS directory
        if not op.exists(self._bids_dir):
            os.makedirs(self._bids_dir)

        # What is the base format to convert to
        curr_ext = self._config["dataFormat"]

        # delay time in TR unit if delay_time = 1, delay_time = repetition_time
        delaytime = self._config["delayTimeInSec"]

        # dataset_description.json must be included in the BIDS folder
        description = op.join(self._bids_dir, "dataset_description.json")
        if op.exists(description):
            with open(description, "r") as fst:
                filedata = json.load(fst)
            with open(description, 'w') as fst:
                data = {'Name': self._dataset_name,
                        'BIDSVersion': self._bids_version}
                filedata.update(data)
                json.dump(filedata, fst, ensure_ascii=False, indent=4)
        else:
            with open(description, 'w') as fst:
                data = {'Name': self._dataset_name,
                        'BIDSVersion': self._bids_version}
                json.dump(data, fst, ensure_ascii=False, indent=4)

        try:
            for key, data in self._config["JSON_files"].items():
                with open(op.join(self._bids_dir, key), 'w') as fst:
                    json.dump(data, fst, ensure_ascii=False, indent=4)
        except KeyError:
            pass

        # add a README file
        readme = op.join(self._bids_dir, "README")
        if not op.exists(readme):
            with open(readme, 'w') as fst:
                data = ""
                fst.write(data)

        # now we can scan all files and rearrange them
        for root, _, files in os.walk(self._data_dir, topdown=True):
            # each loop is a new participant so long as participant is top lev
            files[:] = [f for f in files if not self.check_ignore(
                op.join(root, f))]
            if not files:
                continue
            files.sort()
            eeg = []
            dst_file_path_list = []
            names_list = []
            mat_list = []
            run_list = []
            txt_df_list = []
            correct = None
            part_match = self.find_a_match(files, "partLabel")
            part_match_z = self.part_check(part_match)[1]
            task_label_match = self.find_a_match(files, "task")
            self.make_subdirs(files)
            if self.channels:
                self.announce_channels(part_match)
                self.make_channels_tsv(self._channels_file[part_match],
                                       task_label_match)
            if self._is_verbose:
                print(files)
            while files:  # loops over each participant file
                file = files.pop(0)
                if self._is_verbose:
                    print(file)
                src_file_path = op.join(root, file)
                if re.match(".*?" + "\\.mat", file):
                    mat_list.append(src_file_path)
                    continue
                elif re.match(".*?" + "\\.txt", file):
                    try:
                        df = pd.read_table(src_file_path, header=None,
                                           sep="\s+")
                        e = None
                    except Exception as e:
                        df = None
                    txt_df_list.append(dict(name=file, data=df, error=e))
                    continue
                elif not any(re.match(".*?" + ext, file) for ext in curr_ext):
                    # if the file doesn't match the extension, we skip it
                    print("Warning : Skipping %s" % src_file_path)
                    continue
                if self._is_verbose:
                    print("trying %s" % src_file_path)
                try:
                    (new_name, dst_file_path, part_match, run_match,
                     acq_match, echo_match, sess_match, ce_match,
                     data_type_match, task_label_match,
                     _) = self.generate_names(src_file_path,
                                              part_match=part_match)
                except TypeError as problem:
                    print("\nIssue in generate names")
                    print("problem with %s:" % src_file_path, problem, "\n")
                    continue

                # finally, if the file is not nifti
                if dst_file_path.endswith(
                        "func") or dst_file_path.endswith("anat"):
                    self.mri_file_transfer(
                        src_file_path, dst_file_path, new_name)

                elif dst_file_path.endswith("ieeg"):
                    remove_src_edf = self.force_to_edf(
                        src_file_path, files)

                    f = EdfReader(op.splitext(src_file_path)[0] + ".edf")
                    # check for extra channels in data, not working in other
                    # file modalities
                    extra_arrays, extra_signal_headers = \
                        self.check_for_mat_channels(
                            f, root, files, mat_list)

                    f.close()
                    # read edf and either copy data to BIDS file or save data
                    # as dict for writing later
                    eeg.append(self.read_edf(op.splitext(
                        src_file_path)[0] + ".edf", self.channels[
                                                 part_match], extra_arrays,
                                             extra_signal_headers))

                    if remove_src_edf:
                        if self._is_verbose:
                            print("Removing " + op.splitext(src_file_path)[
                                0] + ".edf")
                        os.remove(op.splitext(src_file_path)[0] + ".edf")

                # move the sidecar from input to output
                names_list.append(new_name)
                dst_file_path_list.append(dst_file_path)
                try:
                    if run_match is not None:
                        run_list.append(int(run_match))
                except UnboundLocalError:
                    pass

            if mat_list:  # deal with remaining .mat files
                self.mat2tsv(mat_list)

            if txt_df_list:
                for txt_df_dict in txt_df_list:
                    if self._config["coordsystem"] in txt_df_dict["name"]:
                        self.make_coordsystem(txt_df_dict, part_match_z)
                    elif self._config["eventFormat"]["AudioCorrection"] in \
                            txt_df_dict["name"]:
                        if txt_df_dict["error"] is not None:
                            raise txt_df_dict["error"]
                        correct = txt_df_dict["data"]
                    else:
                        print("skipping " + txt_df_dict["name"])

            # check final file set
            for new_name in names_list:
                print(new_name)
                file_path = dst_file_path_list[names_list.index(new_name)]
                full_name = op.join(file_path, new_name + ".edf")
                task_match = re.match(".*_task-(\w*)_.*", full_name)
                if task_match:
                    task_label_match = task_match.group(1)
                # split any edfs according to tsvs
                pattern = "{}(?:_acq-{})?_run-{}_events\.tsv".format(
                    new_name.split("_ieeg")[0],
                    self._config["acq"]["content"][0],
                    self._config["runIndex"]["content"][0])
                match_set = [re.match(pattern, str(set_file)) for set_file in
                             os.listdir(file_path)]
                if new_name.endswith("_ieeg") and any(match_set):
                    # if edf is not yet split
                    if self._is_verbose:
                        print("Reading for split... ")
                    if full_name in [i["bids_name"] for i in eeg]:
                        eeg_dict = eeg[
                            [i["bids_name"] for i in eeg].index(full_name)]
                    else:
                        raise LookupError(
                            "This error should not have been raised, was edf "
                            "file " + full_name + " ever written?",
                            [i["name"] for i in eeg])
                    self.write_edf(eeg_dict["data"],
                                   eeg_dict["signal_headers"],
                                   eeg_dict["file_header"],
                                   eeg_dict["name"],
                                   correct)
                    continue
                # write JSON file for any missing files
                self.write_sidecar(op.join(file_path, new_name),
                                   part_match)

            # write any indicated .json files
            try:
                json_list = self._config["JSON_files"]
            except KeyError:
                json_list = dict()
            for jfile, contents in json_list.items():
                print(part_match_z, task_label_match, jfile)
                file_name = op.join(
                    self._bids_dir, "sub-" + part_match_z, "ieeg",
                    "sub-{}_task-{}_{}".format(
                        part_match_z, task_label_match, jfile))
                with open(file_name, "w") as fst:
                    json.dump(contents, fst)
        # Output
        if self._is_verbose:
            tree(self._bids_dir)


def main():
    args = get_parser().parse_args()
    data2bids = Data2Bids(**vars(args))
    data2bids.run()


if __name__ == '__main__':
    main()
