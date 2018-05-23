from mdtraj.formats.hdf5 import HDF5TrajectoryFile
from mdtraj.reporters import HDF5Reporter
from simtk.openmm import app
import simtk.unit as units
import json, yaml
import subprocess
import numpy as np
from mdtraj.utils import unitcell
from mdtraj.utils import in_units_of, ensure_type

import mdtraj.version
import simtk.openmm.version
import blues.version
import logging
import sys, time
import parmed
from blues.formats import *
import blues.reporters
from parmed import unit as u
from parmed.amber.netcdffiles import NetCDFTraj, NetCDFRestart
from parmed.geometry import box_vectors_to_lengths_and_angles
import netCDF4 as nc

def _check_mode(m, modes):
    if m not in modes:
        raise ValueError('This operation is only available when a file '
                         'is open in mode="%s".' % m)

def addLoggingLevel(levelName, levelNum, methodName=None):
    """
    Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

    To avoid accidental clobberings of existing attributes, this method will
    raise an `AttributeError` if the level name is already an attribute of the
    `logging` module or if the method name is already present

    Example
    -------
    >>> addLoggingLevel('TRACE', logging.DEBUG - 5)
    >>> logging.getLogger(__name__).setLevel("TRACE")
    >>> logging.getLogger(__name__).trace('that worked')
    >>> logging.trace('so did this')
    >>> logging.TRACE
    5

    """
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
       logging.warn('{} already defined in logging module'.format(levelName))
    if hasattr(logging, methodName):
       logging.warn('{} already defined in logging module'.format(methodName))
    if hasattr(logging.getLoggerClass(), methodName):
       logging.warn('{} already defined in logger class'.format(methodName))

    # This method was inspired by the answers to Stack Overflow post
    # http://stackoverflow.com/q/2183233/2988730, especially
    # http://stackoverflow.com/a/13638084/2988730
    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)
    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)

def init_logger(logger, level=logging.INFO, outfname=time.strftime("blues-%Y%m%d-%H%M%S")):
    """Initialize the Logger module with the given logger_level and outfname.
    """
    fmt = LoggerFormatter()

    # Stream to terminal
    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(fmt)
    logger.addHandler(stdout_handler)

    # Write to File
    if outfname:
        fh = logging.FileHandler(outfname+'.log')
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    logger.addHandler(logging.NullHandler())
    logger.setLevel(level)

    return logger

class ReporterConfig:
    """
    Generates a set of custom/recommended reporters for
    BLUES simulations from YAML configuration.

    This class is intended to be called internally from `blues.config.set_Reporters`.
    Below is an example to call this externally.

    from blues.reporters import ReporterConfig
    import logging

    outfname = 'blues-test'
    logger = logging.getLogger(__name__)
    md_reporters = { "restart": { "reportInterval": 1000 },
                      "state" : { "reportInterval": 250  },
                      "stream": { "progress": true,
                                  "remainingTime": true,
                                  "reportInterval": 250,
                                  "speed": true,
                                  "step": true,
                                  "title": "md",
                                  "totalSteps": 10000},
                     "traj_netcdf": { "reportInterval": 250 }
                    }

    md_reporter_cfg = ReporterConfig(outfname, md_reporters, logger)
    md_reporters_list = md_reporter_cfg.makeReporters()

    """
    def __init__(self, outfname, reporter_config, logger=None):
        """

        Parameters
        ----------
        outfname : str,
            Output filename prefix for files generated by the reporters.
        reporter_config : dict
            Dict of parameters for the md_reporters or ncmc_reporters
        logger : logging.Logger object
            Provide the root logger for printing information.
        """
        self._outfname = outfname
        self._cfg = reporter_config
        self._logger = logger
        self.trajectory_interval = 0

    def makeReporters(self):
        """
        Returns a list of openmm Reporters based on the configuration at
        initialization of the class.
        """
        Reporters = []
        if 'state' in self._cfg.keys():

            #Use outfname specified for reporter
            if 'outfname' in self._cfg['state']:
                outfname = self._cfg['state']['outfname']
            else: #Default to top level outfname
                outfname = self._outfname

            state = parmed.openmm.reporters.StateDataReporter(outfname+'.ene', **self._cfg['state'])
            Reporters.append(state)

        if 'traj_netcdf' in self._cfg.keys():

            if 'outfname' in self._cfg['traj_netcdf']:
                outfname = self._cfg['traj_netcdf']['outfname']
            else:
                outfname = self._outfname

            #Store as an attribute for calculating time/frame
            if 'reportInterval' in self._cfg['traj_netcdf'].keys():
                self.trajectory_interval = self._cfg['traj_netcdf']['reportInterval']

            traj_netcdf = NetCDF4Reporter(outfname+'.nc', **self._cfg['traj_netcdf'])
            Reporters.append(traj_netcdf)

        if 'restart' in self._cfg.keys():

            if 'outfname' in self._cfg['restart']:
                outfname = self._cfg['restart']['outfname']
            else:
                outfname = self._outfname

            restart =  parmed.openmm.reporters.RestartReporter(outfname+'.rst7', netcdf=True, **self._cfg['restart'])
            Reporters.append(restart)

        if 'progress' in self._cfg.keys():

            if 'outfname' in self._cfg['progress']:
                outfname = self._cfg['progress']['outfname']
            else:
                outfname = self._outfname

            progress = parmed.openmm.reporters.ProgressReporter(outfname+'.prog', **self._cfg['progress'])
            Reporters.append(progress)

        if 'stream' in self._cfg.keys():
            stream = blues.reporters.BLUESStateDataReporter(self._logger, **self._cfg['stream'])
            Reporters.append(stream)

        return Reporters

