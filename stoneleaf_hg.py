import sys
from scription import Execute, echo, error

def update_owner(ui, repo, **kwds):
    '''
    set all files/directories owner to whomever owns the base repo directory
    '''
    url = repo.url()
    if url.startswith('file:'):
        home = url[5:]
        print 'using home of', repr(home)
        job = Execute('/usr/local/bin/hgupdate set-owner-of %s' % home)
        echo(job.stdout, end='')
        error(job.stderr, end='')
        return job.returncode
