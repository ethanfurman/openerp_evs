#!/usr/bin/env python2.7

# imports
from __future__ import print_function

from antipathy import Path
from antipathy.path import uPath
from dbf import Date, DateTime
from fnx_script_support import send_mail
from itertools import groupby
from pandaemonium import PidLockFile, LockError, AlreadyLocked, logger as pandaemonium_logger
from scription import *
from signal import SIGKILL
from socket import getfqdn
from syslog import openlog, syslog, closelog, LOG_PID, LOG_INFO, LOG_CRON, LOG_ERR
from textwrap import wrap
from time import time
from urllib import urlopen
import codecs
import datetime
import errno
import gzip
import logging
import logging.handlers
import os
import re
import traceback
import sys

SCRIPTION_ERROR_EXIT_OKAY = False
TODAY = Date.today()
NOW = DateTime.now()

# load configuration file
try:
    execfile('/etc/cronaide.conf')
except:
    MESSAGE_FROM = "cronaide <noreply@%s>" % (getfqdn() or 'nowhere.invalid')
    MESSAGE_TO = ['root@localhost']
    PULSE_SERVER = None

# and helpers

time_units = {'d':86400, 'h':3600, 'm':60, 's':1}

def time2seconds(time):
    "convert time to seconds (e.g. 2m -> 120)"
    # if all digits, must be seconds already
    if not time:
        return 0
    elif isinstance(time, (int, long)):
        return time
    text = time
    if text[0] == '-':
        sign = -1
        text = text[1:]
    else:
        sign = +1
    if text.isdigit():
        return sign * int(text)
    wait_time = 0
    digits = []
    for c in text:
        if c.isdigit():
            digits.append(c)
            continue
        number = int(''.join(digits))
        c = c.lower()
        if c not in ('dhms'):
            abort('invalid wait time: %r' % time)
        wait_time += time_units[c] * number
        digits = []
    else:
        if digits:
            # didn't specify a unit, abort
            abort('missing trailing time unit of h, m, or s in %r' % time)
    return wait_time
TimeLapse = time2seconds

def seconds2time(seconds):
    if seconds < 0:
        raise ValueError('seconds cannot be negative')
    result = ''
    for unit in 'dhms':
        size = time_units[unit]
        if seconds < size:
            continue
        amount, seconds = divmod(seconds, size)
        result = ('%s %i%s' % (result, amount, unit)).strip()
        if seconds == 0:
            break
    return result or '0s'

# API

@Script(
        log=Spec('logging level to use', OPTION, abbrev=None, force_default='INFO', type=u.upper),
        log_file=Spec('file to log to', OPTION, abbrev=None, force_default='/var/log/cronaide.log'),
        result_dir=Spec('directory to save job output to', OPTION, abbrev=None, force_default='/var/log/cronaide', type=Path),
        result_count=Spec('how many days to keep files', OPTION, abbrev=None, force_default=15),
        )
def main(log, log_file, result_dir, result_count):
    global logger, main_logger, out_dir, out_count
    out_dir = result_dir
    out_count = result_count
    if log and os.getuid() == 0:
        log_level = getattr(logging, log)
        main_logger = logging.getLogger()
        main_logger.setLevel(log_level)
        handler = logging.handlers.TimedRotatingFileHandler(log_file, when='W0', backupCount=52)
        handler.setFormatter(Formatter('%(asctime)s %(pid)d %(name)s %(levelname)s: %(message)s'))
        main_logger.addHandler(handler)
        logger = logging.getLogger('cronaide')
        logger.setLevel(log_level)
        pandaemonium_logger.setLevel(log_level)
    else:
        logger = lambda *a, **kw: 0
        logger.info = lambda *a, **kw: 0
        logger.debug = lambda *a, **kw: 0
        logger.warning = lambda *a, **kw: 0
        logger.error = lambda *a, **kw: 0


@Command(
        email=('Who to send a test mail to.',),
        )
def test_mail(email):
    "send a test message to EMAIL via SERVER"
    send_mail(email, 'test mail', 'hope you got this! :)')


@Alias(
        'cronaide'
        )