######################
#     REPORTERS      #
######################

class BLUESHDF5Reporter(HDF5Reporter):
    """This is a subclass of the HDF5 class from mdtraj that handles
    reporting of the trajectory.

    HDF5Reporter stores a molecular dynamics trajectory in the HDF5 format.
    This object supports saving all kinds of information from the simulation --
    more than any other trajectory format. In addition to all of the options,
    the topology of the system will also (of course) be stored in the file. All
    of the information is compressed, so the size of the file is not much
    different than DCD, despite the added flexibility.
    Parameters
    ----------
    file : str, or HDF5TrajectoryFile
        Either an open HDF5TrajecoryFile object to write to, or a string
        specifying the filename of a new HDF5 file to save the trajectory to.
    title : str,
        String to specify the title of the HDF5 tables
    frame_indices : list, frame numbers for writing the trajectory
    reportInterval : int
        The interval (in time steps) at which to write frames.
    coordinates : bool
        Whether to write the coordinates to the file.
    time : bool
        Whether to write the current time to the file.
    cell : bool
        Whether to write the current unit cell dimensions to the file.
    potentialEnergy : bool
        Whether to write the potential energy to the file.
    kineticEnergy : bool
        Whether to write the kinetic energy to the file.
    temperature : bool
        Whether to write the instantaneous temperature to the file.
    velocities : bool
        Whether to write the velocities to the file.
    atomSubset : array_like, default=None
        Only write a subset of the atoms, with these (zero based) indices
        to the file. If None, *all* of the atoms will be written to disk.
    protocolWork : bool=False,
        Write the protocolWork for the alchemical process in the NCMC simulation
    alchemicalLambda : bool=False,
        Write the alchemicalLambda step for the alchemical process in the NCMC simulation.
    parameters : dict
        Dict of the simulation parameters. Useful for record keeping.
    environment : bool
        True will attempt to export your conda environment to JSON and
        store the information in the HDF5 file. Useful for record keeping.

    Notes
    -----
    If you use the ``atomSubset`` option to write only a subset of the atoms
    to disk, the ``kineticEnergy``, ``potentialEnergy``, and ``temperature``
    fields will not change. They will still refer to the energy and temperature
    of the *whole* system, and are not "subsetted" to only include the energy
    of your subsystem.

    """

    @property
    def backend(self):
        return BLUESHDF5TrajectoryFile

    def __init__(self, file, reportInterval=1,
                 title='NCMC Trajectory',
                 coordinates=True, frame_indices=[],
                 time=False, cell=True, temperature=False,
                 potentialEnergy=False, kineticEnergy=False,
                 velocities=False, atomSubset=None,
                 protocolWork=True, alchemicalLambda=True,
                 parameters=None, environment=True):

        super(BLUESHDF5Reporter, self).__init__(file, reportInterval,
            coordinates, time, cell, potentialEnergy, kineticEnergy,
            temperature, velocities, atomSubset)
        self._protocolWork = bool(protocolWork)
        self._alchemicalLambda = bool(alchemicalLambda)

        self._environment = bool(environment)
        self._title = title
        self._parameters = parameters

        self.frame_indices = frame_indices
        if self.frame_indices:
            #If simulation.currentStep = 1, store the frame from the previous step.
            # i.e. frame_indices=[1,100] will store the first and frame 100
            self.frame_indices = [x-1 for x in frame_indices]

    def describeNextReport(self, simulation):
        """
        Get information about the next report this object will generate.
        Parameters
        ----------
        simulation : :class:`app.Simulation`
            The simulation to generate a report for
        Returns
        -------
        nsteps, pos, vel, frc, ene : int, bool, bool, bool, bool
            nsteps is the number of steps until the next report
            pos, vel, frc, and ene are flags indicating whether positions,
            velocities, forces, and/or energies are needed from the Context
        """
        #Monkeypatch to report at certain frame indices
        if self.frame_indices:
            if simulation.currentStep in self.frame_indices:
                steps = 1
            else:
                steps = -1
        if not self.frame_indices:
            steps_left = simulation.currentStep % self._reportInterval
            steps = self._reportInterval - steps_left
        return (steps, self._coordinates, self._velocities, False, self._needEnergy)

    def report(self, simulation, state):
        """Generate a report.
        Parameters
        ----------
        simulation : simtk.openmm.app.Simulation
            The Simulation to generate a report for
        state : simtk.openmm.State
            The current state of the simulation
        """
        if not self._is_intialized:
            self._initialize(simulation)
            self._is_intialized = True

        self._checkForErrors(simulation, state)

        args = ()
        kwargs = {}
        if self._coordinates:
            coordinates = state.getPositions(asNumpy=True)[self._atomSlice]
            coordinates = coordinates.value_in_unit(getattr(units, self._traj_file.distance_unit))
            args = (coordinates,)
        if self._time:
            kwargs['time'] = state.getTime()
        if self._cell:
            vectors = state.getPeriodicBoxVectors(asNumpy=True)
            vectors = vectors.value_in_unit(getattr(units, self._traj_file.distance_unit))
            a, b, c, alpha, beta, gamma = unitcell.box_vectors_to_lengths_and_angles(*vectors)
            kwargs['cell_lengths'] = np.array([a, b, c])
            kwargs['cell_angles'] = np.array([alpha, beta, gamma])
        if self._potentialEnergy:
            kwargs['potentialEnergy'] = state.getPotentialEnergy()
        if self._kineticEnergy:
            kwargs['kineticEnergy'] = state.getKineticEnergy()
        if self._temperature:
            kwargs['temperature'] = 2*state.getKineticEnergy()/(self._dof*units.MOLAR_GAS_CONSTANT_R)
        if self._velocities:
            kwargs['velocities'] = state.getVelocities(asNumpy=True)[self._atomSlice, :]

        #add a portion like this to store things other than the protocol work
        if self._protocolWork:
            protocol_work = simulation.integrator.get_protocol_work(dimensionless=True)
            kwargs['protocolWork'] = np.array([protocol_work])
        if self._alchemicalLambda:
            kwargs['alchemicalLambda'] = np.array([simulation.integrator.getGlobalVariableByName('lambda')])
        if self._title:
            kwargs['title'] = self._title
        if self._parameters:
            kwargs['parameters'] = self._parameters
        if self._environment:
            kwargs['environment'] = self._environment

        self._traj_file.write(*args, **kwargs)
        # flush the file to disk. it might not be necessary to do this every
        # report, but this is the most proactive solution. We don't want to
        # accumulate a lot of data in memory only to find out, at the very
        # end of the run, that there wasn't enough space on disk to hold the
        # data.
        if hasattr(self._traj_file, 'flush'):
            self._traj_file.flush()

