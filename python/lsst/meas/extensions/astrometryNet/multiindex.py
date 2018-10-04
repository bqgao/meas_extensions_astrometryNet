from __future__ import absolute_import, division, print_function

__all__ = ["getIndexPath", "getConfigFromEnvironment", "AstrometryNetCatalog", "generateCache"]

from builtins import zip
from builtins import range
from builtins import object
import os

import numpy as np
from astropy.io import fits

import lsst.utils
from lsst.log import Log
from .astrometry_net import MultiIndex, healpixDistance
from .astrometryNetDataConfig import AstrometryNetDataConfig


def getIndexPath(fn):
    """Get the path to the specified astrometry.net index file

    No effort is made to confirm that the file exists, so it may be used to
    locate the path to a non-existent file (e.g., to write).

    Parameters
    ----------
    fn :
        path to index file; if relative, then relative to
        astrometry_net_data if that product is setup, else relative to the
        current working directory

    Returns
    -------
    result :
        the absolute path to the index file
    """
    if os.path.isabs(fn):
        return fn
    try:
        andir = lsst.utils.getPackageDir('astrometry_net_data')
    except Exception:
        # Relative to cwd
        return os.path.abspath(fn)
    return os.path.join(andir, fn)


def getConfigFromEnvironment():
    """Find the config file from the environment

    The andConfig.py file is in the astrometry_net_data directory.
    """
    try:
        anDir = lsst.utils.getPackageDir('astrometry_net_data')
    except Exception:
        anDir = os.getcwd()
        andConfigPath = "andConfig.py"
        if not os.path.exists(andConfigPath):
            raise RuntimeError("Unable to find andConfig.py in the current directory.  "
                               "Did you forget to setup astrometry_net_data?")
    else:
        andConfigPath = os.path.join(anDir, "andConfig.py")
        if not os.path.exists(andConfigPath):
            raise RuntimeError("Unable to find andConfig.py in astrometry_net_data directory %s" % (anDir,))

    andConfig = AstrometryNetDataConfig()
    andConfig.load(andConfigPath)
    return andConfig


class MultiIndexCache(object):
    """A wrapper for the multiindex_t, which only reads the data when it
    needs to

    The MultiIndexCache may be instantiated directly, or via the
    'fromFilenameList' class method, which loads it from a list of filenames.

    Parameters
    ----------
    filenameList :
        List of filenames; first is the multiindex, then
        follows the individual index files
    healpix :
        Healpix number
    nside :
        Healpix nside
    """

    def __init__(self, filenameList, healpix, nside):
        if len(filenameList) < 2:
            raise RuntimeError("Insufficient filenames provided for multiindex (%s): expected >= 2" %
                               (filenameList,))
        self._filenameList = filenameList
        self._healpix = int(healpix)
        self._nside = int(nside)
        self._mi = None
        self._loaded = False
        self.log = Log.getDefaultLogger()

    @classmethod
    def fromFilenameList(cls, filenameList):
        """Construct from a list of filenames

        The list of filenames should contain the multiindex filename first,
        then the individual index filenames.  The healpix and nside are
        determined by reading the indices, so this is not very efficient.
        """
        self = cls(filenameList, 0, 0)
        self.reload()
        healpixes = set(self[i].healpix for i in range(len(self)))
        nsides = set(self[i].hpnside for i in range(len(self)))
        assert len(healpixes) == 1
        assert len(nsides) == 1
        self._healpix = healpixes.pop()
        self._nside = nsides.pop()
        return self

    def read(self):
        """Read the indices"""
        if self._mi is not None:
            return
        fn = getIndexPath(self._filenameList[0])
        if not os.path.exists(fn):
            raise RuntimeError(
                "Unable to get filename for astrometry star file %s" % (self._filenameList[0],))
        self._mi = MultiIndex(fn)
        if self._mi is None:
            # Can't proceed at all without stars
            raise RuntimeError('Failed to read stars from astrometry multiindex filename "%s"' % fn)
        for i, fn in enumerate(self._filenameList[1:]):
            if fn is None:
                self.log.debug('Unable to find index part of multiindex %s', fn)
                continue
            fn = getIndexPath(fn)
            if not os.path.exists(fn):
                self.log.warn("Unable to get filename for astrometry index %s", fn)
                continue
            self.log.debug('Reading index from multiindex file "%s"', fn)
            self._mi.addIndex(fn, False)
            ind = self._mi[i]
            self.log.debug('  index %i, hp %i (nside %i), nstars %i, nquads %i',
                           ind.indexid, ind.healpix, ind.hpnside, ind.nstars, ind.nquads)

    def reload(self):
        """Reload the indices."""
        if self._loaded:
            return
        if self._mi is None:
            self.read()
        else:
            self._mi.reload()
        self._loaded = True

    def unload(self):
        """Unload the indices"""
        if not self._loaded:
            return
        self._mi.unload()
        self._loaded = False

    def isWithinRange(self, coord, distance):
        """Is the index within range of the provided coordinates?

        Parameters
        ----------
        coord :
            ICRS coordinate to check (lsst.afw.geom.SpherPoint)
        distance :
            Angular distance (lsst.afw.geom.Angle)
        """
        return (self._healpix == -1 or healpixDistance(self._healpix, self._nside, coord) <= distance)

    def __getitem__(self, i):
        self.reload()
        return self._mi[i]

    def __len__(self):
        return len(self._filenameList) - 1  # The first is the multiindex; the rest are the indices

    def __iter__(self):
        self.reload()
        return iter(self._mi)