@Command(
        email=Spec('Who to send job output to.', MULTI, force_default=MESSAGE_TO),
        email_subject=Spec('subject to use for email (if any)', OPTION, ('subject', 's')),
        job_timeout=Spec('allowed time for job', OPTION, abbrev=('jt','timeout'), type=time2seconds, force_default='60s'),
        retry_job=Spec('number of times to retry job on failure', OPTION, type=int),
        capture=('Send all output to EMAIL', FLAG, ),
        heartbeat=('Send notification of successful job run.', FLAG, ),
        passthrough=('Send STDOUT and STDERR streams to cron.', FLAG, ),
        check_stdout=Spec('check for "ERROR" in stdout stream', FLAG, abbrev=None),
        fail_on_stderr=Spec('report error if any output on stderr stream', FLAG, abbrev=None),
        lock_action=Spec(
            'do LOCK-ACTION if previous job still running',
            OPTION, abbrev='la', choices=['wait', 'kill'],
            ),
        lock=Spec(
            'use LOCK to prevent multiple concurrent runs',
            OPTION, abbrev='lf', force_default='/var/run/${job-name}.pid',
            ),
        lock_timeout=Spec(
            'LOCK-ACTION=wait -> amount of time to wait for previous job to finish\n'
            'LOCK-ACTION=kill -> immediately kill previous job if it has been running for LOCK-TIMEOUT',
            OPTION, abbrev='lt', force_default=-1, type=time2seconds,
            ),
        virtualenv=Spec('activate a virtual environment', OPTION, None, type=uPath),
        pulse=Spec('pulse to send on successful completion of job', OPTION, None),
        pulse_error=Spec('pulse to send on job failure', OPTION, None),
        job=('job to run', REQUIRED, ),
        )
