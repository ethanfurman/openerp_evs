"""
support for short-running scripts
"""
from __future__ import print_function

__all__ = [
        'Notify', 'send_mail', 'grouped', 'grouped_by_column',
        'zip_longest',
        'NOW', 'TOMORROW',
        ]

try:
    from itertools import izip_longest as zip_longest
except ImportError:
    from itertools import zip_longest

from antipathy import Path
from datetime import timedelta
from dbf import DateTime
from random import choice
from scription import Exit, error, Job, OrmFile, print
from time import mktime

# notification support

NOW = DateTime.now()
TOMORROW = NOW.replace(delta_day=+1).date()
MINUTE = timedelta(seconds=60)

BASE = Path('/home/openerp/sandbox')
SCHEDULE = BASE / 'etc/notify.ini'
NOTIFIED = BASE / 'var/notified.'

SCRIPT_NAME = None

class Notify(object):
    """
    notifies users via email/text message when a script signals failure
    """

    def __init__(self, name, schedule=None, notified=None, cut_off=0, grace=13, stable=5, renotify=67):
        # name: name of script (used for notified file name)
        # schedule: file that holds user name, email, text, and availability
        # notified: file that rememebers who has been notified
        # grace: how long to wait for error to clear before notifying
        # stable: how long before an error-free condition clears the last error
        # renotify: how long before re-reporting an error
        # cut_off: how long to wait, in minutes, before resending errors or
        #          sending all clear
        self.name = name
        self.schedule = Path(schedule or SCHEDULE)
        notified = Path(notified or NOTIFIED)
        if notified == NOTIFIED:
            notified += name
        self.notified = notified
        settings = OrmFile(self.schedule)
        if name not in settings.available:
            raise ValueError("%r not in '%s'" % (name, SCHEDULE))
        if cut_off:
            cut_off = timedelta(seconds=cut_off * 60)
            self.grace_period = 0
            self.stablized = cut_off
            self.renotify = cut_off
        else:
            section = settings.available[name]
            self.grace_period = section.grace * MINUTE
            self.stablized = section.stable * MINUTE
            self.renotify = section.renotify * MINUTE

    def __call__(self, errors):
        """
        send errors to valid recipients or cancellation to all notified thus far

        notification file format
        ---
        time contacted      address
        2020-05-20 03:47    ethan@stoneleaf.us
        ---
        """
        # script_name: name of calling script (used in subject line)
        # schedule: file with schedules of whom to contact and when
        # notified: file with contacted details
        # errors: list of errors (will become the message body)
        # grace: how long to wait for error to clear before notifying
        # stable: how long before an error-free condition clears the last error
        # renotify: how long before re-reporting an error
        if not errors:
            if not self.notified.exists():
                return Exit.Success
            last_accessed = self.notified.stat().st_atime
            if NOW - DateTime.fromtimestamp(last_accessed) < self.stablized:
                # too soon to notify, maybe next time
                return Exit.Success
            # get names from file and notify each one that problem is resolved
            addresses = self.get_notified()
            subject = "%s: all good" % self.name
            message = "problem has been resolved"
        else:
            # errors happened; check if notified needs (re)creating
            create_error_file = False
            renotify = False
            print(NOW)
            if not self.notified.exists():
                create_error_file = True
            else:
                last_accessed = self.notified.stat().st_atime
                print(DateTime.fromtimestamp(last_accessed))
                if self.renotify and NOW - DateTime.fromtimestamp(last_accessed) > self.renotify:
                    # time to remind
                    renotify = True
            print('create is', create_error_file)
            print('renotify is', renotify)
            print('self.grace_period is', self.grace_period)
            if create_error_file or renotify:
                print('creating file')
                with open(self.notified, 'w') as fh:
                    fh.write("time contacted      address\n")
            if self.grace_period and not renotify:
                print('checking grace period')
                last_accessed = DateTime.fromtimestamp(self.notified.stat().st_atime)
                if NOW - last_accessed < self.grace_period:
                    print('inside grace period, exiting')
                    return Exit.UnknownError
            self.notified.touch((time_stamp(NOW), None))
            all_addresses = self.get_recipients()
            addresses = self.filter_recipients(all_addresses)
            subject = "%s: errors encountered" % self.name
            message = ''.join(errors)
        sent_addresses, failed_to_send = send_mail(addresses, subject, message)
        if failed_to_send:
            error('\n\nUnable to contact:\n  %s' % ('\n  '.join(failed_to_send)))
        if errors:
            self.update_recipients(sent_addresses)
        else:
            self.notified.unlink()
        if errors or failed_to_send:
            return Exit.UnknownError
        else:
            return Exit.Success

    def filter_recipients(self, addresses):
        """
        return recipients that have not been contacted for current situation
        """
        with open(self.notified) as fh:
            lines = [line for line in fh.read().strip().split('\n') if line]
        if lines and lines[0].startswith('time'):
            lines.pop(0)
        contacted = []
        for line in lines:
            date, time, address = line.split()
            contacted.append(address)
        return [a for a in addresses if a not in contacted]

    def get_notified(self):
        """
        return address that have been notified

        file format is
        ---
        time contacted      address
        2020-05-20 03:47    ethan@stoneleaf.us
        ---
        """
        addresses = []
        with open(self.notified) as fh:
            data = fh.read().strip()
        if data:
            addresses = [line.split()[2] for line in data.split('\n')]
        return addresses

    def get_recipients(self):
        """
        read address file and return eligible recipients based on allowed times

        recipient file format is scription's OrmFile
        ---
        users = ['ethan', 'emile']
        email = None
        text = None

        [ethan]
        email = ['ethan@stoneleaf.us', ]
        text =  ['9715061961@vtext.com', ]

        [emile]
        email = ['emile@gmail.com', ]
        text = ['6503433458@tmomail.net', ]

        [available]
        ethan = ('Mo-Fr:600-1900', 'Su:1700-2100')
        emile = True
        tony = True
        ron = True

        [available.process_openerp_orders]
        ---
        """
        addresses = []
        settings = OrmFile(self.schedule)
        if self.name not in settings.available:
            raise ValueError('%r not in %s' % (self.name, SCHEDULE))
        section = settings.available[self.name]
        for user in settings.users:
            email = settings[user].email
            text = settings[user].text
            times = section[user]
            if times is True:
                available = WeeklyAvailability.always()
            elif times:
                available = WeeklyAvailability(*section[user])
            else:
                available = WeeklyAvailability.none()
            if email:
                addresses.extend(email)
            if text and NOW in available:
                addresses.extend(text)
        return addresses


    def update_recipients(self, addresses):
        """
        update notification file with who was contacted at what time
        """
        with open(self.notified, 'a') as fh:
            for address in addresses:
                fh.write('%-20s %s\n' % (NOW.strftime('%Y-%m-%d %H:%M'), address))