class AstrometryNetCatalog(object):
    """An interface to an astrometry.net catalog

    Behaves like a list of MultiIndexCache (or multiindex_t).

    Parameters
    ----------
    andConfig :
        Configuration (an AstrometryNetDataConfig)

    Notes
    -----
    These should usually be constructed using the 'fromEnvironment'
    class method, which wraps the 'fromIndexFiles' and 'fromCache'
    alternative class methods.
    """
    _cacheFilename = "andCache.fits"

    def __init__(self, andConfig):
        self.config = andConfig
        cacheName = getIndexPath(self._cacheFilename)
        if self.config.allowCache and os.path.exists(cacheName):
            self._initFromCache(cacheName)
        else:
            self._initFromIndexFiles(self.config)

    def _initFromIndexFiles(self, andConfig):
        """Initialise from the index files in an AstrometryNetDataConfig"""
        indexFiles = list(zip(andConfig.indexFiles, andConfig.indexFiles)) + andConfig.multiIndexFiles
        self._multiInds = [MultiIndexCache.fromFilenameList(fnList) for fnList in indexFiles]

    def writeCache(self):
        """Write a cache file

        Notes
        -----
        The cache file is a FITS file with all the required information to
        build the AstrometryNetCatalog quickly.  The first table extension
        contains a row for each multiindex, storing the healpix and nside
        values.  The second table extension contains a row for each filename
        in all the multiindexes.  The two may be JOINed through the 'id'
        column.
        """
        outName = getIndexPath(self._cacheFilename)
        numFilenames = sum(len(ind._filenameList) for ind in self._multiInds)
        maxLength = max(len(fn) for ind in self._multiInds for fn in ind._filenameList) + 1

        # First table
        first = fits.BinTableHDU.from_columns([fits.Column(name="id", format="K"),
                                               fits.Column(name="healpix", format="K"),
                                               fits.Column(name="nside", format="K"),
                                               ], nrows=len(self._multiInds))
        first.data.field("id")[:] = np.arange(len(self._multiInds), dtype=int)
        first.data.field("healpix")[:] = np.array([ind._healpix for ind in self._multiInds])
        first.data.field("nside")[:] = np.array([ind._nside for ind in self._multiInds])

        # Second table
        second = fits.BinTableHDU.from_columns([fits.Column(name="id", format="K"),
                                                fits.Column(name="filename", format="%dA" % (maxLength)),
                                                ], nrows=numFilenames)
        ident = second.data.field("id")
        filenames = second.data.field("filename")
        i = 0
        for j, ind in enumerate(self._multiInds):
            for fn in ind._filenameList:
                ident[i] = j
                filenames[i] = fn
                i += 1

        fits.HDUList([fits.PrimaryHDU(), first, second]).writeto(outName, overwrite=True)

    def _initFromCache(self, filename):
        """Initialise from a cache file

        Ingest the cache file written by the 'writeCache' method and
        use that to quickly instantiate the AstrometryNetCatalog.
        """
        with fits.open(filename) as hduList:
            first = hduList[1].data
            second = hduList[2].data

            # first JOIN second USING(id)
            filenames = {i: [] for i in first.field("id")}
            for id2, fn in zip(second.field("id"), second.field("filename")):
                filenames[id2].append(fn)
            self._multiInds = [MultiIndexCache(filenames[i], hp, nside) for i, hp, nside in
                               zip(first.field("id"), first.field("healpix"), first.field("nside"))]

        # Check for consistency
        cacheFiles = set(second.field("filename"))
        configFiles = set(sum(self.config.multiIndexFiles, []) + self.config.indexFiles)
        assert(cacheFiles == configFiles)

    def __getitem__(self, ii):
        return self._multiInds[ii]

    def __iter__(self):
        return iter(self._multiInds)

    def __len__(self):
        return len(self._multiInds)


def generateCache(andConfig=None):
    """Generate a cache file"""
    if andConfig is None:
        andConfig = getConfigFromEnvironment()
    catalog = AstrometryNetCatalog(andConfig)
    try:
        for index in catalog:
            index.reload()
        catalog.writeCache()
    finally:
        for index in catalog:
            index.unload()
