# This file is part of xrayutilities.
#
# xrayutilities is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.
#
# Copyright (C) 2009-2010 Eugen Wintersberger <eugen.wintersberger@desy.de>
# Copyright (C) 2009-2015 Dominik Kriegner <dominik.kriegner@gmail.com>

"""
module to handle spectra data
"""

import glob
import re

import numpy
import numpy.lib.recfunctions
from numpy import rec

from .. import config
from .helper import xu_h5open

re_wspaces = re.compile(r"\s+")
re_colname = re.compile(r"^Col")

re_comment_section = re.compile(r"^%c")
re_parameter_section = re.compile(r"^%p")
re_data_section = re.compile(r"^%d")
re_end_section = re.compile(r"^!")
re_unit = re.compile(r"\[.+\]")
re_obracket = re.compile(r"\[")
re_cbracket = re.compile(r"\]")
re_underscore = re.compile(r"_")
re_column = re.compile(r"^Col")
re_col_name = re.compile(r"\d+\s+.+\s*\[")
re_col_index = re.compile(r"\d+\s+")
re_col_type = re.compile(r"\[.+\]")
re_num = re.compile(r"[0-9]")

dtype_map = {"FLOAT": "f4",
             "DOUBLE": "f8"}


class SPECTRAFileComments(dict):
    """
    Class that describes the comments in the header of a SPECTRA file.
    The different comments are accessible via the comment keys.
    """

    def __init__(self):
        pass

    def __getattr__(self, name):
        if name in self:
            return self[name]


class SPECTRAFileParameters(dict):

    def __init__(self):
        pass

    def __getattr__(self, name):
        if name in self:
            return self[name]

    def __str__(self):
        ostr = ""
        n = len(self.keys())
        lmax_key = 0
        lmax_item = 0
        strlist = []

        # find the length of the longest key
        for k in self.keys():
            if len(k) > lmax_key:
                lmax_key = len(k)

            i = self[k]
            if not isinstance(i, str):
                # if the item is not a string it must be converted
                i = "%f" % i

            if len(i) > lmax_item:
                lmax_item = len(i)

        # define the format string for a single key-value pair
        kvfmt = "|%%-%is = %%-%is" % (lmax_key, lmax_item)

        nc = 3
        nres = len(self.keys()) % nc
        nrow = (len(self.keys()) - nres) / nc

        cnt = 0
        ostr += (3 * (lmax_key + lmax_item + 4) + 1) * "-" + "\n"
        ostr += "|Parameters:" + (3 * (lmax_key + lmax_item)) * " " + "|\n"
        ostr += (3 * (lmax_key + lmax_item + 4) + 1) * "-" + "\n"
        for key in self.keys():
            value = self[key]
            if not isinstance(value, str):
                value = "%f" % value

            ostr += kvfmt % (key, value)
            cnt += 1
            if cnt == 3:
                ostr += "|\n"
                cnt = 0

        if cnt != 0:
            ostr += "|\n"
        ostr += (3 * (lmax_key + lmax_item + 4) + 1) * "-" + "\n"

        return ostr


class SPECTRAFileDataColumn(object):

    def __init__(self, index, name, unit, type):
        self.index = int(index)
        self.name = name
        self.unit = unit
        self.type = type

    def __str__(self):
        ostr = "%i %s %s %s" % (self.index, self.name, self.unit, self.type)
        return ostr


class SPECTRAFileData(object):

    def __init__(self):
        self.collist = []
        self.data = None

    def append(self, col):
        self.collist.append(col)

    def __getitem__(self, key):
        try:
            return self.data[key]
        except:
            print("XU.io.specta.SPECTRAFileData: data contains no column "
                  "named: %s!" % key)

    def __str__(self):
        ostr = ""

        # determine the maximum lenght of every column string
        lmax = 0
        for c in self.collist:
            if len(c.__str__()) > lmax:
                lmax = len(c.__str__())

        lmax += 3

        # want to print in three columns
        nc = 3
        nres = len(self.collist) % nc
        nrows = (len(self.collist) - nres) / nc

        fmtstr = "| %%-%is| %%-%is| %%-%is|\n" % (lmax, lmax, lmax)

        ostr += (3 * lmax + 7) * "-" + "\n"
        ostr += "|Column names:" + (3 * lmax - 8) * " " + "|\n"
        ostr += (3 * lmax + 7) * "-" + "\n"
        # full output rows
        for i in range(nrows):
            c1 = self.collist[i * nc + 0]
            c2 = self.collist[i * nc + 1]
            c3 = self.collist[i * nc + 2]
            ostr += fmtstr % (c1.__str__(), c2.__str__(), c3.__str__())

        # residual output row
        c = ['', '', '']
        for j in range(nres):
            c[j] = self.collist[-nres + j]

        ostr += fmtstr % (c[0].__str__(), c[1].__str__(), c[2].__str__())

        ostr += (3 * lmax + 7) * "-" + "\n"
        return ostr