def watch(
        email, email_subject, job_timeout, retry_job,
        capture, heartbeat, passthrough, check_stdout, fail_on_stderr,
        lock, lock_timeout, lock_action,
        virtualenv, pulse, pulse_error, *job
    ):
    """monitor JOB, sending results/status to EMAIL

    By default, a locking pid file is used with an immediate timeout, and
    the default action if the previous job is still running is to quit.
    (The default lock filename is the name of the first command.)

    If the LOCK-* options are not specified, or LOCK_ACTION is kill, LOCK-TIMEOUT is
    how long JOB is allowed to run before killing it;
    """
    global SCRIPTION_ERROR_EXIT_OKAY
    if not email and (capture or heartbeat):
        abort('if CAPTURE or HEARTBEAT is specified then EMAIL must also be specified')
    if pulse:
        if not pulse.startswith('http://'):
            if PULSE_SERVER is None:
                abort('global PULSE_SERVER not set and command-line PULSE is incomplete')
            pulse = PULSE_SERVER + pulse
    if pulse_error:
        if not pulse_error.startswith('http://'):
            if PULSE_SERVER is None:
                abort('global PULSE_SERVER not set and command-line PULSE_ERROR is incomplete')
            pulse_error = PULSE_SERVER + pulse_error
    job_env = os.environ.copy()
    if virtualenv:
        print('setting virtualenv')
        if not virtualenv.exists('bin/activate'):
            abort('cannot find a virtualenv at %s' % (virtualenv, ))
        if job_env.get('VIRTUAL_ENV'):
            # virtualenv currently active -- remove it from child jobs
            old_virtualenv = job_env['VIRTUAL_ENV']
            old_virtualenv_path = old_virtualenv + '/bin'
            path = job_env.get('PATH', '').split(':')
            if old_virtualenv_path in path:
                path.remove(old_virtualenv_path)
            job_env['PATH'] = ':'.join(path)
        virtualenv_path = job_env.get('PATH', '').split(':')
        virtualenv_path.insert(0, virtualenv+'/bin')
        job_env['VIRTUAL_ENV'] = virtualenv
        job_env['PATH'] = ':'.join(virtualenv_path)
        if 'PYTHONHOME' in job_env:
            del job_env['PYTHONHOME']
    if lock or lock_action:
        print('getting lock name')
        logger.debug('lock_action: %s', lock_action)
        if lock is None:
            lock = script_commands[script_command_name].__scription__['lock']._script_default
        lock = lock.replace('${job-name}', Path(job[0]).filename)
        logger.debug('lock: %s', lock)
    ppid = os.getppid()
    openlog('AIDE', LOG_PID, LOG_CRON)
    try:
        pid_lock = None
        result = None
        while "trying to run job":
            print('preparing to run job')
            failed = False
            start = time()
            syslog(LOG_INFO, '[from %s] start: %s' % (ppid, ' '.join(sys.argv)))
            if lock:
                print('getting lock... ', end='')
                try:
                    pid_lock = get_lock(lock_action, lock, lock_timeout)
                    print('succeeded')
                except AlreadyLocked:
                    # previous job still running, user does not want to kill it
                    print('previous job still running')
                    syslog(LOG_INFO, '[from %s] skipped; previous job still running, quiting' % (ppid, ))
                    sys.exit(Exit.Success)
                except LockError:
                    # unable to kill previous job / unable to create file / other misc error
                    print('failed')
                    cls, exc, tb = sys.exc_info()
                    logger.error('%s; aborting', exc.args[0])
                    message = exc.args[0]
                    if email:
                        subject = email_subject or job[0].split('/')[-1]
                        send_mail(email, subject, message)
                        syslog(LOG_INFO, '[from %s] skipped; mail sent <%r>' % (ppid, exc.args[0]))
                    else:
                        syslog(LOG_INFO, '[from %s] skipped <%r>' % (ppid, exc.args[0]))
                        error(exc.args[0])
                    if passthrough:
                        echo(message)
                    sys.exit(Exit.CantCreate)
                else:
                    logger.debug('lock obtained')
            print('running job:', job)
            logger.info('running job: %s', ' '.join(job))
            result = Job(job, env=job_env)
            if pid_lock:
                print('sealing lock')
                pid_lock.seal(result.pid)
                logger.debug('lock sealed for PID: %d', result.pid)
            try:
                failed = True  # reset if job completes successfully
                print('communicating with job')
                result.communicate(timeout=job_timeout)
                print(result.stdout, verbose=2)
                print(result.stderr, verbose=2)
                failed = False
            except TimeoutError:
                print('job timed out')
                pass
            except ExecuteError as exc:
                abort(exc)
            finally:
                print('closing job')
                result.close()
                if pid_lock and pid_lock.is_active():
                    print('releasing lock')
                    pid_lock.release()
            stop = time()
            elapsed = stop - start
            message = None
            body = [
                    'job:  %s' % ' '.join(job),
                    'pid:  %s' % result.pid,
                    'return code: %s' % result.returncode,
                    'time to run: approximately %s' % seconds2time(elapsed),
                    ]
            output = result.stdout + result.stderr
            if (output or result.returncode) and out_dir:
                save_result_to_file(result, body)
            if (check_stdout and ('ERROR' in output or 'Traceback' in output)) or result.returncode or (fail_on_stderr and result.stderr):
                failed = True
                if result.stdout:
                    body.append('stdout\n======\n%s' % result.stdout)
                if result.stderr:
                    body.append('stderr\n======\n%s' % result.stderr)
                if email:
                    print('error encountered, sending mail')
                    subject = 'job failed: %s' % (email_subject or job[0].split('/')[-1])
                    message = '\n\n'.join(body)
                    send_mail(email, subject, message)
                    syslog(LOG_ERR, '[from %s] failed; mail sent' % ppid)
                else:
                    syslog(LOG_ERR, '[from %s] failed' % ppid)
                    print('\n\n'.join(body), file=stderr)
            else:
                # options | stdout | email result
                #    H    |   yes  | heartbeat
                #    H    |   no   | heartbeat
                #    HC   |   no   | heartbeat
                #    HC   |   yes  | capture
                #    C    |   yes  | capture
                #    C    |   no   | none
                if heartbeat and not (capture and output):
                    print('sending heartbeat')
                    subject = 'job heartbeat: %s' % (email_subject or job[0].split('/')[-1])
                    message = '\n\n'.join(body)
                    send_mail(email, subject, message)
                if capture and output:
                    if result.stdout:
                        body.append('stdout\n======\n%s' % result.stdout)
                    if result.stderr:
                        body.append('stderr\n======\n%s' % result.stderr)
                    print('catch and release')
                    subject = 'job: %s' % (email_subject or job[0].split('/')[-1])
                    message = '\n\n'.join(body)
                    send_mail(email, subject, message)
                if message:
                    syslog(LOG_INFO, '[from %s] succeeded; mail sent' % ppid)
                else:
                    syslog(LOG_INFO, '[from %s] succeeded' % ppid)
            if failed:
                if retry_job:
                    logger.debug('job failed, retrying (attempts left: %d)', retry_job)
                    retry_job -= 1
                else:
                    logger.debug('job failed with %s, exiting', result.returncode or Exit.Unknown)
                    break
            else:
                logger.info('job finished')
                break
    finally:
        if pid_lock and pid_lock.is_active():
            pid_lock.release()
        if result and (passthrough and script_verbosity < 2) or (failed and not email):
            print('relaying job output')
            if result.stdout:
                echo(result.stdout, verbose=0, end='')
            if result.stderr:
                echo(result.stderr, end='')
        closelog()
    if not failed and pulse:
        try:
            urlopen(pulse).close()
        except IOError:
            pass
    else:
        if result.returncode == Exit.ScriptionError:
            SCRIPTION_ERROR_EXIT_OKAY = True
        sys.exit(result.returncode or Exit.Unknown)
    sys.exit(result.returncode)


