
# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Base class for dispatchers.
"""
from ravenframework.utils import InputData, InputTypes
from ravenframework.BaseClasses import MessageUser, InputDataUser

class Dispatcher(MessageUser, InputDataUser):
  """
    Base class for strategies for consecutive dispatching in a continuous period.
  """
  # ---------------------------------------------
  # INITIALIZATION
  @classmethod
  def get_input_specs(cls):
    """
      Set acceptable input specifications.
      @ In, None
      @ Out, specs, InputData, specs
    """
    specs = InputData.parameterInputFactory('Dispatcher', ordered=False, baseNode=None)
    # only place specifications that are true for ALL dispatchers here
    return specs

  def __init__(self):
    """
      Constructor.
      @ In, None
      @ Out, None
    """
    super().__init__()
    self.name = 'BaseDispatcher'
    self._time_discretization = None # (start, end, num_steps) to build time discretization
    self._validator = None           # can be used to validate activity
    self._solver = None
    self._eps = 1e-9                 # small constant to add to denominator

  def read_input(self, inputs):
    """
      Loads settings based on provided inputs
      @ In, inputs, InputData.InputSpecs, input specifications
      @ Out, None
    """
    pass # add here if true for ALL dispatchers

  def initialize(self, case, components, sources, **kwargs):
    """
      Initialize dispatcher properties.
      @ In, case, Case, HERON case instance
      @ In, components, list, HERON components
      @ In, sources, list, HERON sources
      @ In, kwargs, dict, keyword arguments
      @ Out, None
    """
    pass

  # ---------------------------------------------
  # GETTER AND SETTERS
  def get_time_discr(self):
    """
      Retrieves the time discretization information.
      @ In, None
      @ Out, info, tuple, (start, end, number of steps) for time discretization
    """
    return self._time_discretization

  def set_time_discr(self, info):
    """
      Stores the time discretization information.
      @ In, info, tuple, (start, end, number of steps) for time discretization
      @ Out, None
    """
    assert info is not None
    # TODO is this the right idea?
    # don't expand into linspace right now, just store the pieces
    self._time_discretization = info

  def set_validator(self, validator):
    """
      Sets the dispatch validation instance to use in dispatching.
      @ In, validator, HERON Validator, instance of validator
      @ Out, None
    """
    self._validator = validator

  def get_solver(self):
    """
      Retrieves the solver information (if applicable)
      @ In, None
      @ Out, solver, str, name of solver used
    """
    return self._solver

  # ---------------------------------------------
  # API
  # TODO make this a virtual method?
  def dispatch(self, case, components, sources):
    """
      Performs technoeconomic dispatch.
      @ In, case, Case, HERON case
      @ In, components, list, HERON components
      @ In, sources, list, HERON sources
      @ Out, results, dict, economic and production metrics
    """
    raise NotImplementedError # must be implemented by inheriting classes

  def validate(self, components, activity, times, meta):
    """
      Method to validate a dispatch activity.
      @ In, components, list, HERON components whose cashflows should be evaluated
      @ In, activity, DispatchState instance, activity by component/resources/time
      @ In, times, np.array(float), time values to evaluate; may be length 1 or longer
      @ In, meta, dict, extra information needed for validation
      @ Out, validation, dict, information about validation
    """
    # default implementation
    if self._validator is not None:
      return self._validator.validate(components, activity, times, meta)
    else:
      # no validator, nothing needs to be changed
      return {}

  # ---------------------------------------------
  # UTILITY METHODS
  def _compute_cashflows(self, components, activity, times, meta, state_args=None, time_offset=0):
    """
      Method to compute CashFlow evaluations given components and their activity.
      @ In, components, list, HERON components whose cashflows should be evaluated
      @ In, activity, DispatchState instance, activity by component/resources/time
      @ In, times, np.array(float), time values to evaluate; may be length 1 or longer
      @ In, meta, dict, additional info to be passed through to functional evaluations
      @ In, state_args, dict, optional, additional arguments to pass while getting activity state
      @ In, time_offset, int, optional, increase time index tracker by this value if provided
      @ Out, total, float, total cashflows for given components
    """
    if state_args is None:
      state_args = {}

    if meta['HERON']['Case'].use_levelized_inner:
      total = self._compute_levelized_cashflows(components, activity, times, meta, state_args, time_offset)
      return total

    total = 0
    specific_meta = dict(meta) # TODO what level of copying do we need here?
    resource_indexer = meta['HERON']['resource_indexer']

    #print('DEBUGG computing cashflows!')
    for comp in components:
      #print(f'DEBUGG ... comp {comp.name}')
      specific_meta['HERON']['component'] = comp
      comp_subtotal = 0
      for t, time in enumerate(times):
        #print(f'DEBUGG ... ... time {t}')
        # NOTE care here to assure that pyomo-indexed variables work here too
        specific_activity = {}
        for tracker in comp.get_tracking_vars():
          specific_activity[tracker] = {}
          for resource in resource_indexer[comp]:
            specific_activity[tracker][resource] = activity.get_activity(comp, tracker, resource, time, **state_args)
        specific_meta['HERON']['time_index'] = t + time_offset
        specific_meta['HERON']['time_value'] = time
        cfs = comp.get_state_cost(specific_activity, specific_meta, marginal=True)
        time_subtotal = sum(cfs.values())
        comp_subtotal += time_subtotal
      total += comp_subtotal
    return total

  def _compute_levelized_cashflows(self, components, activity, times, meta, state_args=None, time_offset=0):
    """
      Method to compute CashFlow evaluations given components and their activity.
      @ In, components, list, HERON components whose cashflows should be evaluated
      @ In, activity, DispatchState instance, activity by component/resources/time
      @ In, times, np.array(float), time values to evaluate; may be length 1 or longer
      @ In, meta, dict, additional info to be passed through to functional evaluations
      @ In, state_args, dict, optional, additional arguments to pass while getting activity state
      @ In, time_offset, int, optional, increase time index tracker by this value if provided
      @ Out, total, float, total cashflows for given components
    """
    total = 0
    specific_meta = dict(meta) # TODO what level of copying do we need here?
    resource_indexer = meta['HERON']['resource_indexer']

    # How does this work?
    #   The general equation looks like:
    #
    #     SUM(Non-Multiplied Terms) + x * SUM(Multiplied Terms) = Target
    #
    #   and we are solving for `x`. Target is 0 by default. Terms here are marginal cashflows.
    #   Summations here occur over: components, time steps, tracking variables, and resources.
    #   Typically, there is only 1 multiplied term/cash flow.

    multiplied = 0
    non_multiplied = 0

    for comp in components:
      specific_meta['HERON']['component'] = comp
      multiplied_comp = 0
      non_multiplied_comp = 0
      for t, time in enumerate(times):
        # NOTE care here to assure that pyomo-indexed variables work here too
        specific_activity = {}
        for tracker in comp.get_tracking_vars():
          specific_activity[tracker] = {}
          for resource in resource_indexer[comp]:
            specific_activity[tracker][resource] = activity.get_activity(comp, tracker, resource, time, **state_args)
        specific_meta['HERON']['time_index'] = t + time_offset
        specific_meta['HERON']['time_value'] = time
        cfs = comp.get_state_cost(specific_activity, specific_meta, marginal=True)

        # there is an assumption here that if a component has a levelized cost, marginal cashflow
        # then it is the only marginal cashflow
        if comp.levelized_meta:
          for cf in comp.levelized_meta.keys():
            lcf = cfs.pop(cf) # this should be ok as long as HERON init checks are successful
            multiplied_comp += lcf
        else:
          time_subtotal = sum(cfs.values())
          non_multiplied_comp += time_subtotal

      multiplied     += multiplied_comp
      non_multiplied += non_multiplied_comp

    # at this point, there should be a not None NPV Target
    multiplied += self._eps
    total = (meta['HERON']['Case'].npv_target - non_multiplied) / multiplied
    total *= -1
    return total