def send_mail(recipients, subject, message):
    """
    use system mail command to send MESSAGE to RECIPIENTS
    """
    sent_addresses = []
    failed_to_send = []
    for address in recipients:
        # may be skipped if all eligible addresses have already been notified
        try:
            job = Job(
                    '/usr/bin/mail -# -s "%s" %s' % (subject, address),
                    pty=True,
                    )
            job.communicate(input=message+'\n\x04', timeout=300)
            sent_addresses.append(address)
        except Exception as exc:
            error(exc)
            failed_to_send.append(address)
            continue
    return sent_addresses, failed_to_send

class WeeklyAvailability(object):
    """
    maintain periods of availability on a weekly basis
    """
    def __init__(self, *times):
        # times -> ['Mo:800-1700', 'Tu,Th:1100-1330,1730-2000', 'We:-', 'Fr', 'Sa-Su:1400-2100']
        self.text = str(times)
        matrix = {
                'mo': [0] * 1440,
                'tu': [0] * 1440,
                'we': [0] * 1440,
                'th': [0] * 1440,
                'fr': [0] * 1440,
                'sa': [0] * 1440,
                'su': [0] * 1440,
                }
        for period in times:
            period = period.lower()
            if ':' not in period:
                period += ':'
            days, minutes = period.split(':')
            if minutes == '-':
                minutes = None
            elif minutes == '':
                minutes = ['0-2359']
            else:
                minutes = minutes.split(',')
            for day in which_days(days):
                day = matrix[day]
                if minutes is None:
                    continue
                for sub_period in minutes:
                    start, end = sub_period.split('-')
                    start = int(start[:-2] or 0) * 60 + int(start[-2:])
                    end = int(end[:-2] or 0) * 60 + int(end[-2:])
                    for minute in range(start, end+1):
                        day[minute] = 1
        self.days = [matrix['su'], matrix['mo'], matrix['tu'], matrix['we'], matrix['th'], matrix['fr'], matrix['sa'], matrix['su']]

    def __repr__(self):
        return "WeeklyAvailability(%r)" % self.text

    def __contains__(self, dt):
        """
        checks if dt.day, hour, minute is 1
        """
        day = dt.isoweekday()
        moment = dt.hour * 60 + dt.minute
        return self.days[day][moment] == 1

    @classmethod
    def always(cls):
        """
        create a WeeklyAvailability with all availability
        """
        return cls('su-sa')

    @classmethod
    def none(cls):
        """
        create a WeeklyAvailability with no availability
        """
        return cls('su-sa:-')