class SPECTRAFile(object):

    """
    Represents a SPECTRA data file. The file is read during the
    Constructor call. This class should work for data stored at
    beamlines P08 and BW2 at HASYLAB.

    Required constructor arguments:
    ------------------------------
     filename ............. a string with the name of the SPECTRA file

    Optional keyword arguments:
    --------------------------
     mcatmp ............... template for the MCA files
     mcastart,mcastop ..... start and stop index for the MCA files, if not
                            given, the class tries to determine the start and
                            stop index automatically.
    """

    def __init__(self, filename, mcatmp=None, mcastart=None, mcastop=None):
        self.filename = filename
        self.comments = SPECTRAFileComments()
        self.params = SPECTRAFileParameters()
        self.data = SPECTRAFileData()
        self.mca = None
        self.mca_channels = None

        self.Read()  # reads the .fio data file

        if mcatmp is not None:
            self.mca_file_template = mcatmp

            if mcastart is not None and mcastop is not None:
                self.mca_start_index = mcastart
                self.mca_stop_index = mcastop
            else:
                # try to determine the number of MCA spectra automatically
                spat = self.mca_file_template.replace("%i", "*")
                l = glob.glob(spat)
                self.mca_start_index = 1
                self.mca_stop_index = 0
                if l:
                    self.mca_stop_index = self.data.data.size  # len(l)

            if self.mca_stop_index != 0:
                self.ReadMCA()

    def Save2HDF5(self, h5file, name, group="/", mcaname="MCA"):
        """
        Saves the scan to an HDF5 file. The scan is saved to a
        seperate group of name "name". h5file is either a string
        for the file name or a HDF5 file object.
        If the mca attribute is not None mca data will be stored to an
        chunked array of with name mcaname.

        required input arguments:
         h5file .............. string or HDF5 file object
         name ................ name of the group where to store the data

        optional keyword arguments:
         group ............... root group where to store the data
         mcaname ............. Name of the MCA in the HDF5 file

        Return value:
        The method returns None in the case of everything went fine, True
        otherwise.
        """
        with xu_h5open(h5file, 'w') as h5:
            # create the group where to store the data
            try:
                g = h5.create_group(group + '/' + name)
            except ValueError:
                print("XU.io.spectra.Save2HDF5: cannot create group %s for "
                      "writing data!" % name)
                return True

            # start with saving scan comments
            for k in self.comments.keys():
                try:
                    g.attrs[k] = self.comments[k]
                except:
                    print("XU.io.spectra.Save2HDF5: cannot save file comment "
                          "%s = %s to group %s!" % (k, self.comments[k], name))

            # save scan parameters
            for k in self.params.keys():
                try:
                    g.attrs[k] = self.params[k]
                except:
                    print("XU.io.spectra.Save2HDF5: cannot save file parametes"
                          " %s to group %s!" % (k, name))

            # ----------finally we need to save the data -------------------
            kwds = {'fletcher32': True, 'compression': 'gzip'}

            try:
                dset = g.create_dataset("data", data=self.data.data, **kwds)
            except:
                print("XU.io.spectra.Save2HDF5: cannot create table for "
                      "storing scan data!")
                return True

            # if there is MCA data - store this
            if self.mca is not None:
                try:
                    c = g.create_dataset(mcaname, data=self.mca, **kwds)
                except:
                    print("XU.io.spectra.Save2HDF5: cannot create carray %s "
                          "for MCA data!" % mcaname)
                    return True

                # set MCA specific attributes
                c.attrs["channels"] = self.mca_channels
                c.attrs["nchannels"] = self.mca_channels.shape[0]

            h5.flush()

        return None

    def ReadMCA(self):
        dlist = []
        for i in range(self.mca_start_index, self.mca_stop_index + 1):
            fname = self.mca_file_template % i
            data = numpy.loadtxt(fname)

            if i == self.mca_start_index:
                if len(data.shape) == 2:
                    self.mca_channels = data[:, 0]
                else:
                    self.mca_channels = numpy.arange(0, data.shape[0])

            if len(data.shape) == 2:
                dlist.append(data[:, 1].tolist())
            else:
                dlist.append(data.tolist())

        self.mca = numpy.array(dlist, dtype=float)

    def __str__(self):
        ostr = self.params.__str__()
        ostr += self.data.__str__()

        return ostr

    def Read(self):
        """
        Read the data from the file.
        """

        def addkeyval(l, k, v):
            """
            add new key to a list. if key already exists a number will be
            appended to the key name

            Parameters
            ----------
             l:     list
             k:     key
             v:     value
            """
            kcnt = 0
            key = k
            while key in l:
                key = k + "_%i" % (kcnt + 1)
                kcnt += 1
            l[key] = v

        col_names = []
        col_units = []
        col_types = []
        rec_list = []
        with open(self.filename, 'rb') as fid:
            for line in fid:
                line = line.decode('utf8', 'ignore')
                line = line.strip()

                # read the next line if the line starts with a "!"
                if re_end_section.match(line):
                    continue

                # select the which section to read
                if re_comment_section.match(line):
                    read_mode = 1
                    continue

                if re_parameter_section.match(line):
                    read_mode = 2
                    continue

                if re_data_section.match(line):
                    read_mode = 3
                    continue

                # here we decide how to proceed with the data
                if read_mode == 1:
                    # read the file comments
                    try:
                        (key, value) = line.split("=")
                    except:
                        # avoid annoying output
                        if config.VERBOSITY >= config.INFO_ALL:
                            print("XU.io.SPECTRAFile.Read: cannot interpret "
                                  "the comment string: %s" % (line))
                        continue

                    key = key.strip()
                    # remove whitespaces to be conform with natural naming
                    key = key.replace(' ', '')
                    key = key.replace(':', '_')
                    # remove possible number at first position
                    if re_num.findall(key[0]) != []:
                        key = "_" + key
                    value = value.strip()
                    if config.VERBOSITY >= config.DEBUG:
                        print("XU.io.SPECTRAFile.Read: comment: k,v: %s, %s"
                              % (key, value))

                    try:
                        value = float(value)
                    except:
                        pass

                    # need to handle the case, that a key may appear several
                    # times in the list
                    addkeyval(self.comments, key, value)

                elif read_mode == 2:
                    # read scan parameters
                    try:
                        (key, value) = line.split("=")
                    except:
                        print("XU.io.SPECTRAFile.Read: cannot interpret the "
                              "parameter string: %s" % (line))

                    key = key.strip()
                    # remove whitespaces to be conform with natural naming
                    key = key.replace(' ', '')
                    key = key.replace(':', '_')
                    # remove possible number at first position
                    if re_num.findall(key[0]) != []:
                        key = "_" + key
                    value = value.strip()
                    if config.VERBOSITY >= config.DEBUG:
                        print("XU.io.SPECTRAFile.Read: parameter: k,v: %s, %s"
                              % (key, value))

                    try:
                        value = float(value)
                    except:
                        # if the conversion of the parameter to float
                        # fails it will be saved as a string
                        pass

                    # need to handle the case, that a key may appear several
                    # times in the list
                    addkeyval(self.params, key, value)

                elif read_mode == 3:
                    if re_column.match(line):
                        try:
                            unit = re_unit.findall(line)[0]
                        except IndexError:
                            unit = "NONE"

                        try:
                            sline = re_obracket.split(line)
                            if len(sline) == 1:
                                raise IndexError
                            lval = sline[0]
                            rval = re_cbracket.split(line)[-1]
                            dtype = rval.strip()
                            l = re_wspaces.split(lval)
                            index = int(l[1])
                            name = "".join(l[2:])
                            name = name.replace(':', '_')
                        except IndexError:
                            l = re_wspaces.split(line)
                            index = int(l[1])
                            dtype = l[-1]
                            name = "".join(l[2:-1])
                            name = name.replace(':', '_')

                        # store column definition
                        self.data.append(
                            SPECTRAFileDataColumn(index, name, unit, dtype))

                        if name in col_names:
                            name += "%s_1" % name
                        col_names.append("%s" % name)
                        col_types.append("%s" % (dtype_map[dtype]))

                    else:
                        # read data
                        dlist = re_wspaces.split(line)
                        for i in range(len(dlist)):
                            dlist[i] = float(dlist[i])

                        rec_list.append(dlist)

        if config.VERBOSITY >= config.DEBUG:
            print("XU.io.SPECTRAFile.Read: data columns: name,type: %s, %s"
                  % (col_names, col_types))
        if rec_list:
            self.data.data = rec.fromrecords(rec_list, formats=col_types,
                                             names=col_names)
        else:
            self.data.data = None


