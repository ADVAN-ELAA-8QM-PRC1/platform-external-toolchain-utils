#!/usr/bin/python

# Copyright (c) 2013 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import datetime
import os
import threading
import time
import traceback

from utils import command_executer
from utils import timeline

from machine_manager import NonMatchingMachines
from suite_runner import SuiteRunner
from results_cache import MockResult
from results_cache import MockResultsCache
from results_cache import Result
from results_cache import ResultsCache
from results_cache import TelemetryResult


STATUS_FAILED = "FAILED"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_IMAGING = "IMAGING"
STATUS_RUNNING = "RUNNING"
STATUS_WAITING = "WAITING"
STATUS_PENDING = "PENDING"

class BenchmarkRun(threading.Thread):
  def __init__(self, name, benchmark,
               label,
               iteration,
               cache_conditions,
               machine_manager,
               logger_to_use,
               log_level,
               share_users):
    threading.Thread.__init__(self)
    self.name = name
    self._logger = logger_to_use
    self.log_level = log_level
    self.benchmark = benchmark
    self.iteration = iteration
    self.label = label
    self.result = None
    self.terminated = False
    self.retval = None
    self.run_completed = False
    self.machine_manager = machine_manager
    self.suite_runner = SuiteRunner(self._logger, self.log_level)
    self.machine = None
    self.cache_conditions = cache_conditions
    self.runs_complete = 0
    self.cache_hit = False
    self.failure_reason = ""
    self.test_args = benchmark.test_args
    self.profiler_args = self._GetExtraAutotestArgs()
    self._ce = command_executer.GetCommandExecuter(self._logger,
                                                   log_level=self.log_level)
    self.timeline = timeline.Timeline()
    self.timeline.Record(STATUS_PENDING)
    self.share_users = share_users

  def ReadCache(self):
    # Just use the first machine for running the cached version,
    # without locking it.
    self.cache = ResultsCache()
    self.cache.Init(self.label.chromeos_image,
                    self.label.chromeos_root,
                    self.benchmark.test_name,
                    self.iteration,
                    self.test_args,
                    self.profiler_args,
                    self.machine_manager,
                    self.label.board,
                    self.cache_conditions,
                    self._logger,
                    self.log_level,
                    self.label,
                    self.share_users,
                    self.benchmark.suite,
                    self.benchmark.show_all_results
                   )

    self.result = self.cache.ReadResult()
    self.cache_hit = (self.result is not None)

  def run(self):
    try:
      self.ReadCache()

      if self.result:
        self._logger.LogOutput("%s: Cache hit." % self.name)
        self._logger.LogOutput(self.result.out, print_to_console=False)
        self._logger.LogError(self.result.err, print_to_console=False)

      elif self.label.cache_only:
        self._logger.LogOutput("%s: No cache hit." % self.name)
        output = "%s: No Cache hit." % self.name
        retval = 1
        err = "No cache hit."
        self.result = Result.CreateFromRun(self._logger, self.log_level,
                                           self.label,
                                           output, err, retval,
                                           self.benchmark.show_all_results,
                                           self.benchmark.test_name,
                                           self.benchmark.suite)

      else:
        self._logger.LogOutput("%s: No cache hit." % self.name)
        self.timeline.Record(STATUS_WAITING)
        # Try to acquire a machine now.
        self.machine = self.AcquireMachine()
        self.result = self.RunTest(self.machine)

        self.cache.remote = self.machine.name
        self.cache.StoreResult(self.result)

      if self.terminated:
        return

      if not self.result.retval:
        self.timeline.Record(STATUS_SUCCEEDED)
      else:
        if self.timeline.GetLastEvent() != STATUS_FAILED:
          self.failure_reason = "Return value of test suite was non-zero."
          self.timeline.Record(STATUS_FAILED)

    except Exception, e:
      self._logger.LogError("Benchmark run: '%s' failed: %s" % (self.name, e))
      traceback.print_exc()
      if self.timeline.GetLastEvent() != STATUS_FAILED:
        self.timeline.Record(STATUS_FAILED)
        self.failure_reason = str(e)
    finally:
      if self.machine:
        if not self.machine.IsReachable():
          self._logger.LogOutput("Machine %s is not reachable, removing it."
                                 % self.machine.name)
          self.machine_manager.RemoveMachine(self.machine.name)
        self._logger.LogOutput("Releasing machine: %s" % self.machine.name)
        self.machine_manager.ReleaseMachine(self.machine)
        self._logger.LogOutput("Released machine: %s" % self.machine.name)

  def Terminate(self):
    self.terminated = True
    self.suite_runner.Terminate()
    if self.timeline.GetLastEvent() != STATUS_FAILED:
      self.timeline.Record(STATUS_FAILED)
      self.failure_reason = "Thread terminated."

  def AcquireMachine(self):
    while True:
      machine = None
      if self.terminated:
        raise Exception("Thread terminated while trying to acquire machine.")
      try:
        machine = self.machine_manager.AcquireMachine(self.label.chromeos_image,
                                                      self.label,
                                                      throw=True)

      except NonMatchingMachines:
        self.machine_manager.ForceSameImageToAllMachines(self.label)
        machine = self.machine_manager.AcquireMachine(self.label.chromeos_image,
                                                      self.label,
                                                      throw=False)

      if machine:
        self._logger.LogOutput("%s: Machine %s acquired at %s" %
                               (self.name,
                                machine.name,
                                datetime.datetime.now()))
        break
      else:
        sleep_duration = 10
        time.sleep(sleep_duration)
    return machine

  def _GetExtraAutotestArgs(self):
    if self.benchmark.perf_args and self.benchmark.suite == "telemetry":
      self._logger.LogError("Telemetry does not support profiler.")
      self.benchmark.perf_args = ""

    if self.benchmark.perf_args and self.benchmark.suite == "test_that":
      self._logger.LogError("test_that does not support profiler.")
      self.benchmark.perf_args = ""

    if self.benchmark.perf_args:
      perf_args_list = self.benchmark.perf_args.split(" ")
      perf_args_list = [perf_args_list[0]] + ["-a"] + perf_args_list[1:]
      perf_args = " ".join(perf_args_list)
      if not perf_args_list[0] in ["record", "stat"]:
        raise Exception("perf_args must start with either record or stat")
      extra_test_args = ["--profiler=custom_perf",
                             ("--profiler_args='perf_options=\"%s\"'" %
                              perf_args)]
      return " ".join(extra_test_args)
    else:
      return ""

  def RunTest(self, machine):
    self.timeline.Record(STATUS_IMAGING)
    self.machine_manager.ImageMachine(machine,
                                      self.label)
    self.timeline.Record(STATUS_RUNNING)
    [retval, out, err] = self.suite_runner.Run(machine.name,
                                                  self.label,
                                                  self.benchmark,
                                                  self.test_args,
                                                  self.profiler_args)
    self.run_completed = True
    return Result.CreateFromRun(self._logger,
                                self.log_level,
                                self.label,
                                out,
                                err,
                                retval,
                                self.benchmark.show_all_results,
                                self.benchmark.test_name,
                                self.benchmark.suite)

  def SetCacheConditions(self, cache_conditions):
    self.cache_conditions = cache_conditions