def which_days(text):
    """
    return days from text string
    i.e. 'mo-we' -> ['mo','tu','we']
    """
    week = ['su','mo','tu','we','th','fr','sa']
    text = text.lower()
    groups = text.split(',')
    days = []
    for group in groups:
        if '-' not in group:
            days.append(group)
        else:
            start, stop = group.split('-')
            if start not in week:
                raise ValueError('invalid start day: %r' % (start, ))
            if stop not in week:
                raise ValueError('invalid stop day: %r' % (stop, ))
            start = week.index(start)
            while True:
                days.append(week[start])
                if week[start] == stop:
                    break
                start = (start + 1) % 7
    return days

def time_stamp(dt):
    "return POSIX timestamp as float"
    return mktime((dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, -1, -1, -1)) + dt.microsecond / 1e6

# general

def grouped(it, size):
    'yield chunks of it in groups of size'
    if size < 1:
        raise ValueError('size must be greater than 0 (not %r)' % size)
    result = []
    count = 0
    for ele in it:
        result.append(ele)
        count += 1
        if count == size:
            yield tuple(result)
            count = 0
            result = []
    if result:
        yield tuple(result)

def grouped_by_column(it, size):
    'yield chunks of it in groups of size columns'
    if size < 1:
        raise ValueError('size must be greater than 0 (not %r)' % size)
    elements = list(it)
    iters = []
    rows, remainder = divmod(len(elements), size)
    if remainder:
        rows += 1
    for column in grouped(elements, rows):
        iters.append(column)
    while len(iters) < size:
        iters.append(iter(' '))
    return zip_longest(*iters, fillvalue='')

def all_equal(iterator, test=None):
    '''if `test is None` do a straight equality test'''
    it = iter(iterator)
    if test is None:
        try:
            target = next(it)
            test = lambda x: x == target
        except StopIteration:
            return True
    for item in it:
        if not test(item):
            return False
    return True

class Sentinel(object):
    "provides better help for sentinels"
    #
    def __init__(self, text, boolean=True):
        self.text = text
        self.boolean = boolean
    #
    def __repr__(self):
        return "%s(%r, bool=%r)" % (self.text, self.boolean)
    #
    def __str__(self):
        return '<%s>' % self.text
    #
    def __bool__(self):
        return self.boolean
    __nonzero__ = __bool__

_trans_sentinel = Sentinel('no strip argument')
def translator(frm=u'', to=u'', delete=u'', keep=u'', strip=_trans_sentinel, compress=False):
    """
    modify string by transformation and/or deletion of characters
    """
    # delete and keep are mutually exclusive
    if delete and keep:
        raise ValueError('cannot specify both keep and delete')
    replacement = replacement_ord = None
    if len(to) == 1:
        if frm == u'':
            replacement = to
            replacement_ord = ord(to)
            to = u''
        else:
            to = to * len(frm)
    if len(to) != len(frm):
        raise ValueError('frm and to should be equal lengths (or to should be a single character)')
    uni_table = dict(
            (ord(f), ord(t))
            for f, t in zip(frm, to)
            )
    for ch in delete:
        uni_table[ord(ch)] = None
    def translate(s):
        if isinstance(s, bytes):
            s = s.decode('latin1')
        if keep:
            for chr in set(s) - set(keep):
                uni_table[ord(chr)] = replacement_ord
        s = s.translate(uni_table)
        if strip is not _trans_sentinel:
            s = s.strip(strip)
        if replacement and compress:
            s = replacement.join([p for p in s.split(replacement) if p])
        return s
    return translate

