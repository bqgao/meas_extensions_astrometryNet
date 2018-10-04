#
# LSST Data Management System
# Copyright 2008-2016 AURA/LSST.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#
from __future__ import absolute_import, division, print_function

__all__ = ["ANetAstrometryConfig", "ANetAstrometryTask", "showAstrometry"]

from builtins import input
from builtins import zip
from builtins import range

import numpy as np

import lsstDebug
import lsst.pex.exceptions
import lsst.afw.geom as afwGeom
from lsst.afw.cameraGeom import PIXELS, TAN_PIXELS
from lsst.afw.table import Point2DKey, CovarianceMatrix2fKey, updateSourceCoords
import lsst.pex.config as pexConfig
import lsst.pipe.base as pipeBase
from lsst.meas.astrom import displayAstrometry
from lsst.meas.astrom.sip import makeCreateWcsWithSip
from .anetBasicAstrometry import ANetBasicAstrometryTask


class ANetAstrometryConfig(pexConfig.Config):
    solver = pexConfig.ConfigurableField(
        target=ANetBasicAstrometryTask,
        doc="Basic astrometry solver",
    )
    forceKnownWcs = pexConfig.Field(dtype=bool, doc=(
        "Assume that the input image's WCS is correct, without comparing it to any external reality." +
        " (In contrast to using Astrometry.net).  NOTE, if you set this, you probably also want to" +
        " un-set 'solver.calculateSip'; otherwise we'll still try to find a TAN-SIP WCS starting " +
        " from the existing WCS"), default=False)
    rejectThresh = pexConfig.RangeField(dtype=float, default=3.0, doc="Rejection threshold for Wcs fitting",
                                        min=0.0, inclusiveMin=False)
    rejectIter = pexConfig.RangeField(dtype=int, default=3, doc="Rejection iterations for Wcs fitting",
                                      min=0)

    @property
    def refObjLoader(self):
        """An alias, for a uniform interface with the standard AstrometryTask"""
        return self.solver

    # \addtogroup LSST_task_documentation
    # \{
    # \page measAstrom_anetAstrometryTask
    # \ref ANetAstrometryTask_ "ANetAstrometryTask"
    # Use astrometry.net to match input sources with a reference catalog and solve for the Wcs
    # \}