class BLUESStateDataReporter(app.StateDataReporter):
    """StateDataReporter outputs information about a simulation, such as energy and temperature, to a file.
    To use it, create a StateDataReporter, then add it to the Simulation's list of reporters.  The set of
    data to write is configurable using boolean flags passed to the constructor.  By default the data is
    written in comma-separated-value (CSV) format, but you can specify a different separator to use.
    """
    def __init__(self, file, reportInterval=1, frame_indices=[], title='', step=False, time=False, potentialEnergy=False, kineticEnergy=False, totalEnergy=False,   temperature=False, volume=False, density=False,
    progress=False, remainingTime=False, speed=False, elapsedTime=False, separator='\t', systemMass=None, totalSteps=None):
        super(BLUESStateDataReporter, self).__init__(file, reportInterval, step, time,
            potentialEnergy, kineticEnergy, totalEnergy, temperature, volume, density,
            progress, remainingTime, speed, elapsedTime, separator, systemMass, totalSteps)
        """Create a StateDataReporter.
        Inherited from `openmm.app.StateDataReporter`

        Parameters
        ----------
        file : string or file
            The file to write to, specified as a file name or file-like object (Logger)
        reportInterval : int
            The interval (in time steps) at which to write frames
        frame_indices : list, frame numbers for writing the trajectory
        title : str,
            Text prefix for each line of the report. Used to distinguish
            between the NCMC and MD simulation reports.
        step : bool=False
            Whether to write the current step index to the file
        time : bool=False
            Whether to write the current time to the file
        potentialEnergy : bool=False
            Whether to write the potential energy to the file
        kineticEnergy : bool=False
            Whether to write the kinetic energy to the file
        totalEnergy : bool=False
            Whether to write the total energy to the file
        temperature : bool=False
            Whether to write the instantaneous temperature to the file
        volume : bool=False
            Whether to write the periodic box volume to the file
        density : bool=False
            Whether to write the system density to the file
        progress : bool=False
            Whether to write current progress (percent completion) to the file.
            If this is True, you must also specify totalSteps.
        remainingTime : bool=False
            Whether to write an estimate of the remaining clock time until
            completion to the file.  If this is True, you must also specify
            totalSteps.
        speed : bool=False
            Whether to write an estimate of the simulation speed in ns/day to
            the file
        elapsedTime : bool=False
            Whether to write the elapsed time of the simulation in seconds to
            the file.
        separator : string=','
            The separator to use between columns in the file
        systemMass : mass=None
            The total mass to use for the system when reporting density.  If
            this is None (the default), the system mass is computed by summing
            the masses of all particles.  This parameter is useful when the
            particle masses do not reflect their actual physical mass, such as
            when some particles have had their masses set to 0 to immobilize
            them.
        totalSteps : int=None
            The total number of steps that will be included in the simulation.
            This is required if either progress or remainingTime is set to True,
            and defines how many steps will indicate 100% completion.
        """
        self.log = self._out
        self.title = title

        self.frame_indices = frame_indices
        if self.frame_indices:
            #If simulation.currentStep = 1, store the frame from the previous step.
            # i.e. frame_indices=[1,100] will store the first and frame 100
            self.frame_indices = [x-1 for x in frame_indices]

    def describeNextReport(self, simulation):
        """
        Get information about the next report this object will generate.
        Parameters
        ----------
        simulation : :class:`app.Simulation`
            The simulation to generate a report for
        Returns
        -------
        nsteps, pos, vel, frc, ene : int, bool, bool, bool, bool
            nsteps is the number of steps until the next report
            pos, vel, frc, and ene are flags indicating whether positions,
            velocities, forces, and/or energies are needed from the Context
        """
        #Monkeypatch to report at certain frame indices
        if self.frame_indices:
            if simulation.currentStep in self.frame_indices:
                steps = 1
            else:
                steps = -1
        if not self.frame_indices:
            steps_left = simulation.currentStep % self._reportInterval
            steps = self._reportInterval - steps_left

        return (steps, self._needsPositions, self._needsVelocities,
                self._needsForces, self._needEnergy)

    def report(self, simulation, state):
        """Generate a report.
        Parameters
        ----------
        simulation : Simulation
            The Simulation to generate a report for
        state : State
            The current state of the simulation
        """
        if not self._hasInitialized:
            self._initializeConstants(simulation)
            headers = self._constructHeaders()
            self.log.report('#"%s"' % ('"'+self._separator+'"').join(headers))
            try:
                self._out.flush()
            except AttributeError:
                pass
            self._initialClockTime = time.time()
            self._initialSimulationTime = state.getTime()
            self._initialSteps = simulation.currentStep
            self._hasInitialized = True

        # Check for errors.
        self._checkForErrors(simulation, state)
        # Query for the values
        values = self._constructReportValues(simulation, state)

        # Write the values.
        self.log.report('%s: %s' % (self.title, self._separator.join(str(v) for v in values)))
        try:
            self._out.flush()
        except AttributeError:
            pass

