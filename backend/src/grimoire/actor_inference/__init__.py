"""Isolated, optional actor presentation experiments; no canonical writes."""
from .plan import plan_experiment
from .budget import ExperimentBudget
from .run import ActorExperimentRunner, ExperimentOutcome, ActorProposal, ExperimentCall

__all__ = ['plan_experiment', 'ExperimentBudget', 'ActorExperimentRunner', 'ExperimentOutcome',
           'ActorProposal', 'ExperimentCall']
from .report import (record_experiment, compare_experiments, synthetic_benchmark, BenchmarkInput,
                     ExperimentReport, SyntheticBenchmark)
__all__ += ['record_experiment', 'compare_experiments', 'synthetic_benchmark', 'BenchmarkInput',
            'ExperimentReport', 'SyntheticBenchmark']