@Command(
        )
def check_cron():
    "search crontab for cronaide entries that have been commented out"
    openlog('AIDE', LOG_PID, LOG_CRON)
    ppid = os.getppid()
    syslog(LOG_INFO, '[from %s] start: %s' % (ppid, ' '.join(sys.argv)))
    msg = None
    with open('/etc/crontab') as crontab:
        warnings = []
        for line in crontab:
            if line.startswith('#:cron_check:'):
                _, email, prog = line.rsplit(':', 2)
                cron_check = next(crontab)
                if cron_check[0] == '#':
                    if email.strip():
                        addresses = email.split(',')
                    else:
                        addresses = MESSAGE_TO
                    for addr in addresses:
                        warnings.append((addr, prog))
        warnings.sort()
        for email, progs in groupby(warnings, key=lambda ep: ep[0]):
            prog_names = []
            for _, prog in progs:
                prog_names.append(prog)
            subject = 'disabled cron jobs'
            message = '\n%s' % '\n\t'.join(prog_names)
            send_mail(email, subject, message)
            syslog(LOG_INFO, '[from %s] succeeded; mail sent' % ppid)
    if msg is None:
        syslog(LOG_INFO, '[from %s] succeeded' % ppid)
    closelog()


@Command(
        )
def check_syslog():
    """
    search crontab and syslog and verify that crontab entries of cronaide are running properly

    M: unable to find job in syslog
    E: job timed out and was killed
    S: job ran successfully
    F: job ran and failed
    """
    crontab = read('/etc/crontab')
    cron_jobs = {}
    commands = []
    for line in crontab.split('\n'):
        cronjob, command = is_cronaide(line)
        if command:
            commands.append(command)
            cron_jobs[command] = cronjob
            print('appended:', command, verbose=2)
    syslogs = Path('/var/log').glob('syslog*')
    syslogs.sort()
    aide = {}
    done = set()
    try:
        for sl in syslogs:
            print('checking %s...' % sl)
            sl = read(sl)
            sl = sl.split('\n')
            sl.reverse()
            for line in sl:
                match = re.search('AIDE\[(\d*)\]: \[from (\d*)\] (.*)', line)
                if match:
                    pid, ppid, msg = match.groups()
                    entry = aide.setdefault(ppid, {})
                    entry['child'] = pid
                    if msg.startswith('start'):
                        entry['start'] = msg
                    else:
                        entry['stop'] = msg
                    continue
                match = re.search('CRON\[(\d*)\]: \(\w*\) CMD \((.*)\)', line)
                if match:
                    ppid, msg = match.groups()
                    if msg.split()[0].endswith('cronaide'):
                        entry = aide.setdefault(ppid, {})
                        entry['command'] = command = ' '.join(msg.split())
                        try:
                            if command in done or command not in cron_jobs:
                                # ignore old versions and duplicates
                                del aide[ppid]
                            else:
                                print('removing:', command)
                                done.add(command)
                                commands.remove(entry['command'])
                        except ValueError:
                            print('msg:', msg, '\ndone:', done, '\nentry:', entry, '\ncommand list:', commands, file=stderr)
                            raise
                        if not commands:
                            raise BreakLoop
    except BreakLoop:
        pass
    succeeded = []
    failed = []
    errored = []
    missing = []
    if commands:
        for cmd in commands:
            missing.append(cmd)
    for ppid, entry in aide.items():
        if 'command' not in entry:
            # currently running, ignore
            continue
        cronjob = cron_jobs[entry['command']]
        if 'stop' in entry:
            msg = entry['stop']
            if msg.startswith('failed'):
                failed.append(cronjob)
            elif msg.startswith('succeeded'):
                succeeded.append(cronjob)
            else:
                print('unknown message for:\n  cronjob: %s\n  message: %s' % (cronjob, msg), file=stderr)
        else:
            errored.append(cronjob)
    for job in missing:
        print('\nM: ', '\n    '.join(wrap(job)), verbose=0)
    for job in errored:
        print('\nE: ', '\n    '.join(wrap(job)), verbose=0)
    for job in succeeded:
        print('\nS: ', '\n    '.join(wrap(job)), verbose=0)
    for job in failed:
        print('\nF: ', '\n    '.join(wrap(job)), verbose=0)


@Command(
        )