class ANetAstrometryTask(pipeBase.Task):
    # Parameters
    # ----------
    # schema :
    #     An lsst::afw::table::Schema used to create the output lsst.afw.table.SourceCatalog
    # refObjLoader :
    #     The AstrometryTask constructor requires a refObjLoader.  In order to make this
    # task retargettable for AstrometryTask it needs to take the same arguments.  This argument will be
    # ignored since it uses its own internal loader.
    # \*\*kwds : keyword arguments to be passed to the lsst.pipe.base.task.Task constructor

    # Notes
    # -----
    # A centroid field "centroid.distorted" (used internally during the Task's operation)
    # will be added to the schema.
    """!Use astrometry.net to match input sources with a reference catalog and solve for the Wcs

    @anchor ANetAstrometryTask_

    The actual matching and solving is done by the 'solver'; this Task
    serves as a wrapper for taking into account the known optical distortion.

    \section pipe_tasks_astrometry_Contents Contents

     - \ref pipe_tasks_astrometry_Purpose
     - \ref pipe_tasks_astrometry_Initialize
     - \ref pipe_tasks_astrometry_IO
     - \ref pipe_tasks_astrometry_Config
     - \ref pipe_tasks_astrometry_Debug
     - \ref pipe_tasks_astrometry_Example

    \section pipe_tasks_astrometry_Purpose  Description

    \copybrief ANetAstrometryTask

    \section pipe_tasks_astrometry_Initialize   Task initialisation

    \copydoc \_\_init\_\_

    \section pipe_tasks_astrometry_IO       Invoking the Task

    \copydoc run

    \section pipe_tasks_astrometry_Config       Configuration parameters

    See \ref ANetAstrometryConfig

    \section pipe_tasks_astrometry_Debug        Debug variables

    The \link lsst.pipe.base.cmdLineTask.CmdLineTask command line task\endlink interface supports a
    flag \c -d to import \b debug.py from your \c PYTHONPATH;
    see \ref baseDebug for more about \b debug.py files.

    The available variables in ANetAstrometryTask are:
    <DL>
      <DT> \c display
      <DD> If True call showAstrometry while iterating ANetAstrometryConfig.rejectIter times,
      and also after converging; and call displayAstrometry after applying the distortion correction.
      <DT> \c frame
      <DD> ds9 frame to use in showAstrometry and displayAstrometry
      <DT> \c pause
      <DD> Pause after showAstrometry and displayAstrometry?
    </DL>

    \section pipe_tasks_astrometry_Example  A complete example of using ANetAstrometryTask

    See \ref pipe_tasks_photocal_Example.

    To investigate the \ref pipe_tasks_astrometry_Debug, put something like
    \code{.py}
        import lsstDebug
        def DebugInfo(name):
            di = lsstDebug.getInfo(name)        # N.b. lsstDebug.Info(name) would call us recursively
            if name in ("lsst.pipe.tasks.anetAstrometry", "lsst.pipe.tasks.anetBasicAstrometry"):
                di.display = 1
                di.frame = 1
                di.pause = True

            return di

        lsstDebug.Info = DebugInfo
    \endcode
    into your debug.py file and run photoCalTask.py with the \c --debug flag.
    """
    ConfigClass = ANetAstrometryConfig
    _DefaultName = "astrometricSolver"

    def __init__(self, schema, refObjLoader=None, **kwds):
        pipeBase.Task.__init__(self, **kwds)
        self.distortedName = "astrom_distorted"
        self.centroidXKey = schema.addField(self.distortedName + "_x", type="D",
                                            doc="centroid distorted for astrometry solver")
        self.centroidYKey = schema.addField(self.distortedName + "_y", type="D",
                                            doc="centroid distorted for astrometry solver")
        self.centroidXErrKey = schema.addField(self.distortedName + "_xErr", type="F",
                                               doc="centroid distorted err for astrometry solver")
        self.centroidYErrKey = schema.addField(self.distortedName + "_yErr", type="F",
                                               doc="centroid distorted err for astrometry solver")
        self.centroidFlagKey = schema.addField(self.distortedName + "_flag", type="Flag",
                                               doc="centroid distorted flag astrometry solver")
        self.centroidKey = Point2DKey(self.centroidXKey, self.centroidYKey)
        self.centroidErrKey = CovarianceMatrix2fKey((self.centroidXErrKey, self.centroidYErrKey))
        # postpone making the solver subtask because it may not be needed and is expensive to create
        self.solver = None

    @pipeBase.timeMethod
    def run(self, exposure, sourceCat):
        """Load reference objects, match sources and optionally fit a WCS

        This is a thin layer around solve or loadAndMatch, depending on config.forceKnownWcs

        Parameters
        ----------
        exposure :
            exposure whose WCS is to be fit
            The following are read only:

            - bbox
            - calib (may be absent)
            - filter (may be unset)
            - detector (if wcs is pure tangent; may be absent)
            The following are updated:
            - wcs (the initial value is used as an initial guess, and is required)

        sourceCat :
            catalog of sourceCat detected on the exposure (an lsst.afw.table.SourceCatalog)

        Returns
        -------
        result : `lsst.pipe.base.Struct`
            with these fields:

            - ``refCat`` :  reference object catalog of objects that overlap the exposure (with some margin)
                (an lsst::afw::table::SimpleCatalog)
            - ``matches`` :  astrometric matches, a list of lsst.afw.table.ReferenceMatch
            - ``matchMeta`` :  metadata about the field (an lsst.daf.base.PropertyList)
        """
        if self.config.forceKnownWcs:
            return self.loadAndMatch(exposure=exposure, sourceCat=sourceCat)
        else:
            return self.solve(exposure=exposure, sourceCat=sourceCat)

    @pipeBase.timeMethod
    def solve(self, exposure, sourceCat):
        """Match with reference sources and calculate an astrometric solution

        exposure : Exposure to calibrate; wcs is updated
        sourceCat : catalog of measured sources (an lsst.afw.table.SourceCatalog)

        Returns
        -------
        result : `lsst.pipe.base.struct`
            a pipeBase.Struct with fields:

            - ``refCat`` :  reference object catalog of objects that overlap the exposure (with some margin)
                (an lsst::afw::table::SimpleCatalog)
            - ``matches`` :  astrometric matches, a list of lsst.afw.table.ReferenceMatch
            - ``matchMeta`` :  metadata about the field (an lsst.daf.base.PropertyList)

        Notes
        -----
        The reference catalog actually used is up to the implementation
        of the solver; it will be manifested in the returned matches as
        a list of lsst.afw.table.ReferenceMatch objects (\em i.e. of lsst.afw.table.Match with
        first being of type lsst.afw.table.SimpleRecord and \c second type lsst.afw.table.SourceRecord ---
        the reference object and matched object respectively).

        The input sources have the centroid slot moved to a new column "centroid.distorted"
        which has the positions corrected for any known optical distortion;
        the 'solver' (which is instantiated in the 'astrometry' member)
        should therefore simply use the centroids provided by calling
        afw.table.Source.getCentroid() on the individual source records.  This column \em must
        be present in the sources table.

        ignores config.forceKnownWcs
        """
        results = self._astrometry(sourceCat=sourceCat, exposure=exposure)

        if results.matches:
            self.refitWcs(sourceCat=sourceCat, exposure=exposure, matches=results.matches)

        return results

    @pipeBase.timeMethod
    def distort(self, sourceCat, exposure):
        """Calculate distorted source positions

        The distortion correction moves sources, so we return the distorted bounding box.

        Parameters
        ----------
        exposure :
            Exposure to process
        sourceCat :
            SourceCatalog; getX() and getY() will be used as inputs,
            with distorted points in "centroid.distorted" field.

        Returns
        -------
        result :
            bounding box of distorted exposure

        Notes
        -----
        CCD images are often affected by optical distortion that makes
        the astrometric solution higher order than linear.  Unfortunately,
        most (all?) matching algorithms require that the distortion be
        small or zero, and so it must be removed.  We do this by calculating
        (un-)distorted positions, based on a known optical distortion model
        in the Ccd.
        """
        detector = exposure.getDetector()
        pixToTanXYTransform = None
        if detector is None:
            self.log.warn("No detector associated with exposure; assuming null distortion")
        else:
            pixToTanXYTransform = detector.getTransform(PIXELS, TAN_PIXELS)

        if pixToTanXYTransform is None:
            self.log.info("Null distortion correction")
            for s in sourceCat:
                s.set(self.centroidKey, s.getCentroid())
                s.set(self.centroidErrKey, s.getCentroidErr())
                s.set(self.centroidFlagKey, s.getCentroidFlag())
            return exposure.getBBox()

        # Distort source positions
        self.log.info("Applying distortion correction")
        for s in sourceCat:
            centroid = pixToTanXYTransform.forwardTransform(s.getCentroid())
            s.set(self.centroidKey, centroid)
            s.set(self.centroidErrKey, s.getCentroidErr())
            s.set(self.centroidFlagKey, s.getCentroidFlag())

        # Get distorted image size so that astrometry_net does not clip.
        bboxD = afwGeom.Box2D()
        for corner in detector.getCorners(TAN_PIXELS):
            bboxD.include(corner)

        if lsstDebug.Info(__name__).display:
            frame = lsstDebug.Info(__name__).frame
            pause = lsstDebug.Info(__name__).pause
            displayAstrometry(sourceCat=sourceCat, distortedCentroidKey=self.centroidKey,
                              exposure=exposure, frame=frame, pause=pause)

        return afwGeom.Box2I(bboxD)

    @pipeBase.timeMethod
    def loadAndMatch(self, exposure, sourceCat, bbox=None):
        """Load reference objects overlapping an exposure and match to sources detected on that exposure

        Parameters
        ----------
        exposure :
            exposure whose WCS is to be fit
        sourceCat :
            catalog of sourceCat detected on the exposure (an lsst.afw.table.SourceCatalog)
        bbox :
            bounding box go use for finding reference objects; if None, use exposure's bbox

        Returns
        -------
        result : `lsst.pipe.base.Struct`
            with these fields:

            - ``refCat`` :  reference object catalog of objects that overlap the exposure (with some margin)
                (an lsst::afw::table::SimpleCatalog)
            - ``matches`` :  astrometric matches, a list of lsst.afw.table.ReferenceMatch
            - ``matchMeta`` :  metadata about the field (an lsst.daf.base.PropertyList)

        Notes
        -----
        ignores config.forceKnownWcs
        """
        bbox = exposure.getBBox()
        if not self.solver:
            self.makeSubtask("solver")

        astrom = self.solver.useKnownWcs(
            sourceCat=sourceCat,
            exposure=exposure,
            bbox=bbox,
            calculateSip=False,
        )

        if astrom is None or astrom.getWcs() is None:
            raise RuntimeError("Unable to solve astrometry")

        matches = astrom.getMatches()
        matchMeta = astrom.getMatchMetadata()
        if matches is None or len(matches) == 0:
            raise RuntimeError("No astrometric matches")
        self.log.info("%d astrometric matches", len(matches))

        if self._display:
            frame = lsstDebug.Info(__name__).frame
            displayAstrometry(exposure=exposure, sourceCat=sourceCat, matches=matches,
                              frame=frame, pause=False)

        return pipeBase.Struct(
            refCat=astrom.refCat,
            matches=matches,
            matchMeta=matchMeta,
        )

    @pipeBase.timeMethod
    def _astrometry(self, sourceCat, exposure, bbox=None):
        """Solve astrometry to produce WCS

        Parameters
        ----------
        sourceCat :
        Sources on exposure, an lsst.afw.table.SourceCatalog
        exposure :
            Exposure to process, an lsst.afw.image.ExposureF or D; wcs is updated
        bbox :
            Bounding box, or None to use exposure

        Returns
        -------
        result : `pipe.base.Struct`
            with fields:

            - ``refCat`` :  reference object catalog of objects that overlap the exposure (with some margin)
                (an lsst::afw::table::SimpleCatalog)
            - ``matches`` :  astrometric matches, a list of lsst.afw.table.ReferenceMatch
            - ``matchMeta`` :  metadata about the field (an lsst.daf.base.PropertyList)

        """
        self.log.info("Solving astrometry")
        if bbox is None:
            bbox = exposure.getBBox()

        if not self.solver:
            self.makeSubtask("solver")

        astrom = self.solver.determineWcs(sourceCat=sourceCat, exposure=exposure, bbox=bbox)

        if astrom is None or astrom.getWcs() is None:
            raise RuntimeError("Unable to solve astrometry")

        matches = astrom.getMatches()
        matchMeta = astrom.getMatchMetadata()
        if matches is None or len(matches) == 0:
            raise RuntimeError("No astrometric matches")
        self.log.info("%d astrometric matches", len(matches))

        # Note that this is the Wcs for the provided positions, which may be distorted
        exposure.setWcs(astrom.getWcs())

        if self._display:
            frame = lsstDebug.Info(__name__).frame
            displayAstrometry(exposure=exposure, sourceCat=sourceCat, matches=matches,
                              frame=frame, pause=False)

        return pipeBase.Struct(
            refCat=astrom.refCat,
            matches=matches,
            matchMeta=matchMeta,
        )

    @pipeBase.timeMethod
    def refitWcs(self, sourceCat, exposure, matches):
        """!A final Wcs solution after matching and removing distortion

        Specifically, fitting the non-linear part, since the linear
        part has been provided by the matching engine.

        sourceCat :
            Sources on exposure, an lsst.afw.table.SourceCatalog
        exposure :
            Exposure of interest, an lsst.afw.image.ExposureF or D
        matches :
            Astrometric matches, as a list of lsst.afw.table.ReferenceMatch

        Returns
        -------
        result :
            resolved-Wcs object, or None if config.solver.calculateSip is False.
        """
        sip = None
        if self.config.solver.calculateSip:
            self.log.info("Refitting WCS")
            origMatches = matches
            wcs = exposure.getWcs()

            import lsstDebug
            display = lsstDebug.Info(__name__).display
            frame = lsstDebug.Info(__name__).frame
            pause = lsstDebug.Info(__name__).pause

            def fitWcs(initialWcs, title=None):
                """!Do the WCS fitting and display of the results"""
                sip = makeCreateWcsWithSip(matches, initialWcs, self.config.solver.sipOrder)
                resultWcs = sip.getNewWcs()
                if display:
                    showAstrometry(exposure, resultWcs, origMatches, matches, frame=frame,
                                   title=title, pause=pause)
                return resultWcs, sip.getScatterOnSky()

            numRejected = 0
            try:
                for i in range(self.config.rejectIter):
                    wcs, scatter = fitWcs(wcs, title="Iteration %d" % i)

                    ref = np.array([wcs.skyToPixel(m.first.getCoord()) for m in matches])
                    src = np.array([m.second.getCentroid() for m in matches])
                    diff = ref - src
                    rms = diff.std()
                    trimmed = []
                    for d, m in zip(diff, matches):
                        if np.all(np.abs(d) < self.config.rejectThresh*rms):
                            trimmed.append(m)
                        else:
                            numRejected += 1
                    if len(matches) == len(trimmed):
                        break
                    matches = trimmed

                # Final fit after rejection iterations
                wcs, scatter = fitWcs(wcs, title="Final astrometry")

            except lsst.pex.exceptions.LengthError as e:
                self.log.warn("Unable to fit SIP: %s", e)

            self.log.info("Astrometric scatter: %f arcsec (%d matches, %d rejected)",
                          scatter.asArcseconds(), len(matches), numRejected)
            exposure.setWcs(wcs)

            # Apply WCS to sources
            updateSourceCoords(wcs, sourceCat)
        else:
            self.log.warn("Not calculating a SIP solution; matches may be suspect")

        if self._display:
            frame = lsstDebug.Info(__name__).frame
            displayAstrometry(exposure=exposure, sourceCat=sourceCat, matches=matches,
                              frame=frame, pause=False)

        return sip