class MockBenchmarkRun(BenchmarkRun):
  """Inherited from BenchmarkRuna."""

  def ReadCache(self):
    # Just use the first machine for running the cached version,
    # without locking it.
    self.cache = MockResultsCache()
    self.cache.Init(self.label.chromeos_image,
                    self.label.chromeos_root,
                    self.benchmark.test_name,
                    self.iteration,
                    self.test_args,
                    self.profiler_args,
                    self.machine_manager,
                    self.label.board,
                    self.cache_conditions,
                    self._logger,
                    self.log_level,
                    self.label,
                    self.share_users,
                    self.benchmark.suite,
                    self.benchmark.show_all_results
                   )

    self.result = self.cache.ReadResult()
    self.cache_hit = (self.result is not None)


  def RunTest(self, machine):
    """Remove Result.CreateFromRun for testing."""
    self.timeline.Record(STATUS_IMAGING)
    self.machine_manager.ImageMachine(machine,
                                      self.label)
    self.timeline.Record(STATUS_RUNNING)
    [retval, out, err] = self.suite_runner.Run(machine.name,
                                                  self.label.chromeos_root,
                                                  self.label.board,
                                                  self.benchmark.test_name,
                                                  self.test_args,
                                                  self.profiler_args)
    self.run_completed = True
    rr = MockResult("logger", self.label, self.log_level)
    rr.out = out
    rr.err = err
    rr.retval = retval
    return rr
