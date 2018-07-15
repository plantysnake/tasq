# -*- coding: utf-8 -*-

"""
tasq.worker.py
~~~~~~~~~~~~~~
Generic worker, useful to run jobs in a single node with multiple core available.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import sys
import uuid
import signal
import logging
from threading import Thread
from abc import ABCMeta, abstractmethod
from multiprocessing import Process

from .remote.sockets import pickle_and_compress, decompress_and_unpickle


_fmt = logging.Formatter('%(message)s', '%Y-%m-%d %H:%M:%S')


class Worker(metaclass=ABCMeta):

    """Generic worker class, contains a job queue and handle incoming jobs for execution, should be
    mixed int with Thread or Process class"""

    def __init__(self, job_queue, completed_jobs, name=u'', debug=False, *args, **kwargs):
        # Process name, defaulted to a uuid
        self._name = name or uuid.uuid4()
        # A joinable job queue
        self._job_queue = job_queue
        # Completed jobs queue
        self._completed_jobs = completed_jobs
        # Debug flag
        self._debug = debug
        # Last job done flag
        self._done = False
        # Track last job executed
        self._last_job = None
        # Logging settings
        self._log = logging.getLogger(f'{__name__}.{self._name}')
        sh = logging.StreamHandler()
        sh.setFormatter(_fmt)
        if self._debug is True:
            sh.setLevel(logging.DEBUG)
            self._log.setLevel(logging.DEBUG)
            self._log.addHandler(sh)
        else:
            sh.setLevel(logging.INFO)
            self._log.setLevel(logging.INFO)
            self._log.addHandler(sh)
        # Handle exit gracefully
        signal.signal(signal.SIGINT, self.exit)
        signal.signal(signal.SIGTERM, self.exit)
        super().__init__(*args, **kwargs)

    @property
    def name(self):
        return self._name

    @property
    def results(self):
        return self._completed_jobs

    @property
    def done(self):
        return self._done

    @abstractmethod
    def run(self):
        pass

    def exit(self, sgl, frm):
        """Handle exit signals"""
        if not self._done and self._last_job is not None:
            self._job_queue.put(self._last_job)
            self._log.debug("%s - Re added interrupted job", self.name)
        self._log.debug("%s - Exiting", self.name)
        self._job_queue.put(None)
        sys.exit()


class QueueWorker(Worker):

    """Worker unit based on `multiprocessing.JoinableQueue`, used to pass jobs to workers in a
    producer-consumer like way"""

    def run(self):
        while True:
            # Need to decompress and unpickle data here cause the function contained in the job
            # could be not defined in the __main__ module being the worker optionally run in a
            # remote machine
            self._done = False
            self._last_job = None
            zipped_job = self._job_queue.get()
            # Poison pill check
            if zipped_job is None:
                break
            job = decompress_and_unpickle(zipped_job)
            self._last_job = job
            self._log.debug("%s - executing job %s", self.name, job.job_id)
            if 'eta' in job.kwargs:
                eta = job.kwargs.pop('eta')
                multiples = {'h': 60 * 60, 'm': 60, 's': 1}
                if isinstance(eta, int):
                    delay = eta
                else:
                    try:
                        delay = int(eta)
                    except ValueError:
                        delay = multiples[eta[-1]] * int(eta[:-1])
                job.add_delay(delay)
                response = job.execute()
                self._job_queue.task_done()
                self._log.debug(
                    "%s - Job %s succesfully executed in %s s",
                    self.name,
                    job.job_id,
                    job.execution_time()
                )
                self._log.debug(
                    '%s - Timed job %s result = %s',
                    self.name,
                    job.job_id,
                    response.value
                )
                # Push the completed job in the result queue ready to be answered to the requesting
                # client
                self._completed_jobs.put(response)
                # Re enter the job in the queue
                job.kwargs['eta'] = str(job.delay) + 's'
                self._job_queue.put(pickle_and_compress(job))
            else:
                response = job.execute()
                self._job_queue.task_done()
                self._log.debug(
                    "%s - Job %s succesfully executed in %s s",
                    self.name,
                    job.job_id,
                    job.execution_time()
                )
                # Push the completed job in the result queue ready to be answered to the requesting
                # client
                self._completed_jobs.put(response)
            self._done = True


class ThreadQueueWorker(QueueWorker, Thread):

    """Worker unit based on `threading.Thread` superclass, useful if the majority of the jobs are
    I/O bound"""

    pass


class ProcessQueueWorker(QueueWorker, Process):

    """Worker unit based on `multiprocessing.Process` superclass, meant to be employed in case the
    majority of the jobs are CPU bound"""

    pass