def showAstrometry(exposure, wcs, allMatches, useMatches, frame=0, title=None, pause=False):
    """Show results of astrometry fitting

    exposure :
        Image to display
    wcs :
        Astrometric solution
    allMatches :
        List of all astrometric matches (including rejects)
    useMatches :
        List of used astrometric matches
    frame :
        Frame number for display
    title :
        Title for display
    pause :
        Pause to allow viewing of the display and optional debugging?

    Notes
    -----
    - Matches are shown in yellow if used in the Wcs solution, otherwise red

     - +: Detected objects
     - x: Catalogue objects

    """
    import lsst.afw.display.ds9 as ds9
    ds9.mtv(exposure, frame=frame, title=title)

    useIndices = set(m.second.getId() for m in useMatches)

    radii = []
    with ds9.Buffering():
        for i, m in enumerate(allMatches):
            x, y = m.second.getX(), m.second.getY()
            pix = wcs.skyToPixel(m.first.getCoord())

            isUsed = m.second.getId() in useIndices
            if isUsed:
                radii.append(np.hypot(pix[0] - x, pix[1] - y))

            color = ds9.YELLOW if isUsed else ds9.RED

            ds9.dot("+", x, y, size=10, frame=frame, ctype=color)
            ds9.dot("x", pix[0], pix[1], size=10, frame=frame, ctype=color)

    radii = np.array(radii)
    print("<dr> = %.4g +- %.4g pixels [%d/%d matches]" % (radii.mean(), radii.std(),
                                                          len(useMatches), len(allMatches)))

    if pause:
        import sys
        while True:
            try:
                reply = input("Debugging? [p]db [q]uit; any other key to continue... ").strip()
            except EOFError:
                reply = ""

            if len(reply) > 1:
                reply = reply[0]
            if reply == "p":
                import pdb
                pdb.set_trace()
            elif reply == "q":
                sys.exit(1)
            else:
                break