def hrtd(td):
    "human readable time delta"
    seconds = td.total_seconds()
    days, seconds = divmod(seconds, 60*60*24)
    hours, seconds = divmod(seconds, 60*60)
    minutes, seconds = divmod(seconds, 60)
    if seconds:
        minutes += 1
    res = []
    if days:
        res.append('%d days' % days)
    if hours:
        res.append('%d hours' % hours)
    if minutes:
        if minutes != 1:
            res.append('%d minutes' % minutes)
        else:
            res.append('%d minute' % minutes)
    return ', '.join(res)

class xrange(object):
    '''
    accepts arbitrary objects to use to produce sequences
    '''

    def __init__(self, start, stop=None, step=None, count=None, epsilon=None):
        if stop is not None and count is not None:
            raise ValueError("cannot specify both stop and count")
        if stop is None and count is None:
            # check for default start based on type
            start, stop = None, start
            try:
                start = type(stop)(0)
            except Exception:
                raise ValueError("start must be specified for type %r" %
                        type(stop))
        if start is None:
            ref = type(stop)
        else:
            ref = type(start)
        if step is None:
            try:
                step = ref(1)
            except TypeError:
                raise ValueError("step must be specified for type %r" %
                        type(stop))
        if epsilon is None:
            try:
                epsilon = .5 * step
                if not isinstance(epsilon, ref):
                    raise TypeError
            except TypeError:
                pass
        self.start = self.init_start = start
        self.stop = stop
        self.step = step
        self.count = self.init_count = count
        self.epsilon = epsilon

    def __contains__(self, value):
        start, stop, step = self.start, self.stop, self.step
        count = self.count
        if callable(step):
            raise TypeError(
                    "range with step %r does not support containment checks" %
                    step)
        try:
            value % step
        except TypeError:
            raise TypeError(
                    "range of %s with step %s does not support "
                    "containment checks" % (type(start), type(step)))
        if stop is None:
            stop = start + (step * count)
        if stop == start:
            return False
        if start < stop and not start <= value < stop:
            return False
        elif stop < start and not stop >= value > stop:
            return False
        try:
            distance = value - start
            return distance % step == 0.0
        except TypeError:
            raise TypeError(
                    "range of %s with step %s does not support "
                    "containment checks" % (type(start), type(step)))

    def __iter__(self):
        start = self.start
        stop = self.stop
        step = self.step
        count = self.count
        epsilon = self.epsilon
        if stop is not None and epsilon:
            stop -= epsilon
        value = None
        i = -1
        while 'more values to yield':
            i += 1
            if callable(step):
                if i:
                    value = step(start, i, value)
                else:
                    value = start
            else:
                value = start + i * step
            if count is not None:
                if count < 1:
                    break
                count -= 1
            else:
                if stop > start and value >= stop:
                    break
                if stop < start and value <= stop:
                    break
            yield value

    def __repr__(self):
        values = [
                '%s=%r' % (k,v)
                for k,v in (
                    ('start',self.start),
                    ('stop',self.stop),
                    ('step', self.step),
                    ('count', self.count),
                    ('epsilon', self.epsilon),
                    )
                if v is not None
                ]
        return '%s(%s)' % (self.__class__.__name__, ', '.join(values))

def generate_passphrase(words=[]):
    """
    xkcd password generator
    http://xkcd.com/936/
    """
    if not words:
        with open('/usr/share/dict/words') as fh:
            for line in fh:
                word = line.strip()
                if word.isalpha() and word.islower() and 4 <= len(word) <= 9:
                    words.append(word)
    pass_phrase = []
    while len(pass_phrase) < 4:
        word = choice(words)
        if word in pass_phrase:
            continue
        pass_phrase.append(word)
    return ' '.join(pass_phrase)


