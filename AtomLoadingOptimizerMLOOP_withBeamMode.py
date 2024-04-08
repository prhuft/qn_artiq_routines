"""
Atom loading optimization which positions the MOT using M-LOOP
"""

from artiq.experiment import *
import numpy as np
import scipy as sp

#Imports for M-LOOP
import mloop.interfaces as mli
import mloop.controllers as mlc
import mloop.visualizations as mlv

from utilities.BaseExperiment import BaseExperiment


# Declare your custom class that inherits from the Interface class
class MLOOPInterface(mli.Interface):

    # Initialization of the interface, including this method is optional
    def __init__(self):
        # You must include the super command to call the parent class, Interface, constructor
        super(MLOOPInterface, self).__init__()

        # Attributes of the interface can be added here
        # If you want to precalculate any variables etc. this is the place to do it
        # In this example we will just define the location of the minimum
        self.minimum_params = np.array([0, 0.1, -0.1])

    # You must include the get_next_cost_dict method in your class
    # this method is called whenever M-LOOP wants to run an experiment
    def get_next_cost_dict(self, params_dict):
        pass


class AtomLoadingOptimizerMLOOP(EnvExperiment):

    def build(self):
        """
        declare hardware and user-configurable independent variables
        """
        self.base = BaseExperiment(experiment=self)
        self.base.build()

        # overwrite the experiment variables of the same names
        # self.setattr_argument("t_SPCM_exposure", NumberValue(10 * ms, unit='ms'))
        self.setattr_argument("atom_counts_per_s_threshold", NumberValue(30000))
        self.setattr_argument("t_MOT_loading", NumberValue(500 * ms, unit='ms'))
        self.setattr_argument("n_measurements", NumberValue(400, type='int', scale=1, ndecimals=0, step=1))
        self.setattr_argument("set_best_parameters_at_finish", BooleanValue(True))
        self.both_mode = "coils and beam powers"
        self.beam_mode = "beam powers only"
        self.coil_mode = "coils only"
        self.setattr_argument("what_to_tune", EnumerationValue([self.both_mode, self.coil_mode, self.beam_mode]))

        group = "differential coil volts boundaries"
        self.setattr_argument("use_differential_boundaries",BooleanValue(True),group)
        self.setattr_argument("dV_AZ_bottom", NumberValue(0.05, unit="V"), group)
        self.setattr_argument("dV_AZ_top", NumberValue(0.05, unit="V"), group)
        self.setattr_argument("dV_AX", NumberValue(0.05, unit="V"), group)
        self.setattr_argument("dV_AY", NumberValue(0.05, unit="V"), group)
        group = "absolute coil volts boundaries"
        self.setattr_argument("V_AZ_bottom_min", NumberValue(3.5,unit="V"), group)
        self.setattr_argument("V_AZ_bottom_max", NumberValue(4.8,unit="V"), group)
        self.setattr_argument("V_AZ_top_min", NumberValue(4.8,unit="V"), group)
        self.setattr_argument("V_AZ_top_max", NumberValue(4.8,unit="V"), group)
        self.setattr_argument("V_AX_min", NumberValue(-0.5,unit="V"), group)
        self.setattr_argument("V_AX_max", NumberValue(0.5,unit="V"), group)
        self.setattr_argument("V_AY_min", NumberValue(-0.5, unit="V"), group)
        self.setattr_argument("V_AY_max", NumberValue(0.5, unit="V"), group)

        group = "beam tuning settings"

        self.setattr_argument("enable_beam_balance_tuning",
                              NumberValue(0.2), group)

        self.setattr_argument("max_RF_ampl_percent_deviation",
                              NumberValue(0.2), group)

        # we can balance the z beams with confidence by measuring the powers outside the chamber,
        # so unless we are fine tuning loading, we may want trust our initial manual balancing
        self.setattr_argument("disable_z_beam_tuning",
                              BooleanValue(True), group)

        group1 = "optimizer settings"
        self.setattr_argument("max_runs",NumberValue(70, type='int', scale=1, ndecimals=0, step=1),group1)

        # this should be close to the mean signal from the atom
        self.base.set_datasets_from_gui_args()
        print("build - done")

    def prepare(self):
        self.base.prepare()

        self.sampler_buffer = np.zeros(8)

        self.atom_counts_threshold = self.atom_counts_per_s_threshold*self.t_SPCM_exposure

        # whether to use boundaries which are defined as +/- dV around the current settings
        if self.use_differential_boundaries:
            # override the absolute boundaries
            self.V_AZ_bottom_min = self.AZ_bottom_volts_MOT - self.dV_AZ_bottom
            self.V_AZ_top_min = self.AZ_top_volts_MOT - self.dV_AZ_top
            self.V_AX_min = self.AX_volts_MOT - self.dV_AX
            self.V_AY_min = self.AY_volts_MOT - self.dV_AY

            self.V_AZ_bottom_max = self.AZ_bottom_volts_MOT + self.dV_AZ_bottom
            self.V_AZ_top_max = self.AZ_top_volts_MOT + self.dV_AZ_top
            self.V_AX_max = self.AX_volts_MOT + self.dV_AX
            self.V_AY_max = self.AY_volts_MOT + self.dV_AY

        self.setpoint_datasets = ["set_point_PD1_AOM_A1", "set_point_PD2_AOM_A2", "set_point_PD3_AOM_A3",
                                  "set_point_PD4_AOM_A4", "set_point_PD5_AOM_A5", "set_point_PD6_AOM_A6"]
        self.default_setpoints = [getattr(self, dataset) for dataset in self.setpoint_datasets]

        self.counts_list = np.zeros(self.n_measurements)
        self.set_dataset(self.count_rate_dataset,
                         [0.0],
                         broadcast=True)

        self.cost_dataset = "cost"
        self.set_dataset(self.cost_dataset,
                         [0.0],
                         broadcast=True)

        # instantiate the MLOOP interface
        interface = MLOOPInterface()
        interface.get_next_cost_dict = self.get_next_cost_dict_for_mloop

        min_bounds = []
        max_bounds = []

        if self.what_to_tune == self.coil_mode or self.what_to_tune == self.both_mode:

            min_bounds += [self.V_AZ_bottom_min,
                           self.V_AZ_top_min,
                           self.V_AX_min,
                           self.V_AY_min]

            max_bounds += [self.V_AZ_bottom_max,
                           self.V_AZ_top_max,
                           self.V_AX_max,
                           self.V_AY_max]

        elif self.what_to_tune == self.beam_mode or self.what_to_tune == self.both_mode:
            min_bounds += [1 - self.max_RF_ampl_percent_deviation,
                           1 - self.max_RF_ampl_percent_deviation,
                           1 - self.max_RF_ampl_percent_deviation,
                           1 - self.max_RF_ampl_percent_deviation]

            max_bounds += [1 + self.max_RF_ampl_percent_deviation,
                           1 + self.max_RF_ampl_percent_deviation,
                           1 + self.max_RF_ampl_percent_deviation,
                           1 + self.max_RF_ampl_percent_deviation]

            if not self.disable_z_beam_tuning:
                # append additional bounds for MOT5,6
                min_bounds.append(1 - self.max_RF_ampl_percent_deviation)
                min_bounds.append(1 - self.max_RF_ampl_percent_deviation)
                max_bounds.append(1 + self.max_RF_ampl_percent_deviation)
                max_bounds.append(1 + self.max_RF_ampl_percent_deviation)
        n_params = len(max_bounds)

        self.mloop_controller = mlc.create_controller(interface,
                                           max_num_runs=self.max_runs,
                                           target_cost=-0.5*self.n_measurements, # -1 * number of atoms to load
                                           num_params=n_params,
                                           min_boundary=min_bounds,
                                           max_boundary=max_bounds)

    def run(self):
        self.initialize_hardware()
        self.warm_up()

        self.mloop_controller.optimize()

        print('Best parameters found:')
        print(self.mloop_controller.best_params)
        best_params = self.mloop_controller.best_params
        self.set_experiment_variables_to_best_params(best_params)

    @kernel
    def initialize_hardware(self):
        self.base.initialize_hardware()

    @kernel
    def warm_up(self):
        """hardware init and turn things on"""

        self.core.reset()

        self.zotino0.set_dac([self.AZ_bottom_volts_MOT, self.AZ_top_volts_MOT, self.AX_volts_MOT, self.AY_volts_MOT],
                             channels=self.coil_channels)

        self.dds_cooling_DP.sw.on()
        self.dds_AOM_A1.sw.on()
        self.dds_AOM_A2.sw.on()
        self.dds_AOM_A3.sw.on()
        self.dds_AOM_A4.sw.on()
        self.dds_AOM_A5.sw.on()
        self.dds_AOM_A6.sw.on()
        self.dds_FORT.sw.on()

        # delay for AOMs to thermalize
        delay(2 * s)

        self.core.break_realtime()
        # warm up to get make sure we get to the setpoints
        for i in range(10):
            self.laser_stabilizer.run()
        self.dds_FORT.sw.on()


    def get_cost(self, data: TArray(TFloat,1)) -> TInt32:
        q = 0 # whether the counts exceeded the atom threshold
        atoms_loaded = 0
        q_last = (data[0] > self.atom_counts_threshold)
        for x in data[1:]:
            q = x > self.atom_counts_threshold
            if q != q_last and q_last:
                atoms_loaded += 1
                # todo: replace with logging statement?
                # self.print_async("optimizer found the single atom signal!")
            q_last = q
        return -1 * atoms_loaded

    @kernel
    def optimization_routine(self, params: TArray(TFloat)) -> TInt32:
        """
        the function that will be called by the optimizer.

        for use with M-LOOP, this should be called in the interface's "get_next_cost_dict"
        method.

        params: array of float values which we are trying to optimize
        return:
            cost: the cost for the optimizer
        """

        self.core.reset()
        delay(1*ms)

        coil_values = params[:4]
        setpoint_multipliers = params[4:]

        self.zotino0.set_dac(coil_values, channels=self.coil_channels)
        delay(1*ms)

        self.stabilizer_AOM_A1.set_point = self.default_setpoints[0] * setpoint_multipliers[0]
        self.stabilizer_AOM_A2.set_point = self.default_setpoints[1] * setpoint_multipliers[1]
        self.stabilizer_AOM_A3.set_point = self.default_setpoints[2] * setpoint_multipliers[2]
        self.stabilizer_AOM_A4.set_point = self.default_setpoints[3] * setpoint_multipliers[3]
        if not self.disable_z_beam_tuning:
            self.stabilizer_AOM_A5.set_point = self.default_setpoints[4] * setpoint_multipliers[4]
            self.stabilizer_AOM_A6.set_point = self.default_setpoints[5] * setpoint_multipliers[5]

        for i in range(3):
            self.laser_stabilizer.run()

        self.dds_FORT.sw.on()

        delay(self.t_MOT_loading)

        # reset the counts dataset each run so we don't overwhelm the dashboard when plotting
        self.set_dataset(self.count_rate_dataset,[0.0],broadcast=True)

        for i in range(self.n_measurements):
            t_end = self.ttl0.gate_rising(self.t_SPCM_exposure)
            counts_per_s = self.ttl0.count(t_end) / self.t_SPCM_exposure
            delay(1 * ms)
            self.append_to_dataset(self.count_rate_dataset, counts_per_s)
            self.counts_list[i] = counts_per_s * self.t_SPCM_exposure

        cost = self.get_cost(self.counts_list)
        self.append_to_dataset(self.cost_dataset, cost)

        return cost

    def get_next_cost_dict_for_mloop(self,params_dict):

        # Get parameters from the provided dictionary
        params = params_dict['params']

        cost = self.optimization_routine(params)
        uncertainty = 1/np.sqrt(-1*cost) if cost < 0 else 0

        cost_dict = {'cost': cost, 'uncer': uncertainty}
        return cost_dict

    @kernel
    def set_experiment_variables_to_best_params(self, best_params: TArray(TFloat)):
        self.core.reset()
        delay(1 * ms)

        best_volts = best_params[:4]
        best_setpoint_multipliers = best_params[4:]

        volt_datasets = ["AZ_bottom_volts_MOT", "AZ_top_volts_MOT", "AX_volts_MOT", "AY_volts_MOT"]
        if self.set_best_parameters_at_finish:
            self.print_async("updating coil values")
            for i in range(4):
                self.set_dataset(volt_datasets[i], float(best_volts[i]), broadcast=True, persist=True)

            self.print_async("updating laser setpoints")
            if self.disable_z_beam_tuning:
                n_beams = 4
            else:
                n_beams = 6

            for i in range(n_beams):
                self.set_dataset(self.setpoint_datasets[i], self.default_setpoints[i] * best_setpoint_multipliers[i],
                                 broadcast=True,
                                 persist=True)

    def analyze(self):
        mlv.show_all_default_visualizations(self.mloop_controller)