class NetCDF4Reporter(parmed.openmm.reporters.NetCDFReporter):
    """
    Class to read or write NetCDF trajectory files
    """

    def __init__(self, file, reportInterval=1, frame_indices=[], crds=True, vels=False, frcs=False,
                protocolWork=False, alchemicalLambda=False):
        """
        Create a NetCDFReporter instance.
        Inherited from `parmed.openmm.reporters.NetCDFReporter`

        Parameters
        ----------
        file : str
            Name of the file to write the trajectory to
        reportInterval : int
            How frequently to write a frame to the trajectory
        frame_indices : list, frame numbers for writing the trajectory
        crds : bool=True
            Should we write coordinates to this trajectory? (Default True)
        vels : bool=False
            Should we write velocities to this trajectory? (Default False)
        frcs : bool=False
            Should we write forces to this trajectory? (Default False)
        protocolWork : bool=False,
            Write the protocolWork for the alchemical process in the NCMC simulation
        alchemicalLambda : bool=False,
            Write the alchemicalLambda step for the alchemical process in the NCMC simulation.
        """
        super(NetCDF4Reporter,self).__init__(file, reportInterval, crds, vels, frcs)
        self.crds, self.vels, self.frcs, self.protocolWork, self.alchemicalLambda = crds, vels, frcs, protocolWork, alchemicalLambda
        self.frame_indices = frame_indices
        if self.frame_indices:
            #If simulation.currentStep = 1, store the frame from the previous step.
            # i.e. frame_indices=[1,100] will store the first and frame 100
            self.frame_indices = [x-1 for x in frame_indices]

    def describeNextReport(self, simulation):
        """
        Get information about the next report this object will generate.
        Parameters
        ----------
        simulation : :class:`app.Simulation`
            The simulation to generate a report for
        Returns
        -------
        nsteps, pos, vel, frc, ene : int, bool, bool, bool, bool
            nsteps is the number of steps until the next report
            pos, vel, frc, and ene are flags indicating whether positions,
            velocities, forces, and/or energies are needed from the Context
        """
        #Monkeypatch to report at certain frame indices
        if self.frame_indices:
            if simulation.currentStep in self.frame_indices:
                steps = 1
            else:
                steps = -1
        if not self.frame_indices:
            steps_left = simulation.currentStep % self._reportInterval
            steps = self._reportInterval - steps_left
        return (steps, self.crds, self.vels, self.frcs, False)

    def report(self, simulation, state):
        """Generate a report.
        Parameters
        ----------
        simulation : :class:`app.Simulation`
            The Simulation to generate a report for
        state : :class:`mm.State`
            The current state of the simulation
        """
        global VELUNIT, FRCUNIT
        if self.crds:
            crds = state.getPositions().value_in_unit(u.angstrom)
        if self.vels:
            vels = state.getVelocities().value_in_unit(VELUNIT)
        if self.frcs:
            frcs = state.getForces().value_in_unit(FRCUNIT)
        if self.protocolWork:
            protocolWork = simulation.integrator.get_protocol_work(dimensionless=True)
        if self.alchemicalLambda:
            alchemicalLambda = simulation.integrator.getGlobalVariableByName('lambda')
        if self._out is None:
            # This must be the first frame, so set up the trajectory now
            if self.crds:
                atom = len(crds)
            elif self.vels:
                atom = len(vels)
            elif self.frcs:
                atom = len(frcs)
            self.uses_pbc = simulation.topology.getUnitCellDimensions() is not None
            self._out = NetCDF4Traj.open_new(
                    self.fname, atom, self.uses_pbc, self.crds, self.vels,
                    self.frcs, title="ParmEd-created trajectory using OpenMM",
                    protocolWork=self.protocolWork, alchemicalLambda=self.alchemicalLambda,
            )

        if self.uses_pbc:
            vecs = state.getPeriodicBoxVectors()
            lengths, angles = box_vectors_to_lengths_and_angles(*vecs)
            self._out.add_cell_lengths_angles(lengths.value_in_unit(u.angstrom),
                                              angles.value_in_unit(u.degree))

        # Add the coordinates, velocities, and/or forces as needed
        if self.crds:
            self._out.add_coordinates(crds)
        if self.vels:
            # The velocities get scaled right before writing
            self._out.add_velocities(vels)
        if self.frcs:
            self._out.add_forces(frcs)
        if self.protocolWork:
            self._out.add_protocolWork(protocolWork)
        if self.alchemicalLambda:
            self._out.add_alchemicalLambda(alchemicalLambda)
        # Now it's time to add the time.
        self._out.add_time(state.getTime().value_in_unit(u.picosecond))