def purge():
    "remove old watch files, keep RESULT_COUNT days worth"
    oldest = NOW.replace(delta_day=-out_count)
    removed = 0
    for file in ViewProgress(out_dir.glob()):
        print(file.filename, end='', verbose=2)
        if DateTime.fromtimestamp(file.stat().st_mtime) < oldest:
            print('--> removed', verbose=2)
            file.unlink()
            removed += 1
        else:
            print(verbose=2)
    print('%d files removed' % removed)


# supporting routines

def get_lock(lock_action, lock_file, timeout):
    logger.debug('%s: action -> %r   timeout -> %r', lock_file, lock_action, timeout)
    lock = PidLockFile(lock_file)
    if lock_action in (None, 'wait'):
        logger.debug('%s: waiting', lock_file)
        # wait for lock, quit after timeout if unable to get lock
        try:
            lock.acquire(timeout=timeout)
        except AlreadyLocked:
            if timeout < 0:
                timeout = 0
            raise AlreadyLocked(
                    'previous job still running after timeout:\npid: %s\nlocked: %s\ntimeout: %s'
                    % (lock.last_read_pid, lock.last_read_timestamp, seconds2time(timeout))
                    )
    elif lock_action == 'kill':
        logger.debug('%s: kill', lock_file)
        try:
            logger.debug('%s: first acquire attempt', lock_file)
            #
            # on failure, lock.last_read_pid and lock.last_read_timestamp will be set
            #
            lock.acquire(timeout=0)
        except AlreadyLocked:
            # check if enough time has passed to warrant killing the process
            logger.debug('%s: first attempt failed', lock_file)
            active_pid = lock.last_read_pid
            start = lock.last_read_timestamp
            if NOW - start <= datetime.timedelta(seconds=timeout):
                logger.debug('%s: previous job within grace period (pid: %s, start: %s, grace period: %s'
                        % (lock_file, active_pid, start.strftime('%Y-%m-%d %H:%M:%S'), seconds2time(timeout))
                        )
                abort('previous job within grace period (pid: %s, start: %s, grace: %s)'
                        % (lock.last_read_pid, lock.last_read_timestamp, seconds2time(timeout))
                        )
            else:
                try:
                    logger.debug('%s: killing job %r', lock_file, active_pid)
                    os.kill(active_pid, SIGKILL)
                except OSError:
                    exc = sys.exc_info()[1]
                    if exc.errno != errno.ESRCH:
                        raise LockError('error killing previous job')
                try:
                    logger.debug('%s: second acquire attempt', lock_file)
                    lock.break_lock()
                    lock.acquire(timeout=1)
                except LockError:
                    exc = sys.exc_info()[1]
                    logger.error('%s: second attempt failed with <%s>', lock_file, exc)
                    raise
    else:
        abort('unknown option for LOCK_ACTION: %r' % lock_action)
    return lock

def read(filename):
    if filename.endswith('.gz'):
        open_file = gzip.open
    else:
        open_file = open
    with open_file(filename) as f:
        return f.read()

def is_cronaide(line):
    "determine if crontab line is calling cronaide"
    if line and line[0] != '#':
        segments = line.split()
        if len(segments) > 6:
            command = segments[6]
            if command.endswith('cronaide'):
                return ' '.join(segments), ' '.join(segments[6:])
    return False, False

class BreakLoop(Exception):
    pass

class Formatter(logging.Formatter):
    def format(self, record):
        record.pid = os.getpid()
        return logging.Formatter.format(self, record)


def save_result_to_file(job, info):
    if not out_dir.exists():
        out_dir.mkdir()
    body = '\n'.join(info)
    if job.stdout:
        body += '\n\nstdout\n======\n%s' % job.stdout
    if job.stderr:
        body += '\n\nstderr\n======\n%s' % job.stderr
    filename = Path(job.name).filename + NOW.strftime('-%Y%m%d-%H%M%S')
    with codecs.open(out_dir/filename, 'w', encoding='utf8') as fh:
        fh.write(body)


try:
    Run()
except:
    cls, exc, tb = sys.exc_info()
    if isinstance(exc, SystemExit):
        exit_code = exc.args[-1]
        if exit_code == Exit.ScriptionError and not SCRIPTION_ERROR_EXIT_OKAY:
            send_mail(
                    MESSAGE_TO,
                    "cronaide error",
                    "error with cronaide parameters:\n\n%s\n\n%s"
                    % (
                        script_module['script_abort_message'],
                        traceback.format_exc(),
                    ))
    else:
        send_mail(MESSAGE_TO, "cronaide: exception raised", traceback.format_exc())
    raise