def geth5_spectra_map(h5file, scans, *args, **kwargs):
    """
    function to obtain the omega and twotheta as well as intensity values
    for a reciprocal space map saved in an HDF5 file, which was created
    from a spectra file by the Save2HDF5 method.

    further more it is possible to obtain even more positions from
    the data file if more than two string arguments with its names are given

    Parameters
    ----------
     h5f:     file object of a HDF5 file opened using h5py
     scans:   number of the scans of the reciprocal space map (int,tuple or
              list)

    *args:   arbitrary number of motor names (strings)
     omname:  name of the omega motor (or its equivalent)
     ttname:  name of the two theta motor (or its equivalent)

    **kwargs (optional):
     mca:        name of the mca data (if available) otherwise None
                 (default: "MCA")
     samplename: string with the hdf5-group containing the scan data
                 if omitted the first child node of h5f.root will be used
                 to determine the sample name

    Returns
    -------
     [ang1,ang2,...],MAP:
                angular positions of the center channel of the position
                sensitive detector (numpy.ndarray 1D) together with all the
                data values as stored in the data file (includes the
                intensities e.g. MAP['MCA']).
    """

    with xu_h5open(h5file) as h5:
        mca = kwargs.get('mca', 'MCA')

        if "samplename" in kwargs:
            basename = kwargs["samplename"]
        else:
            nodename = list(h5.keys())[0]
            basenlist = re_underscore.split(nodename)
            basename = "_".join(basenlist[:-1])
            if config.VERBOSITY >= config.DEBUG:
                print("XU.io.spectra.geth5_spectra_map: using \'%s\' as "
                      "basename" % (basename))

        if isinstance(scans, (list, tuple)):
            scanlist = scans
        else:
            scanlist = list([scans])

        angles = dict.fromkeys(args)
        for key in angles.keys():
            angles[key] = numpy.zeros(0)
        buf = numpy.zeros(0)
        MAP = numpy.zeros(0)

        for nr in scanlist:
            h5scan = h5.get(basename + "_%05d" % nr)
            sdata = h5scan.get('data')
            if mca:
                mcanode = h5.get(basename + "_%05d/%s" % (nr, mca))
                mcadata = numpy.asarray(mcanode)

            # append scan data to MAP, where all data are stored
            mcatemp = mcadata.view([(mca, (mcadata.dtype, mcadata.shape[1]))])
            sdtmp = numpy.lib.recfunctions.merge_arrays([sdata, mcatemp],
                                                        flatten=True)
            if MAP.dtype == numpy.float64:
                MAP.dtype = sdtmp.dtype
            MAP = numpy.append(MAP, sdtmp)

            # check type of scan
            notscanmotors = []
            for i in range(len(args)):
                motname = args[i]
                try:
                    buf = sdata[motname]
                    scanshape = buf.shape
                    angles[motname] = numpy.concatenate((angles[motname], buf))
                except:
                    notscanmotors.append(i)
            for i in notscanmotors:
                motname = args[i]
                buf = numpy.ones(scanshape) * \
                    h5.getNodeAttr(h5scan, "%s" % motname)
                angles[motname] = numpy.concatenate((angles[motname], buf))

    retval = []
    for motname in args:
        # create return values in correct order
        retval.append(angles[motname])

    return retval, MAP
