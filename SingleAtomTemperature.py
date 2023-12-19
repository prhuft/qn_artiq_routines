"""
Two-shot experiment with a varying time between the shots over which the trap is turned off before being turned back
on just before the second shot
"""

from artiq.experiment import *
import csv
import numpy as np
from datetime import datetime as dt

from utilities.BaseExperiment import BaseExperiment


class SingleAtomTemperature(EnvExperiment):

    def build(self):
        """
        declare hardware and user-configurable independent variables
        """
        self.base = BaseExperiment(experiment=self)
        self.base.build()

        # this is an argument for using a scan package, maybe
        self.scan_datasets = ["t_delay_between_shots_sequence"]
        try:
            for dataset in self.scan_datasets:
                value = self.get_dataset(dataset)
                self.setattr_argument(dataset, StringValue(value))
        except KeyError as e:
            print(e)
            self.setattr_argument("t_delay_between_shots_sequence", StringValue(
                'np.array([1.0, 10.0, 50.0, 100.])*us'))

        self.setattr_argument("n_measurements", NumberValue(10, ndecimals=0, step=1))
        self.setattr_argument("no_first_shot", BooleanValue(False))
        self.setattr_argument("do_PGC_in_MOT", BooleanValue(False))
        self.setattr_argument("bins", NumberValue(50, ndecimals=0, step=1), "Histogram setup (set bins=0 for auto)")
        self.setattr_argument("enable_laser_feedback", BooleanValue(default=True),"Laser power stabilization")

        self.base.set_datasets_from_gui_args()
        print("build - done")

    def prepare(self):
        """
        performs initial calculations and sets parameter values before
        running the experiment. also sets data filename for now.

        any conversions from human-readable units to machine units (mu) are done here
        """
        self.base.prepare()

        self.t_exp_trigger = 1*ms

        self.sampler_buffer = np.full(8, 0.0)
        self.cooling_volts_ch = 7

        self.t_delay_between_shots_list = eval(self.t_delay_between_shots_sequence)
        self.n_iterations = len(self.t_delay_between_shots_list)

        print("prepare - done")

    @kernel
    def run(self):
        self.base.initialize_hardware()
        self.expt()
        print("Experiment finished.")

    @kernel
    def expt(self):
        """
        The experiment loop.

        :return:
        """
        self.zotino0.set_dac([0.0, 0.0, 0.0, 0.0],  # voltages must be floats or ARTIQ complains
                             channels=self.coil_channels)

        # todo: these are going to be regularly used, so put these in the base experiment
        self.set_dataset("photocounts", [0])
        self.set_dataset("photocounts2", [0])

        self.set_dataset("photocount_bins", [self.bins], broadcast=True)


        # turn on cooling MOT AOMs
        self.dds_cooling_DP.sw.on() # cooling double pass
        self.dds_AOM_A2.sw.on()
        self.dds_AOM_A3.sw.on()
        self.dds_AOM_A1.sw.on()
        self.dds_AOM_A6.sw.on()
        self.dds_AOM_A4.sw.on()
        self.dds_AOM_A5.sw.on()
        delay(1 * ms)

        delay(2000*ms) # wait for AOMS to thermalize in case they have been off.

        if self.enable_laser_feedback:
            self.laser_stabilizer.run()
        delay(1*ms)

        counts = 0
        counts2 = 0

        iteration = 0
        for t_delay_between_shots in self.t_delay_between_shots_list:

            # these are the datasets for plotting only, an we restart them each iteration
            self.set_dataset("photocounts_current_iteration", [0], broadcast=True)
            self.set_dataset("photocounts2_current_iteration", [0], broadcast=True)

            # loop the experiment sequence
            for measurement in range(self.n_measurements):

                if self.enable_laser_feedback:
                    if measurement % 10 == 0:
                        self.laser_stabilizer.run()
                        delay(1 * ms)
                    self.dds_FORT.sw.on()
                    self.dds_FORT.set(frequency=self.f_FORT - 30 * MHz, amplitude=self.ampl_FORT_loading)

                self.ttl7.pulse(self.t_exp_trigger) # in case we want to look at signals on an oscilloscope

                # Turn on the MOT coils and cooling light
                self.zotino0.set_dac(
                    [self.AZ_bottom_volts_MOT, self.AZ_top_volts_MOT, self.AX_volts_MOT, self.AY_volts_MOT],
                    channels=self.coil_channels)
                # delay(2 * ms)
                self.dds_cooling_DP.sw.on()

                # wait for the MOT to load
                delay_mu(self.t_MOT_loading_mu)

                # try loading from a PGC phase
                if self.do_PGC_in_MOT:
                    self.zotino0.set_dac([0.0, 0.0, 0.0, 0.0], channels=self.coil_channels)
                    self.dds_cooling_DP.set(frequency=self.f_cooling_DP_PGC, amplitude=self.ampl_cooling_DP_MOT)
                    delay(self.t_PGC_in_MOT)

                # turn on the dipole trap and wait to load atoms
                self.dds_FORT.set(frequency=self.f_FORT, amplitude=self.ampl_FORT_loading)
                delay_mu(self.t_FORT_loading_mu)

                # turn off the coils
                if not self.do_PGC_in_MOT:
                    self.zotino0.set_dac([0.0, 0.0, 0.0, 0.0], channels=self.coil_channels)

                delay(3*ms) # should wait for the MOT to dissipate

                # set the cooling DP AOM to the readout settings
                self.dds_cooling_DP.set(frequency=self.f_cooling_DP_RO, amplitude=self.ampl_cooling_DP_MOT)

                if not self.no_first_shot:
                    # take the first shot
                    self.dds_cooling_DP.sw.on()
                    t_gate_end = self.ttl0.gate_rising(self.t_SPCM_first_shot)
                    counts = self.ttl0.count(t_gate_end)
                    delay(1*ms)
                    self.dds_cooling_DP.sw.off()

                # turn the FORT off
                self.dds_FORT.set(frequency=self.f_FORT - 30 * MHz, amplitude=self.ampl_FORT_loading)

                delay(t_delay_between_shots)

                # turn the FORT on
                self.dds_FORT.set(frequency=self.f_FORT, amplitude=self.ampl_FORT_loading)
                delay(1*ms)

                # take the second shot
                self.dds_cooling_DP.sw.on()
                t_gate_end = self.ttl0.gate_rising(self.t_SPCM_second_shot)
                counts2 = self.ttl0.count(t_gate_end)
                delay(1*ms)
                self.dds_cooling_DP.sw.off()

                # todo: check the FORT extinction ratio here
                # effectively turn the FORT AOM off
                self.dds_FORT.set(frequency=self.f_FORT - 30 * MHz, amplitude=self.ampl_FORT_loading)
                # set the cooling DP AOM to the MOT settings
                self.dds_cooling_DP.set(frequency=self.f_cooling_DP_MOT, amplitude=self.ampl_cooling_DP_MOT)

                delay(2*ms)

                iteration += 1

                # update the datasets
                if not self.no_first_shot:
                    self.append_to_dataset('photocounts', counts)
                    self.append_to_dataset('photocounts_current_iteration', counts)

                # update the datasets
                self.append_to_dataset('photocounts2', counts2)
                self.append_to_dataset('photocounts2_current_iteration', counts2)
                self.set_dataset("iteration", iteration, broadcast=True)

        delay(1*ms)
        # leave MOT on at end of experiment, but turn off the FORT
        self.dds_cooling_DP.sw.on()
        self.zotino0.set_dac([self.AZ_bottom_volts_MOT, self.AZ_top_volts_MOT, self.AX_volts_MOT, self.AY_volts_MOT],
                             channels=self.coil_channels)